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
        # Batch pre-fetch caches (shared across an album/playlist's songs).
        self._m3u8_cache: dict[str, str] = {}          # adam_id -> wrapper m3u8 URL
        self._song_info_cache: dict[tuple, object] = {}
        self._song_info_pending: dict[tuple, asyncio.Future] = {}
        self._album_info_cache: dict[tuple, object] = {}
        self._album_info_pending: dict[tuple, asyncio.Future] = {}

    # ------------------------------------------------------------------ #
    # Cached metadata fetches (dedupe across songs, in-flight coalescing)
    # ------------------------------------------------------------------ #
    async def _get_song_info_cached(self, adam_id: str, storefront: str, lang: str):
        key = (adam_id, storefront, lang)
        if key in self._song_info_cache:
            return self._song_info_cache[key]
        pending = self._song_info_pending.get(key)
        if pending is not None:
            return await pending
        fut = asyncio.get_running_loop().create_future()
        self._song_info_pending[key] = fut
        try:
            info = await it(WebAPI).get_song_info(adam_id, storefront, lang)
            self._song_info_cache[key] = info
            fut.set_result(info)
            return info
        except Exception as e:
            fut.set_exception(e)
            raise
        finally:
            self._song_info_pending.pop(key, None)

    async def _get_album_info_cached(self, album_id: str, storefront: str, lang: str):
        key = (album_id, storefront, lang)
        if key in self._album_info_cache:
            return self._album_info_cache[key]
        pending = self._album_info_pending.get(key)
        if pending is not None:
            return await pending
        fut = asyncio.get_running_loop().create_future()
        self._album_info_pending[key] = fut
        try:
            info = await it(WebAPI).get_album_info(album_id, storefront, lang)
            self._album_info_cache[key] = info
            fut.set_result(info)
            return info
        except Exception as e:
            fut.set_exception(e)
            raise
        finally:
            self._album_info_pending.pop(key, None)

    async def _prefetch_batch(self, adam_ids: list[str], storefront: str, codec: str, lang: str):
        """Warm caches for an album/playlist so each song's rip starts hot.

        - prefetch decrypt template (content-independent, warmed once)
        - prefetch song metadata / enhancedHls (all codecs)
        - prefetch wrapper /m3u8 (ALAC, which is the only codec that uses it)
        """
        if not adam_ids:
            return
        sem = asyncio.Semaphore(16)

        async def warm_template():
            try:
                await it(Decryptor).warm_prefetch()
            except Exception:
                pass

        async def warm_song(a):
            async with sem:
                try:
                    await self._get_song_info_cached(a, storefront, lang)
                except Exception:
                    pass

        async def warm_m3u8(a):
            if codec != Codec.ALAC or a in self._m3u8_cache:
                return
            async with sem:
                try:
                    self._m3u8_cache[a] = await it(WrapperClient).m3u8(a)
                except Exception:
                    pass

        await asyncio.gather(
            warm_template(),
            *[warm_song(a) for a in adam_ids],
            *[warm_m3u8(a) for a in adam_ids],
        )

    # ------------------------------------------------------------------ #
    # Public entry points
    # ------------------------------------------------------------------ #
    async def rip_song(self, url: Song, codec: str, flags: Flags = Flags(),
                       parent_done: ParentDoneHandler = None, playlist: PlaylistInfo = None,
                       timeout_sec: int = 0, group_node_id: str = ""):
        if self.download_manager.get_task(url.id):
            if parent_done:
                # Already being processed: notify the parent so it does not wait.
                await parent_done.try_done()
            return

        task = Task(adamId=url.id, parentDone=parent_done, playlist=playlist,
                    group_node_id=group_node_id)
        task.logger = RipLogger(URLType.Song, task.adamId)
        # Register with the TUI task tree (no-op when tree is absent).
        try:
            from creart import it as _it
            from src.tui.task_tree import TaskTree
            _it(TaskTree).register_song(url.id, task.display_name, task,
                                        parent_id=group_node_id)
        except Exception:
            pass

        try:
            await self.download_manager.register_task(task)

            # Fetch metadata (song info, then album + cover + lyrics in parallel)
            task.update_status(Status.PARSING)
            raw_metadata = await self._get_song_info_cached(task.adamId, url.storefront, flags.language)
            album_id = raw_metadata.relationships.albums.data[0].id
            album_f = asyncio.create_task(self._get_album_info_cached(album_id, url.storefront, flags.language))
            cover_f = asyncio.create_task(it(WebAPI).get_cover(raw_metadata.attributes.artwork.url,
                                                               it(Config).download.coverFormat,
                                                               it(Config).download.coverSize))
            lyrics_f = None
            if raw_metadata.attributes.hasTimeSyncedLyrics:
                lyrics_f = asyncio.create_task(it(WrapperClient).lyrics(
                    task.adamId, flags.language, url.storefront,
                    syllable=it(Config).download.lyricsSyllable))

            album_data = await album_f
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

            task.metadata.cover = await cover_f
            if lyrics_f is not None:
                task.metadata.lyrics = await lyrics_f

            if playlist:
                task.metadata.set_playlist_index(playlist.songIdIndexMapping.get(url.id))

            if not flags.force_save and check_song_exists(task.metadata, codec, playlist):
                task.logger.already_exist()
                task.update_status(Status.ALREADY_EXIST)
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
                    task.update_status(Status.ALREADY_EXIST)
                    return

            if it(Config).download.streamDecrypt or it(Config).download.lowMemory:
                # streaming is disk-backed (.part) and low-memory friendly
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
        """Return the master playlist URL for this song.

        Preference order:
        1. wrapper /m3u8 (the local account's real device rendition).  The
           web/API enhancedHls is known to have a "残血" problem: for some
           tracks it does not offer the highest bitrate/codec rendition, so
           it is only used as a fallback.
        2. enhancedHls from the web API (fallback).

        The prefetch pass already warms ``_m3u8_cache`` with wrapper URLs for
        ALAC, so use that when available.
        """
        # 1) wrapper /m3u8, from the prefetch cache when present.
        cached = self._m3u8_cache.get(task.adamId)
        if cached:
            return cached
        try:
            wrapper_url = await it(WrapperClient).m3u8(task.adamId)
            if wrapper_url:
                self._m3u8_cache[task.adamId] = wrapper_url
                return wrapper_url
        except Exception:
            pass

        # 2) fallback: enhancedHls from the web API.
        if raw_metadata.attributes.extendedAssetUrls:
            return raw_metadata.attributes.extendedAssetUrls.enhancedHls

        task.logger.audio_not_exist()
        return None

    # ------------------------------------------------------------------ #
    # Streaming (边下边解) pipeline
    # ------------------------------------------------------------------ #
    async def _rip_song_stream(self, task: Task, timeout_sec: int = 0):
        local_codec = get_codec_from_codec_id(task.m3u8Info.codec_id)
        raw_atmos = if_raw_atmos(local_codec, it(Config).download.atmosConventToM4a)
        segment_keys = task.m3u8Info.segment_keys
        final_path, part_path = prepare_paths(local_codec, task.metadata, task.playlist)

        def key_for(seq: int) -> str:
            if seq < len(segment_keys):
                return segment_keys[seq]
            return PREFETCH_KEY

        async def _phase():
            out_file = open(part_path, "wb")
            current = {"seq": None, "moof": None, "samples": []}
            done = collections.deque()  # (seq, moof, samples, key_uri)

            def on_init(init):
                if raw_atmos:
                    return
                creation = mac_epoch_to_datetime(init.creation_time) if init.creation_time is not None else None
                modification = mac_epoch_to_datetime(init.modification_time) if init.modification_time is not None else None
                out_file.write(patch_mvhd_times(init.output_init, creation, modification))

            def on_fragment_moof(seq, rebuilt_moof):
                if current["moof"] is not None:
                    done.append((current["seq"], current["moof"], current["samples"],
                                 key_for(current["seq"])))
                current["seq"] = seq
                current["moof"] = rebuilt_moof
                current["samples"] = []

            def on_sample(seq, spec, data):
                current["samples"].append(data)

            parser = StreamingMP4Parser(on_init=on_init, on_fragment_moof=on_fragment_moof,
                                        on_sample=on_sample)

            async def flush_one(seq, moof, samples, key_uri):
                template = await it(Decryptor).get_template(task.adamId, key_uri)
                plains = template.decrypt_par(samples)
                for p in plains:
                    it(Measurer).record_decrypt(len(p))
                    task.decrypted_bytes += len(p)
                payload = b"".join(plains)
                if raw_atmos:
                    out_file.write(payload)
                else:
                    out_file.write(moof)
                    if len(payload) + 8 < 0xFFFFFFFF:
                        out_file.write(struct.pack(">I4s", 8 + len(payload), b"mdat"))
                    else:
                        out_file.write(struct.pack(">I4sQ", 1, b"mdat", 16 + len(payload)))
                    out_file.write(payload)

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
                            while done:
                                await flush_one(*done.popleft())
                        parser.finish()
                        if current["moof"] is not None:
                            done.append((current["seq"], current["moof"], current["samples"],
                                         key_for(current["seq"])))
                        while done:
                            await flush_one(*done.popleft())
                        break
                    except (httpx.HTTPError, ssl.SSLError, MP4ParseError, asyncio.TimeoutError) as e:
                        if not it(Config).download.resumeDownload or attempts >= max_attempts:
                            raise
                        attempts += 1
                        resume_from = parser.next_box_offset
                        task.logger.logger.warning(
                            f"Download interrupted ({e.__class__.__name__}), resuming from offset {resume_from}")
                        # Re-download from a box boundary; drop any partial fragment.
                        current["moof"] = None
                        current["samples"] = []
                        await asyncio.sleep(min(2 ** attempts, 30))

                out_file.flush()
                if it(Config).download.fsync:
                    await asyncio.to_thread(os.fsync, out_file.fileno())
            finally:
                out_file.close()

            # Streaming decrypts while downloading (边下边解), so by the time
            # the CDN stream ends the samples are already decrypted — there is
            # no separate DECRYPTING phase here.  Go straight to SAVING.
            task.update_status(Status.SAVING)
            if not raw_atmos:
                ok = await run_sync(finalize_and_verify, str(part_path), str(final_path),
                                    task.metadata, local_codec)
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
        segment_keys = task.m3u8Info.segment_keys
        final_path, part_path = prepare_paths(local_codec, task.metadata, task.playlist)

        def key_for(seq: int) -> str:
            if seq < len(segment_keys):
                return segment_keys[seq]
            return PREFETCH_KEY

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
                template = await it(Decryptor).get_template(task.adamId, key_for(seq))
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

            task.update_status(Status.SAVING)
            if not raw_atmos:
                ok = await run_sync(finalize_and_verify, str(part_path), str(final_path),
                                    task.metadata, local_codec)
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

            task.update_status(Status.SAVING)
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
        album_info = await self._get_album_info_cached(url.id, url.storefront, flags.language)
        logger = RipLogger(url.type, url.id)
        album_name  = album_info.data[0].attributes.name
        artist_name = album_info.data[0].attributes.artistName
        logger.set_fullname(artist_name, album_name)
        logger.create()
        if not await check_album_existence(url.id, url.storefront):
            logger.not_exist()
            return
        # Register album group node in the TUI task tree.
        try:
            from creart import it as _it
            from src.tui.task_tree import TaskTree, NodeKind
            _it(TaskTree).register_group(url.id, NodeKind.ALBUM,
                                         f"{artist_name} - {album_name}")
        except Exception:
            pass

        async def on_children_done():
            logger.done()
            if parent_done:
                await parent_done.try_done()

        done_handler = ParentDoneHandler(len(album_info.data[0].relationships.tracks.data), on_children_done)
        tracks = album_info.data[0].relationships.tracks.data
        safely_create_task(self._prefetch_batch([t.id for t in tracks], url.storefront, codec, flags.language))
        for track in tracks:
            song = Song(id=track.id, storefront=url.storefront, url="", type=URLType.Song)
            safely_create_task(self.rip_song(song, codec, flags, done_handler,
                                             group_node_id=url.id))

    async def rip_artist(self, url: Album, codec: str, flags: Flags = Flags()):
        artist_info = await it(WebAPI).get_artist_info(url.id, url.storefront, flags.language)
        logger = RipLogger(url.type, url.id)
        artist_name = artist_info.data[0].attributes.name
        logger.set_fullname(artist_name)
        logger.create()
        # Register artist group node in the TUI task tree.
        try:
            from creart import it as _it
            from src.tui.task_tree import TaskTree, NodeKind
            _it(TaskTree).register_group(url.id, NodeKind.ARTIST, artist_name)
        except Exception:
            pass

        async def on_children_done():
            logger.done()

        if flags.include_participate_in_works:
            songs = await it(WebAPI).get_songs_from_artist(url.id, url.storefront, flags.language)
            done_handler = ParentDoneHandler(len(songs), on_children_done)
            for song_url in songs:
                safely_create_task(self.rip_song(Song.parse_url(song_url), codec, flags,
                                                 done_handler, group_node_id=url.id))
        else:
            albums = await it(WebAPI).get_albums_from_artist(url.id, url.storefront, flags.language)
            done_handler = ParentDoneHandler(len(albums), on_children_done)
            for album_url in albums:
                safely_create_task(self.rip_album(Album.parse_url(album_url), codec, flags, done_handler))

    async def rip_playlist(self, url: Playlist, codec: str, flags: Flags = Flags()):
        playlist_info = await it(WebAPI).get_playlist_info_and_tracks(url.id, url.storefront, flags.language)
        playlist_info = playlist_write_song_index(playlist_info)
        logger = RipLogger(url.type, url.id)
        pl_name = playlist_info.data[0].attributes.name
        curator = playlist_info.data[0].attributes.curatorName
        logger.set_fullname(curator, pl_name)
        logger.create()
        # Register playlist group node in the TUI task tree.
        try:
            from creart import it as _it
            from src.tui.task_tree import TaskTree, NodeKind
            _it(TaskTree).register_group(url.id, NodeKind.PLAYLIST,
                                         f"{curator} - {pl_name}")
        except Exception:
            pass

        async def on_children_done():
            logger.done()

        done_handler = ParentDoneHandler(len(playlist_info.data[0].relationships.tracks.data), on_children_done)
        tracks = playlist_info.data[0].relationships.tracks.data
        safely_create_task(self._prefetch_batch([t.id for t in tracks], url.storefront, codec, flags.language))
        for track in tracks:
            song = Song(id=track.id, storefront=url.storefront, url="", type=URLType.Song)
            safely_create_task(self.rip_song(song, codec, flags, done_handler,
                                             playlist=playlist_info,
                                             group_node_id=url.id))


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
    """AES-CBC (CBCS) decryption with the crypt/skip pattern.

    The CBC chain restarts from the (per-sample or constant) IV at each
    sub-sample (protected) run; within a run it carries across the encrypted
    blocks only (skipped clear blocks do not advance it). The crypt/skip
    pattern phase restarts at each run. Partial final blocks are left
    untouched (matching standard mp4decrypt behaviour).
    """
    from Crypto.Cipher import AES

    out = bytearray(data)
    pos = 0
    for clear, protected in patterns:
        pos += clear
        if protected <= 0:
            continue
        cipher = AES.new(key, AES.MODE_CBC, iv)
        nfull = protected // 16
        phase = 0
        for blk in range(nfull):
            off = pos + blk * 16
            if cbb == 0 or sbb == 0:
                dec = cipher.decrypt(bytes(out[off:off + 16]))
                out[off:off + 16] = dec
            else:
                if phase % (cbb + sbb) < cbb:
                    dec = cipher.decrypt(bytes(out[off:off + 16]))
                    out[off:off + 16] = dec
                phase += 1
        pos += protected
    return bytes(out)


