import asyncio
import subprocess
from typing import Dict

from creart import it

from src.api import WebAPI
from src.config import Config
from src.flags import Flags
from src.grpc.manager import WrapperManager
from src.logger import RipLogger
from src.measurer import SpeedMeasurer
from src.metadata import SongMetadata
from src.models import PlaylistInfo
from src.mp4 import extract_media, extract_song, encapsulate, write_metadata, fix_encapsulate, fix_esds_box, \
    check_song_integrity
from src.save import save
from src.task import Task, Status
from src.types import Codec, ParentDoneHandler
from src.url import Song, Album, URLType, Playlist
from src.utils import get_codec_from_codec_id, check_song_existence, check_song_exists, if_raw_atmos, \
    check_album_existence, playlist_write_song_index, run_sync, safely_create_task
from src.legacy.mp4 import extract_media as legacy_extract_media
from src.legacy.mp4 import decrypt as legacy_decrypt
from src.legacy.decrypt import WidevineDecrypt

# START -> getMetadata -> getLyrics -> getM3U8 -> downloadSong -> decrypt -> encapsulate -> save -> END

adam_id_task_mapping: Dict[str, Task] = {}
task_lock = asyncio.Semaphore(it(Config).download.maxRunningTasks)


async def task_done(task: Task, status: Status):
    task_lock.release()
    task.update_status(status)
    if task.parentDone:
        await task.parentDone.try_done()
    del adam_id_task_mapping[task.adamId]


async def on_decrypt_success(adam_id: str, key: str, sample: bytes, sample_index: int):
    it(SpeedMeasurer).record_decrypt(len(sample))
    safely_create_task(recv_decrypted_sample(adam_id, sample_index, sample))


async def on_decrypt_failed(adam_id: str, key: str, sample: bytes, sample_index: int):
    await it(WrapperManager).decrypt(adam_id, key, sample, sample_index)


async def recv_decrypted_sample(adam_id: str, sample_index: int, sample: bytes):
    task = adam_id_task_mapping[adam_id]
    task.decryptedSamples[sample_index] = sample
    task.decryptedCount += 1
    if task.decryptedCount == len(task.decryptedSamples):
        safely_create_task(decrypt_done(adam_id))


async def decrypt_done(adam_id: str):
    task = adam_id_task_mapping[adam_id]
    codec = get_codec_from_codec_id(task.m3u8Info.codec_id)

    song = await run_sync(encapsulate, task.info, bytes().join(task.decryptedSamples),
                          it(Config).download.atmosConventToM4a)
    if not if_raw_atmos(codec, it(Config).download.atmosConventToM4a):
        if codec != Codec.EC3 or codec != Codec.EC3:
            song = await run_sync(fix_encapsulate, song)
        song = await run_sync(write_metadata, song, task.metadata, it(Config).metadata.embedMetadata,
                              it(Config).download.coverFormat, task.info.params)
        if codec == Codec.AAC or codec == Codec.AAC_DOWNMIX or codec == Codec.AAC_BINAURAL:
            song = await run_sync(fix_esds_box, task.info.raw, song)

    if not await run_sync(check_song_integrity, song):
        task.logger.failed_integrity()

    filename = await run_sync(save, song, codec, task.metadata, task.playlist)
    task.logger.saved()

    await task_done(task, Status.DONE)

    if it(Config).download.afterDownloaded:
        command = it(Config).download.afterDownloaded.format(filename=filename)
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def rip_song(url: Song, codec: str, flags: Flags = Flags(),
                   parent_done: ParentDoneHandler = None, playlist: PlaylistInfo = None):
    task = Task(adam_id=url.id, parent_done=parent_done, playlist=playlist)
    adam_id_task_mapping[url.id] = task
    task.init_logger()
    await task_lock.acquire()

    # Set Metadata
    raw_metadata = await it(WebAPI).get_song_info(task.adamId, url.storefront, it(Config).region.language)
    album_data = await it(WebAPI).get_album_info(raw_metadata.relationships.albums.data[0].id, url.storefront,
                                                 it(Config).region.language)
    task.metadata = SongMetadata.parse_from_song_data(raw_metadata)
    task.metadata.parse_from_album_data(album_data)

    task.update_logger()
    task.logger.create()

    if not await check_song_existence(url.id, url.storefront):
        task.logger.not_exist()
        await task_done(task, Status.FAILED)
        return

    await task.metadata.get_cover(it(Config).download.coverFormat, it(Config).download.coverSize)
    if raw_metadata.attributes.hasTimeSyncedLyrics:
        task.metadata.lyrics = await it(WrapperManager).lyrics(task.adamId, it(Config).region.language,
                                                               url.storefront)
    if playlist:
        task.metadata.set_playlist_index(playlist.songIdIndexMapping.get(url.id))

    # Check existence
    if not flags.force_save and check_song_exists(task.metadata, codec, playlist):
        task.logger.already_exist()
        await task_done(task, Status.DONE)
        return

    # Get M3U8
    if not raw_metadata.attributes.extendedAssetUrls:
        task.logger.audio_not_exist()
        await task_done(task, Status.FAILED)
        return
    if codec == Codec.ALAC and raw_metadata.attributes.extendedAssetUrls.enhancedHls:
        m3u8_url = await it(WrapperManager).m3u8(task.adamId)
    else:
        if codec == Codec.AAC_LEGACY:
            safely_create_task(rip_song_legacy(task))
            return
        else:
            m3u8_url = raw_metadata.attributes.extendedAssetUrls.enhancedHls
    if not m3u8_url and it(Config).download.codecAlternative and Codec.AAC_LEGACY in it(Config).download.codecPriority:
        task.logger.lossless_audio_not_exist_aac()
        safely_create_task(rip_song_legacy(task))
        return
    elif not m3u8_url:
        task.logger.lossless_audio_not_exist()
        await task_done(task, Status.FAILED)
        return

    task.m3u8Info = await extract_media(m3u8_url, codec, task.metadata)
    task.logger.selected_codec(task.m3u8Info.codec_id)
    if all([bool(task.m3u8Info.bit_depth), bool(task.m3u8Info.sample_rate)]):
        task.metadata.set_bit_depth_and_sample_rate(task.m3u8Info.bit_depth, task.m3u8Info.sample_rate)
        # Check existence again
        if not flags.force_save and check_song_exists(task.metadata, codec, playlist):
            task.logger.already_exist()
            await task_done(task, Status.DONE)
            return

    # Download
    task.logger.downloading()
    task.update_status(Status.DOWNLOADING)
    raw_song = await it(WebAPI).download_song(task.m3u8Info.uri)

    # Decrypt
    task.logger.decrypting()
    task.update_status(Status.DECRYPTING)
    codec = get_codec_from_codec_id(task.m3u8Info.codec_id)
    task.info = await run_sync(extract_song, raw_song, codec)
    task.init_decrypted_samples()
    for sampleIndex, sample in enumerate(task.info.samples):
        await it(WrapperManager).decrypt(task.adamId, task.m3u8Info.keys[sample.descIndex], sample.data, sampleIndex)


