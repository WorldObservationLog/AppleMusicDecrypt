"""Ripping pipeline for v3.

Default mode is 边下边解 (stream-decrypt): the encrypted media file is
streamed from the Apple CDN and samples are decrypted locally with Temari as
they arrive (StreamDecryptor), so download / decrypt / write overlap and memory
stays bounded to a fragment.

Fallback batch mode (config ``streamDecrypt=false``) downloads the whole file
first, then decrypts per fragment with ``temari.decrypt_par``.

Legacy AAC (aac-legacy) uses pywidevine for the license and pure-Python
AES-CBC for sample decryption — no external binaries anywhere.
"""

import asyncio
import collections
import os
import ssl
import struct
import subprocess
from typing import Optional

import httpx
from creart import it

from src.api import WebAPI
from src.config import Config
from src.decrypt import Decryptor, PREFETCH_KEY
from src.exceptions import CodecNotFoundException, SongNotPassIntegrityCheckException
from src.flags import Flags
from src.hls import extract_media, legacy_extract_media
from src.logger import RipLogger
from src.measurer import Measurer
from src.metadata import SongMetadata
from src.models import PlaylistInfo
from src.mp4 import (StreamingMP4Parser, patch_mvhd_times, mac_epoch_to_datetime,
                     MP4ParseError, parse_init, parse_next_fragment, rebuild_fragment_bytes)
from src.save import prepare_paths, finalize
from src.task import Task, Status
from src.types import Codec, ParentDoneHandler
from src.url import Song, Album, URLType, Playlist
from src.wrapper import WrapperClient
from src.utils import (get_codec_from_codec_id, check_song_existence, check_song_exists,
                       if_raw_atmos, check_album_existence, playlist_write_song_index, run_sync,
                       safely_create_task, language_exist, query_language)