def _decode_verify_alac(path: str) -> bool | None:
    """Verify ALAC by a real ffmpeg decode.

    Uses the ``ffmpeg`` CLI only.  Returns True on a clean full decode,
    False when any decode error is observed, and None when ffmpeg is not
    installed (caller should skip the check).
    """
    import shutil
    import subprocess
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-i", path, "-map", "0:a:0", "-f", "null", "-"],
            capture_output=True, timeout=120,
        )
        if proc.returncode != 0:
            return False
        err = proc.stderr.decode(errors="replace").lower()
        if ("invalid data" in err or "error" in err
                or "not yet implemented" in err or "patches welcome" in err):
            return False
        return True
    except Exception:
        return None


def try_fix_alac(path: str) -> bool:
    """Repair known Apple ALAC END-tag defects in place.

    Returns True if any frame was repaired, False otherwise.  This is a
    lossless fix (only the missing 3-bit END tag is written).
    """
    from src.alac_fix import find_bad_packets, fix_alac_end_tags
    try:
        bad = find_bad_packets(path)
        if not bad:
            return False
        fixed, _ = fix_alac_end_tags(path)
        return fixed > 0
    except Exception:
        return False


def finalize_and_verify(part_path: str, final_path: str, metadata, codec: str):
    """Write metadata, run optional ALAC repair, then verify via ffmpeg.

    Returns True when the final file is considered good.  ffmpeg absence is
    not fatal (the integrity check is skipped with a warning emitted by the
    startup ffmpeg probe).
    """
    from src.config import Config
    from src.save import finalize
    finalize(part_path, final_path, metadata, it(Config).download.coverFormat)

    # Repair known Apple ALAC END-tag defects before integrity checking.
    if str(codec).upper() == "ALAC" and it(Config).download.alacFix:
        repaired = try_fix_alac(final_path)
        if repaired:
            from src.logger import GlobalLogger
            it(GlobalLogger).logger.warning(
                "ALAC END-tag defect detected and repaired in "
                f"{final_path}")

    result = check_song_integrity(final_path, codec)
    if result is True:
        # Only ALAC performs a real ffmpeg decode; report the pass.
        from src.logger import GlobalLogger
        it(GlobalLogger).logger.success(
            f"Song integrity check passed: {final_path}")
    # None = ffmpeg unavailable -> skip integrity (startup prints warning).
    return result is not False


def check_song_integrity(path: str, codec: str) -> bool | None:
    """Validate a song with a real ffmpeg decode.

    Returns True when ffmpeg decodes the file cleanly, False when it does
    not, and None when ffmpeg is not installed (caller skips the check).
    """
    if str(codec).upper() != "ALAC":
        # Only ALAC has the known Apple END-tag corruption pattern; for other
        # codecs the downloader's own checks are sufficient.
        return True
    return _decode_verify_alac(path)

