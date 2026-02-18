import asyncio
import subprocess
from typing import Dict, Optional, List

from creart import it
from tenacity import retry, stop_after_attempt, wait_fixed

from src.api import WebAPI
from src.config import Config
from src.exceptions import CodecNotFoundException
from src.flags import Flags
from src.grpc.manager import WrapperManager
from src.logger import RipLogger
from src.measurer import Measurer
from src.metadata import SongMetadata
from src.models import PlaylistInfo
from src.mp4 import extract_media, extract_song, encapsulate, write_metadata, fix_encapsulate, fix_esds_box, \
    check_song_integrity
from src.save import save
from src.task import Task, Status
from src.types import Codec, ParentDoneHandler
from src.url import Song, Album, URLType, Playlist
from src.utils import get_codec_from_codec_id, check_song_existence, check_song_exists, if_raw_atmos, \
    check_album_existence, playlist_write_song_index, run_sync, safely_create_task, language_exist, query_language
from src.legacy.mp4 import extract_media as legacy_extract_media
from src.legacy.mp4 import decrypt as legacy_decrypt
from src.legacy.decrypt import WidevineDecrypt


class DownloadManager:
    def __init__(self):
        self.adam_id_task_mapping: Dict[str, Task] = {}
        self.task_lock = asyncio.Semaphore(it(Config).download.maxRunningTasks)

    async def register_task(self, task: Task):
        self.adam_id_task_mapping[task.adamId] = task
        await self.task_lock.acquire()
        it(Measurer).record_task_start()

    async def unregister_task(self, task: Task):
        if task.adamId in self.adam_id_task_mapping:
            del self.adam_id_task_mapping[task.adamId]
            self.task_lock.release()
            it(Measurer).record_task_finish()

    def get_task(self, adam_id: str) -> Optional[Task]:
        return self.adam_id_task_mapping.get(adam_id)