class DownloadManager:
    def __init__(self):
        self.adam_id_task_mapping = {}
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

    # ------------------------------------------------------------------ #
    # Public entry points
    # ------------------------------------------------------------------ #
    async def rip_song(self, url: Song, codec: str, flags: Flags = Flags(),
                       parent_done: ParentDoneHandler = None, playlist: PlaylistInfo = None,
                       timeout_sec: int = 0):
        if self.download_manager.get_task(url.id):
            if parent_done:
                # Already being processed: notify the parent so it does not wait.
                await parent_done.try_done()
            return

        task = Task(adamId=url.id, parentDone=parent_done, playlist=playlist)
        task.logger = RipLogger(URLType.Song, task.adamId)

        try:
            await self.download_manager.register_task(task)

            # Fetch metadata
            raw_metadata = await it(WebAPI).get_song_info(task.adamId, url.storefront, flags.language)
            album_data = await it(WebAPI).get_album_info(
                raw_metadata.relationships.albums.data[0].id, url.storefront, flags.language)
            task.metadata = SongMetadata.parse_from_song_data(raw_metadata)
            task.metadata.parse_from_album_data(album_data)

            task.logger.set_fullname(task.metadata.artist, task.metadata.title)
            task.logger.create()

            if it(Config).region.languageNotExistWarning and not language_exist(url.storefront, flags.language):
                default_language, _ = query_language(url.storefront)
                task.logger.language_not_exist(url.storefront, flags.language, default_language)

            if not await check_song_existence(url.id, url.storefront):
                task.logger.not_exist()
                task.update_status(Status.FAILED)
                task.error = Exception("Song not found on Apple Music")
                return

            task.metadata.cover = await it(WebAPI).get_cover(task.metadata.cover_url,
                                                             it(Config).download.coverFormat,
                                                             it(Config).download.coverSize)

            if raw_metadata.attributes.hasTimeSyncedLyrics:
                task.metadata.lyrics = await it(WrapperClient).lyrics(task.adamId, flags.language, url.storefront)

            if playlist:
                task.metadata.set_playlist_index(playlist.songIdIndexMapping.get(url.id))

            if not flags.force_save and check_song_exists(task.metadata, codec, playlist):
                task.logger.already_exist()
                task.update_status(Status.DONE)
                return

            m3u8_url = await self._get_m3u8_url(task, codec, raw_metadata)

            if codec == Codec.AAC_LEGACY or (
                    it(Config).download.codecAlternative and not raw_metadata.attributes.extendedAssetUrls.enhancedHls
                    and Codec.AAC_LEGACY in it(Config).download.codecPriority):
                await self._rip_song_legacy(task, timeout_sec)
                return

            if not m3u8_url:
                task.logger.lossless_audio_not_exist()
                task.update_status(Status.FAILED)
                task.error = Exception("Lossless audio does not exist")
                return

            try:
                task.m3u8Info = await extract_media(m3u8_url, codec, task)
            except CodecNotFoundException:
                task.logger.audio_not_exist()
                task.update_status(Status.FAILED)
                task.error = CodecNotFoundException(f"Audio codec '{codec}' not found")
                return

            task.logger.selected_codec(task.m3u8Info.codec_id)
            if all([bool(task.m3u8Info.bit_depth), bool(task.m3u8Info.sample_rate)]):
                task.metadata.set_bit_depth_and_sample_rate(task.m3u8Info.bit_depth, task.m3u8Info.sample_rate)
                if not flags.force_save and check_song_exists(task.metadata, codec, playlist):
                    task.logger.already_exist()
                    task.update_status(Status.DONE)
                    return

            task.logger.logger.info("Waiting for available download streams...")
            if it(Config).download.streamDecrypt:
                await self._rip_song_stream(task, timeout_sec)
            else:
                await self._rip_song_batch(task, timeout_sec)

        except asyncio.TimeoutError:
            task.logger.logger.warning("Task processing timed out after waiting in queue")
            task.update_status(Status.FAILED)
            task.error = Exception("Task execution timed out")
        except Exception as e:
            task.logger.logger.exception(f"Error processing song: {e}")
            task.update_status(Status.FAILED)
            task.error = e
        except asyncio.CancelledError:
            task.logger.logger.warning("Task processing was cancelled")
            task.update_status(Status.FAILED)
            task.error = Exception("Task execution cancelled")
            raise
        finally:
            await self.download_manager.unregister_task(task)
            task.update_status(task.status)
            if task.parentDone:
                await task.parentDone.try_done()

    async def _get_m3u8_url(self, task: Task, codec: str, raw_metadata) -> Optional[str]:
        if not raw_metadata.attributes.extendedAssetUrls:
            task.logger.audio_not_exist()
            return None
        if codec == Codec.ALAC and raw_metadata.attributes.extendedAssetUrls.enhancedHls:
            return await it(WrapperClient).m3u8(task.adamId)
        if codec != Codec.AAC_LEGACY:
            return raw_metadata.attributes.extendedAssetUrls.enhancedHls
        return None

    # ------------------------------------------------------------------ #
    # Streaming (边下边解) pipeline
    # ------------------------------------------------------------------ #
    async def _rip_song_stream(self, task: Task, timeout_sec: int = 0):
        local_codec = get_codec_from_codec_id(task.m3u8Info.codec_id)
        raw_atmos = if_raw_atmos(local_codec, it(Config).download.atmosConventToM4a)
        key_uri = task.m3u8Info.keys[0] if task.m3u8Info.keys else PREFETCH_KEY

        final_path, part_path = prepare_paths(local_codec, task.metadata, task.playlist)

        async def _phase():
            stream = await it(Decryptor).stream(task.adamId, key_uri)
            out_file = open(part_path, "wb")
            moofs = {}
            pending = collections.deque()
            sample_queue = asyncio.Queue(maxsize=2048)
            spec_queue = asyncio.Queue()
            init_written = False

            async def submitter():
                while True:
                    item = await sample_queue.get()
                    if item is None:
                        break
                    _, _, data = item
                    await asyncio.to_thread(stream.submit, data)
                    await spec_queue.put((item[0], item[1]))

            def flush(seq: int, payload: bytes):
                if raw_atmos:
                    out_file.write(payload)
                else:
                    out_file.write(moofs[seq])
                    # Re-emit the mdat box header (32-bit, or 64-bit when huge)
                    # so the file stays a valid fragmented MP4.
                    if len(payload) + 8 < 0xFFFFFFFF:
                        out_file.write(struct.pack(">I4s", 8 + len(payload), b"mdat"))
                    else:
                        out_file.write(struct.pack(">I4sQ", 1, b"mdat", 16 + len(payload)))
                    out_file.write(payload)

            async def consumer():
                current_seq = None
                buf = bytearray()
                try:
                    async for plain in stream.aiter():
                        seq, spec = await spec_queue.get()
                        if current_seq is None:
                            current_seq = seq
                            buf = bytearray()
                        if seq != current_seq:
                            flush(current_seq, bytes(buf))
                            current_seq = seq
                            buf = bytearray()
                        buf.extend(plain)
                        it(Measurer).record_decrypt(len(plain))
                        task.decrypted_bytes += len(plain)
                    if current_seq is not None:
                        flush(current_seq, bytes(buf))
                except Exception:
                    raise

            def on_init(init):
                nonlocal init_written
                if raw_atmos:
                    init_written = True
                    return
                creation = mac_epoch_to_datetime(init.creation_time) if init.creation_time is not None else None
                modification = mac_epoch_to_datetime(init.modification_time) if init.modification_time is not None else None
                out_file.write(patch_mvhd_times(init.output_init, creation, modification))
                init_written = True

            def on_fragment_moof(seq, rebuilt_moof):
                moofs[seq] = rebuilt_moof

            def on_sample(seq, spec, data):
                pending.append((seq, spec, data))

            parser = StreamingMP4Parser(on_init=on_init, on_fragment_moof=on_fragment_moof,
                                        on_sample=on_sample)

            submitter_task = asyncio.create_task(submitter())
            consumer_task = asyncio.create_task(consumer())

            async def drain_pending():
                while pending:
                    await sample_queue.put(pending.popleft())

            try:
                task.logger.downloading()
                task.update_status(Status.DOWNLOADING)
                url = task.m3u8Info.uri
                attempts = 0
                max_attempts = max(1, it(Config).download.retryTime)
                resume_from = 0
                while True:
                    try:
                        headers = {}
                        if task.m3u8Info.range_start is not None:
                            start = task.m3u8Info.range_start + resume_from
                            end = task.m3u8Info.range_start + task.m3u8Info.range_length - 1
                            headers["Range"] = f"bytes={start}-{end}" if resume_from == 0 else f"bytes={start}-"
                        elif resume_from:
                            headers["Range"] = f"bytes={resume_from}-"

                        async for chunk in it(WebAPI).stream_song(url, resume_from=resume_from,
                                                                  extra_headers=headers):
                            parser.feed(chunk)
                            await drain_pending()
                        parser.finish()
                        await drain_pending()
                        break
                    except (httpx.HTTPError, ssl.SSLError, MP4ParseError, asyncio.TimeoutError) as e:
                        if not it(Config).download.resumeDownload or attempts >= max_attempts:
                            raise
                        attempts += 1
                        resume_from = parser.next_box_offset
                        task.logger.logger.warning(
                            f"Download interrupted ({e.__class__.__name__}), resuming from offset {resume_from}")
                        await asyncio.sleep(min(2 ** attempts, 30))

                # Finish streaming
                await sample_queue.put(None)
                await submitter_task
                await asyncio.to_thread(stream.finish)
                await consumer_task
                out_file.flush()
                os.fsync(out_file.fileno())
            finally:
                # Ensure background tasks terminate even on failure paths.
                try:
                    sample_queue.put_nowait(None)
                except (asyncio.QueueFull, RuntimeError):
                    pass
                for t in (submitter_task, consumer_task):
                    if not t.done():
                        t.cancel()
                await asyncio.gather(submitter_task, consumer_task, return_exceptions=True)
                out_file.close()
                try:
                    stream.close()
                except Exception:
                    pass

            task.logger.decrypting()
            task.update_status(Status.DECRYPTING)

            if not raw_atmos:
                await run_sync(finalize, str(part_path), str(final_path), task.metadata,
                               it(Config).download.coverFormat)
                ok = await run_sync(check_song_integrity, str(final_path), local_codec)
            else:
                os.replace(part_path, final_path)
                ok = final_path.stat().st_size > 0

            if not ok:
                if it(Config).download.failedSongNotPassIntegrityCheck:
                    task.logger.failed_integrity(True)
                    task.update_status(Status.FAILED)
                    raise SongNotPassIntegrityCheckException("Integrity Check Failed")
                else:
                    task.logger.failed_integrity(False)
                    task.error = SongNotPassIntegrityCheckException("Integrity Check Warning")

            task.logger.saved()
            task.update_status(Status.DONE)
            if it(Config).download.afterDownloaded:
                command = it(Config).download.afterDownloaded.format(filename=str(final_path))
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if timeout_sec > 0:
            await asyncio.wait_for(_phase(), timeout=timeout_sec)
        else:
            await _phase()

    # ------------------------------------------------------------------ #
    # Batch fallback pipeline
    # ------------------------------------------------------------------ #
    async def _rip_song_batch(self, task: Task, timeout_sec: int = 0):
        local_codec = get_codec_from_codec_id(task.m3u8Info.codec_id)
        raw_atmos = if_raw_atmos(local_codec, it(Config).download.atmosConventToM4a)
        key_uri = task.m3u8Info.keys[0] if task.m3u8Info.keys else PREFETCH_KEY
        final_path, part_path = prepare_paths(local_codec, task.metadata, task.playlist)

        async def _phase():
            task.logger.downloading()
            task.update_status(Status.DOWNLOADING)
            headers = {}
            if task.m3u8Info.range_start is not None:
                headers["Range"] = (f"bytes={task.m3u8Info.range_start}-"
                                    f"{task.m3u8Info.range_start + task.m3u8Info.range_length - 1}")
            raw_song = bytearray()
            async for chunk in it(WebAPI).stream_song(task.m3u8Info.uri, extra_headers=headers):
                raw_song.extend(chunk)

            task.logger.decrypting()
            task.update_status(Status.DECRYPTING)
            template = await it(Decryptor).get_template(task.adamId, key_uri)
            init, off = parse_init(bytes(raw_song))
            if init is None:
                raise MP4ParseError("No init segment found")
            creation = mac_epoch_to_datetime(init.creation_time) if init.creation_time is not None else None
            modification = mac_epoch_to_datetime(init.modification_time) if init.modification_time is not None else None
            out = bytearray()
            if not raw_atmos:
                out.extend(patch_mvhd_times(init.output_init, creation, modification))
            seq = 0
            while True:
                frag, off = parse_next_fragment(bytes(raw_song), off, seq)
                if frag is None:
                    break
                decrypted = []
                for spec in frag.samples:
                    payload = frag.mdat_payload[spec.offset:spec.offset + spec.length]
                    decrypted.append(template.decrypt(payload))
                    it(Measurer).record_decrypt(len(payload))
                    task.decrypted_bytes += len(payload)
                if raw_atmos:
                    out.extend(b"".join(decrypted))
                else:
                    out.extend(rebuild_fragment_bytes(frag, b"".join(decrypted)))
                seq += 1
            del raw_song

            with open(part_path, "wb") as f:
                f.write(bytes(out))
            del out

            if not raw_atmos:
                await run_sync(finalize, str(part_path), str(final_path), task.metadata,
                               it(Config).download.coverFormat)
                ok = await run_sync(check_song_integrity, str(final_path), local_codec)
            else:
                os.replace(part_path, final_path)
                ok = final_path.stat().st_size > 0

            if not ok:
                if it(Config).download.failedSongNotPassIntegrityCheck:
                    task.logger.failed_integrity(True)
                    task.update_status(Status.FAILED)
                    raise SongNotPassIntegrityCheckException("Integrity Check Failed")
                else:
                    task.logger.failed_integrity(False)
                    task.error = SongNotPassIntegrityCheckException("Integrity Check Warning")

            task.logger.saved()
            task.update_status(Status.DONE)
            if it(Config).download.afterDownloaded:
                command = it(Config).download.afterDownloaded.format(filename=str(final_path))
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if timeout_sec > 0:
            await asyncio.wait_for(_phase(), timeout=timeout_sec)
        else:
            await _phase()

    # ------------------------------------------------------------------ #
    # Legacy (Widevine / aac-legacy) pipeline — pure Python decryption
    # ------------------------------------------------------------------ #
    async def _rip_song_legacy(self, task: Task, timeout_sec: int = 0):
        final_path, part_path = prepare_paths(Codec.AAC_LEGACY, task.metadata, task.playlist)

        async def _phase():
            task.m3u8Info = await legacy_extract_media(await it(WrapperClient).webplayback(task.adamId))
            task.logger.downloading()
            task.update_status(Status.DOWNLOADING)
            headers = {}
            if task.m3u8Info.range_start is not None:
                headers["Range"] = (f"bytes={task.m3u8Info.range_start}-"
                                    f"{task.m3u8Info.range_start + task.m3u8Info.range_length - 1}")
            raw_song = bytearray()
            async for chunk in it(WebAPI).stream_song(task.m3u8Info.uri, extra_headers=headers):
                raw_song.extend(chunk)

            task.logger.decrypting()
            task.update_status(Status.DECRYPTING)
            kid, key = await it(Decryptor).legacy_content_key(task.adamId, task.m3u8Info.keys[0])

            init, off = parse_init(bytes(raw_song))
            if init is None:
                raise MP4ParseError("No init segment found")
            creation = mac_epoch_to_datetime(init.creation_time) if init.creation_time is not None else None
            modification = mac_epoch_to_datetime(init.modification_time) if init.modification_time is not None else None
            out = bytearray()
            out.extend(patch_mvhd_times(init.output_init, creation, modification))
            seq = 0
            while True:
                frag, off = parse_next_fragment(bytes(raw_song), off, seq)
                if frag is None:
                    break
                decrypted = b"".join(
                    _decrypt_cbcs_sample(frag.mdat_payload[spec.offset:spec.offset + spec.length],
                                         spec.iv, key, init.tracks.get(spec.desc_index),
                                         [(p.bytes_of_clear_data, p.bytes_of_protected_data)
                                          for p in spec.sub_sample_patterns])
                    for spec in frag.samples
                )
                out.extend(rebuild_fragment_bytes(frag, decrypted))
                it(Measurer).record_decrypt(len(decrypted))
                seq += 1
            del raw_song

            with open(part_path, "wb") as f:
                f.write(bytes(out))
            del out

            await run_sync(finalize, str(part_path), str(final_path), task.metadata,
                           it(Config).download.coverFormat)
            ok = await run_sync(check_song_integrity, str(final_path), Codec.AAC_LEGACY)
            if not ok:
                task.logger.failed_integrity(True)
                task.update_status(Status.FAILED)
                raise SongNotPassIntegrityCheckException("Integrity Check Failed")

            task.logger.saved()
            task.update_status(Status.DONE)
            if it(Config).download.afterDownloaded:
                command = it(Config).download.afterDownloaded.format(filename=str(final_path))
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if timeout_sec > 0:
            await asyncio.wait_for(_phase(), timeout=timeout_sec)
        else:
            await _phase()

    # ------------------------------------------------------------------ #
    # Containers (album / artist / playlist)
    # ------------------------------------------------------------------ #
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


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #
def _decrypt_cbcs_sample(sample: bytes, iv: Optional[bytes], key: bytes,
                         track_info, patterns=None) -> bytes:
    """Pure-Python legacy (Widevine CENC/CBCS) sample decryption.

    ``patterns`` are the per-sample sub-sample patterns from ``senc``
    (list of (bytes_of_clear_data, bytes_of_protected_data)); an empty list
    means full-sample encryption. Decryption mode is chosen by the track's
    scheme: 'cbcs' uses AES-CBC (with pattern when crypt/skip are non-zero),
    anything else (Apple legacy 'cenc', 8-byte IV) uses AES-CTR where the
    sample IV is the high 64 bits of the counter block.
    """
    if not iv or not track_info or not track_info.protected:
        return sample
    if not patterns:
        patterns = [(0, len(sample))]
    scheme = (track_info.scheme_type or "").lower()
    if scheme == "cbcs":
        return _cbcs_decrypt_runs(sample, patterns, key, iv,
                                  track_info.crypt_byte_block or 1,
                                  track_info.skip_byte_block or 0)
    return _decrypt_cenc_sample(sample, iv, key, patterns)