async def rip_song_legacy(task: Task):
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

    await task_done(task, Status.DONE)

    if it(Config).download.afterDownloaded:
        command = it(Config).download.afterDownloaded.format(filename=filename)
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def rip_album(url: Album, codec: str, flags: Flags = Flags(), parent_done: ParentDoneHandler = None):
    album_info = await it(WebAPI).get_album_info(url.id, url.storefront, it(Config).region.language)
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
        safely_create_task(rip_song(song, codec, flags, done_handler))


async def rip_artist(url: Album, codec: str, flags: Flags = Flags()):
    artist_info = await it(WebAPI).get_artist_info(url.id, url.storefront, it(Config).region.language)
    logger = RipLogger(url.type, url.id)
    logger.set_fullname(artist_info.data[0].attributes.name)

    logger.create()

    async def on_children_done():
        logger.done()

    if flags.include_participate_in_works:
        songs = await it(WebAPI).get_songs_from_artist(url.id, url.storefront, it(Config).region.language)
        done_handler = ParentDoneHandler(len(songs), on_children_done)
        for song_url in songs:
            safely_create_task(rip_song(Song.parse_url(song_url), codec, flags, done_handler))
    else:
        albums = await it(WebAPI).get_albums_from_artist(url.id, url.storefront, it(Config).region.language)
        done_handler = ParentDoneHandler(len(albums), on_children_done)
        for album_url in albums:
            safely_create_task(rip_album(Album.parse_url(album_url), codec, flags, done_handler))


async def rip_playlist(url: Playlist, codec: str, flags: Flags = Flags()):
    playlist_info = await it(WebAPI).get_playlist_info_and_tracks(url.id, url.storefront, it(Config).region.language)
    playlist_info = playlist_write_song_index(playlist_info)
    logger = RipLogger(url.type, url.id)
    logger.set_fullname(playlist_info.data[0].attributes.curatorName, playlist_info.data[0].attributes.name)

    logger.create()

    async def on_children_done():
        logger.done()

    done_handler = ParentDoneHandler(len(playlist_info.data[0].relationships.tracks.data), on_children_done)

    for track in playlist_info.data[0].relationships.tracks.data:
        song = Song(id=track.id, storefront=url.storefront, url="", type=URLType.Song)
        safely_create_task(rip_song(song, codec, flags, done_handler, playlist=playlist_info))