class Ripper:
    def __init__(self):
        self.download_manager = DownloadManager()

    async def rip_song(self, url: Song, codec: str, flags: Flags = Flags(),
                       parent_done: ParentDoneHandler = None, playlist: PlaylistInfo = None):
        if self.download_manager.get_task(url.id):
            if parent_done:
                # If task already exists, we must notify the parent that this "sub-task" is considered handled/skipped
                # to prevent the parent from waiting indefinitely.
                await parent_done.try_done()
            return

        task = Task(adamId=url.id, parentDone=parent_done, playlist=playlist)
        
        # Initialize Logger
        task.logger = RipLogger(URLType.Song, task.adamId)

        try:
            await self.download_manager.register_task(task)
            
            # Fetch Metadata
            raw_metadata = await it(WebAPI).get_song_info(task.adamId, url.storefront, flags.language)
            album_data = await it(WebAPI).get_album_info(raw_metadata.relationships.albums.data[0].id, url.storefront,
                                                         flags.language)
            task.metadata = SongMetadata.parse_from_song_data(raw_metadata)
            task.metadata.parse_from_album_data(album_data)
            
            # Update Logger with metadata
            task.logger.set_fullname(task.metadata.artist, task.metadata.title)
            task.logger.create()
            
            # Check Language
            if it(Config).region.languageNotExistWarning and not language_exist(url.storefront, flags.language):
                default_language, _ = query_language(url.storefront)
                task.logger.language_not_exist(url.storefront, flags.language, default_language)

            # Check Existence on Apple Music
            if not await check_song_existence(url.id, url.storefront):
                task.logger.not_exist()
                task.update_status(Status.FAILED)
                return

            # Get Cover and Lyrics
            task.metadata.cover = await it(WebAPI).get_cover(task.metadata.cover_url, 
                                                             it(Config).download.coverFormat, 
                                                             it(Config).download.coverSize)
            
            if raw_metadata.attributes.hasTimeSyncedLyrics:
                task.metadata.lyrics = await it(WrapperManager).lyrics(task.adamId, flags.language, url.storefront)
            
            if playlist:
                task.metadata.set_playlist_index(playlist.songIdIndexMapping.get(url.id))

            # Check Local Existence
            if not flags.force_save and check_song_exists(task.metadata, codec, playlist):
                task.logger.already_exist()
                task.update_status(Status.DONE)
                return

            # Get M3U8
            m3u8_url = await self._get_m3u8_url(task, codec, raw_metadata)
            if not m3u8_url:
                task.update_status(Status.FAILED)
                return
            
            if codec == Codec.AAC_LEGACY or (it(Config).download.codecAlternative and not raw_metadata.attributes.extendedAssetUrls.enhancedHls and Codec.AAC_LEGACY in it(Config).download.codecPriority):
                 await self._rip_song_legacy(task)
                 return

            try:
                task.m3u8Info = await extract_media(m3u8_url, codec, task)
            except CodecNotFoundException:
                task.logger.audio_not_exist()
                task.update_status(Status.FAILED)
                return

            task.logger.selected_codec(task.m3u8Info.codec_id)
            if all([bool(task.m3u8Info.bit_depth), bool(task.m3u8Info.sample_rate)]):
                task.metadata.set_bit_depth_and_sample_rate(task.m3u8Info.bit_depth, task.m3u8Info.sample_rate)
                # Check existence again with precise metadata
                if not flags.force_save and check_song_exists(task.metadata, codec, playlist):
                    task.logger.already_exist()
                    task.update_status(Status.DONE)
                    return

            # Download
            task.logger.downloading()
            task.update_status(Status.DOWNLOADING)
            raw_song = await it(WebAPI).download_song(task.m3u8Info.uri)

            # Decrypt
            task.logger.decrypting()
            task.update_status(Status.DECRYPTING)
            
            task.info = await run_sync(extract_song, raw_song, get_codec_from_codec_id(task.m3u8Info.codec_id))
            # Initialize futures for each sample
            for i in range(len(task.info.samples)):
                task.decrypted_samples_futures[i] = asyncio.get_running_loop().create_future()

            # Launch decryption for all samples with tenacity
            decryption_tasks = []
            for sampleIndex, sample in enumerate(task.info.samples):
                decryption_tasks.append(
                    self.decrypt_sample_with_retry(task.adamId, task.m3u8Info.keys[sample.descIndex], sample.data, sampleIndex)
                )

            # Wait for all decryption tasks to complete.
            # If any decrypt_sample_with_retry fails (raises exception after retries), we catch it.
            await asyncio.gather(*decryption_tasks)
            
            # Encapsulate and Save
            # Collect results from futures in order
            decrypted_samples = []
            for i in range(len(task.info.samples)):
                # At this point all futures should have result because gather completed successfully
                decrypted_samples.append(task.decrypted_samples_futures[i].result())

            codec = get_codec_from_codec_id(task.m3u8Info.codec_id)

            song = await run_sync(encapsulate, task.info, bytes().join(decrypted_samples),
                                it(Config).download.atmosConventToM4a)
            if not if_raw_atmos(codec, it(Config).download.atmosConventToM4a):
                if codec != Codec.EC3 and codec != Codec.AC3:
                    song = await run_sync(fix_encapsulate, song)
                song = await run_sync(write_metadata, song, task.metadata, it(Config).metadata.embedMetadata,
                                    it(Config).download.coverFormat, task.info.params)
                if codec == Codec.AAC or codec == Codec.AAC_DOWNMIX or codec == Codec.AAC_BINAURAL:
                    song = await run_sync(fix_esds_box, task.info.raw, song)

            if not await run_sync(check_song_integrity, song):
                if it(Config).download.failedSongNotPassIntegrityCheck:
                    task.logger.failed_integrity(True)
                    task.update_status(Status.FAILED)
                    raise Exception("Integrity Check Failed")
                else:
                    task.logger.failed_integrity(False)

            filename = await run_sync(save, song, codec, task.metadata, task.playlist)
            task.logger.saved()
            task.update_status(Status.DONE)

            if it(Config).download.afterDownloaded:
                command = it(Config).download.afterDownloaded.format(filename=filename)
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        except Exception as e:
            task.logger.error(f"Error processing song: {e}")
            task.update_status(Status.FAILED)
        finally:
            await self.download_manager.unregister_task(task)
            task.update_status(task.status) # Ensure status is set
            if task.parentDone:
                await task.parentDone.try_done()
    
    async def _get_m3u8_url(self, task: Task, codec: str, raw_metadata) -> Optional[str]:
        if not raw_metadata.attributes.extendedAssetUrls:
            task.logger.audio_not_exist()
            return None
        
        m3u8_url = None
        if codec == Codec.ALAC and raw_metadata.attributes.extendedAssetUrls.enhancedHls:
            m3u8_url = await it(WrapperManager).m3u8(task.adamId)
        else:
             if codec != Codec.AAC_LEGACY:
                 m3u8_url = raw_metadata.attributes.extendedAssetUrls.enhancedHls
        
        return m3u8_url

    async def _rip_song_legacy(self, task: Task):
        # Simplified legacy ripping integrated into the flow
        try:
            task.m3u8Info = await legacy_extract_media(await it(WrapperManager).webPlayback(task.adamId))

            task.logger.downloading()
            task.update_status(Status.DOWNLOADING)
            raw_song = await it(WebAPI).download_song(task.m3u8Info.uri)
            task.info = await run_sync(extract_song, raw_song, Codec.AAC_LEGACY)

            task.logger.decrypting()
            task.update_status(Status.DECRYPTING)
            wvDecrypt = WidevineDecrypt()
            challenge = wvDecrypt.generate_challenge(task.m3u8Info.keys[0].split(",")[1])
            wvLicense = await it(WrapperManager).license(adam_id=task.adamId, challenge=challenge,
                                                         kid=task.m3u8Info.keys[0])
            keys = wvDecrypt.generate_key(wvLicense)
            song = await run_sync(legacy_decrypt, raw_song, keys[1].kid.hex, keys[1].key.hex())

            song = await run_sync(write_metadata, song, task.metadata, it(Config).metadata.embedMetadata,
                                  it(Config).download.coverFormat, task.info.params)

            if not await run_sync(check_song_integrity, song):
                task.logger.failed_integrity()

            filename = await run_sync(save, song, Codec.AAC_LEGACY, task.metadata, task.playlist)
            task.logger.saved()
            task.update_status(Status.DONE) # Set status explicitly for finally block? 
            # Wait, implementing try...finally in main rip_song handles cleanup.
            # But legacy logic is linear, it doesn't use callbacks for decryption.
            # So we can just run it.

            if it(Config).download.afterDownloaded:
                command = it(Config).download.afterDownloaded.format(filename=filename)
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            task.logger.error(f"Legacy rip failed: {e}")
            task.update_status(Status.FAILED)
            raise e # re-raise to catch in main loop or handle here? Main loop handles it.

    async def rip_album(self, url: Album, codec: str, flags: Flags = Flags(), parent_done: ParentDoneHandler = None):
        album_info = await it(WebAPI).get_album_info(url.id, url.storefront, flags.language)
        logger = RipLogger(url.type, url.id)
        logger.set_fullname(album_info.data[0].attributes.artistName, album_info.data[0].attributes.name)

        logger.create()
        if not await check_album_existence(url.id, url.storefront):
            logger.not_exist()
            return

        async def on_children_done():
            logger.done()
            if parent_done:
                await parent_done.try_done()

        done_handler = ParentDoneHandler(len(album_info.data[0].relationships.tracks.data), on_children_done)

        for track in album_info.data[0].relationships.tracks.data:
            song = Song(id=track.id, storefront=url.storefront, url="", type=URLType.Song)
            safely_create_task(self.rip_song(song, codec, flags, done_handler))

    async def rip_artist(self, url: Album, codec: str, flags: Flags = Flags()):
        artist_info = await it(WebAPI).get_artist_info(url.id, url.storefront, flags.language)
        logger = RipLogger(url.type, url.id)
        logger.set_fullname(artist_info.data[0].attributes.name)

        logger.create()

        async def on_children_done():
            logger.done()

        if flags.include_participate_in_works:
            songs = await it(WebAPI).get_songs_from_artist(url.id, url.storefront, flags.language)
            done_handler = ParentDoneHandler(len(songs), on_children_done)
            for song_url in songs:
                safely_create_task(self.rip_song(Song.parse_url(song_url), codec, flags, done_handler))
        else:
            albums = await it(WebAPI).get_albums_from_artist(url.id, url.storefront, flags.language)
            done_handler = ParentDoneHandler(len(albums), on_children_done)
            for album_url in albums:
                safely_create_task(self.rip_album(Album.parse_url(album_url), codec, flags, done_handler))

    async def rip_playlist(self, url: Playlist, codec: str, flags: Flags = Flags()):
        playlist_info = await it(WebAPI).get_playlist_info_and_tracks(url.id, url.storefront, flags.language)
        playlist_info = playlist_write_song_index(playlist_info)
        logger = RipLogger(url.type, url.id)
        logger.set_fullname(playlist_info.data[0].attributes.curatorName, playlist_info.data[0].attributes.name)

        logger.create()

        async def on_children_done():
            logger.done()

        done_handler = ParentDoneHandler(len(playlist_info.data[0].relationships.tracks.data), on_children_done)

        for track in playlist_info.data[0].relationships.tracks.data:
            song = Song(id=track.id, storefront=url.storefront, url="", type=URLType.Song)
            safely_create_task(self.rip_song(song, codec, flags, done_handler, playlist=playlist_info))




    @retry(stop=stop_after_attempt(3), wait=wait_fixed(1))
    async def decrypt_sample_with_retry(self, adam_id: str, key: str, sample: bytes, sample_index: int):
        task = self.download_manager.get_task(adam_id)
        if not task:
            raise Exception("Task cancelled or not found")
        
        # Reset future if it is already done (e.g. from previous failed attempt)
        if task.decrypted_samples_futures[sample_index].done():
             task.decrypted_samples_futures[sample_index] = asyncio.get_running_loop().create_future()

        future = task.decrypted_samples_futures[sample_index]
        
        # We need to send the command to wrapper manager
        await it(WrapperManager).decrypt(adam_id, key, sample, sample_index)
        
        # Wait for the future to be resolved by the callback
        return await future

    async def on_decrypt_success(self, adam_id: str, key: str, sample: bytes, sample_index: int):
        it(Measurer).record_decrypt(len(sample))
        task = self.download_manager.get_task(adam_id)
        if task and sample_index in task.decrypted_samples_futures:
            if not task.decrypted_samples_futures[sample_index].done():
                task.decrypted_samples_futures[sample_index].set_result(sample)

    async def on_decrypt_failed(self, adam_id: str, key: str, sample: bytes, sample_index: int):
        task = self.download_manager.get_task(adam_id)
        if task and sample_index in task.decrypted_samples_futures:
            if not task.decrypted_samples_futures[sample_index].done():
                task.decrypted_samples_futures[sample_index].set_exception(Exception("Decryption failed callback"))

    # Removed recv_decrypted_sample and on_decrypt_done as they are replaced by linear flow in rip_song