def _decrypt_cenc_sample(sample: bytes, iv: bytes, key: bytes, patterns) -> bytes:
    """AES-CTR (CENC) sample decryption with an 8/16-byte per-sample IV.

    Counter block: ``iv`` (high bytes) + 64-bit big-endian block counter that
    increments per 16-byte block of protected data (starting at 0). Clear
    bytes (sub-sample gaps) do not consume counter blocks. CTR is a stream
    cipher: the whole protected length is decrypted, including the partial
    final block.
    """
    from Crypto.Cipher import AES

    if not patterns:
        patterns = [(0, len(sample))]
    if len(iv) < 16:
        iv = iv.ljust(16, b"\x00")
    aes = AES.new(key, AES.MODE_ECB)
    out = bytearray(sample)
    pos = 0
    protected_done = 0
    for clear, protected in patterns:
        pos += clear
        remaining = protected
        block = 0
        while remaining > 0:
            n = min(16, remaining)
            counter = iv[:8] + struct.pack(">Q", protected_done // 16 + block)
            ks = aes.encrypt(counter)[:n]
            off = pos + block * 16
            val = int.from_bytes(out[off:off + n], "big") ^ int.from_bytes(ks, "big")
            out[off:off + n] = val.to_bytes(n, "big")
            remaining -= n
            block += 1
        pos += protected
        protected_done += protected
    return bytes(out)


def _cbcs_decrypt_runs(data: bytes, patterns, key: bytes, iv: bytes, cbb: int, sbb: int) -> bytes:
    """AES-CBC (CBCS) decryption. One chained cipher across the sample's
    protected runs; 16-byte-aligned prefixes are decrypted, tails pass."""
    from Crypto.Cipher import AES

    out = bytearray(data)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    pos = 0
    for clear, protected in patterns:
        pos += clear
        if protected <= 0:
            continue
        if cbb == 0 or sbb == 0:
            aligned = protected & ~15
            if aligned:
                dec = cipher.decrypt(bytes(out[pos:pos + aligned]))
                out[pos:pos + aligned] = dec
        else:
            block = pos
            block_end = pos + protected
            while block < block_end:
                n = min(cbb * 16, block_end - block)
                aligned = n & ~15
                if aligned:
                    dec = cipher.decrypt(bytes(out[block:block + aligned]))
                    out[block:block + aligned] = dec
                block += n
                block += sbb * 16
        pos += protected
    return bytes(out)


def check_song_integrity(path: str, codec: str) -> bool:
    """Pure-Python structural integrity check: the output file must parse as a
    valid fragmented MP4 (init + >=1 fragment)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return False
    try:
        init, off = parse_init(data)
        if init is None:
            return False
        seq = 0
        while True:
            frag, off = parse_next_fragment(data, off, seq)
            if frag is None:
                break
            seq += 1
        return seq > 0
    except MP4ParseError:
        return False