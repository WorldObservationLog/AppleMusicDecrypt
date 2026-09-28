"""Music video downloader (v3).

MV streams are delivered as separate video + audio HLS playlists (each a
fragmented MP4 with an ``#EXT-X-MAP`` init segment and ``.m4s`` fragments),
encrypted with Widevine ``cbcs``. Video variants whose ``ALLOWED-CPC`` only
admits PlayReady ``SL3000`` (e.g. 4K) use the PlayReady key instead, acquired
with the SL3000 device in ``assets/pr_device.json``. We download both, decrypt
with the content key from the wrapper's ``/license`` (pure-Python AES-CBC cbcs, matched
then remux them into one MP4 with the pure-Python muxer.

Default keeps fragments in memory (fine for most MVs). With ``[download]
lowMemory`` the fragments are spilled to a temp file and the muxer streams
straight to the final ``.part`` file. Segments download concurrently
(``[mv] segmentConcurrency``) with retries (``[mv] segmentRetries``).
"""

import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx
import m3u8
import mutagen.mp4
from creart import it

from src.api import WebAPI
from src.config import Config
from src.decrypt import Decryptor
from src.logger import RipLogger
from src.measurer import Measurer
from src.metadata import SongMetadata
from src.mp4 import (MP4ParseError, parse_init, parse_next_fragment, rebuild_fragment_bytes)
from src.mux import (FragmentStore, build_container, fragment_entry, mux_mv, mux_mv_streamed)
from src.rip import _decrypt_cbcs_sample
from src.url import URLType, MusicVideo
from src.wrapper import WrapperClient
from src.utils import get_valid_filename, get_song_name_and_dir_path, run_sync


def _write_bytes(path, data: bytes):
    with open(path, "wb") as f:
        f.write(data)


def _find_map_url(media_playlist_txt: str, base_url: str) -> str:
    m = re.search(r'#EXT-X-MAP:URI="([^"]+)"', media_playlist_txt)
    if not m:
        raise RuntimeError("MV media playlist has no #EXT-X-MAP init segment")
    return urljoin(base_url, m.group(1))


def _find_widevine_key(media) -> Optional[str]:
    for k in media.keys:
        u = k.uri or ""
        if u.startswith("data:") and "UTF-16" not in u and "base64," in u:
            return u
    return None


def _find_playready_key(media) -> Optional[str]:
    for k in media.keys:
        if k.keyformat == "com.microsoft.playready" and k.uri:
            return k.uri
    return None


def _allowed_cpc(master_txt: str, variant_uri: str) -> str:
    """``ALLOWED-CPC`` of the ``#EXT-X-STREAM-INF`` whose URI is ``variant_uri``.

    The m3u8 library does not parse this attribute, so read the raw text.
    """
    attrs = None
    for raw in master_txt.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs = line
            continue
        if attrs is None or not line or line.startswith("#"):
            continue
        if line == variant_uri:
            m = re.search(r'ALLOWED-CPC="([^"]*)"', attrs)
            return m.group(1) if m else ""
        attrs = None
    return ""


def _select_video_variant(master, max_height: int):
    candidates = [p for p in master.playlists if p.stream_info.resolution]
    if not candidates:
        raise RuntimeError("MV master has no video variants")
    allowed = [p for p in candidates if p.stream_info.resolution[1] <= max_height]
    pool = allowed or candidates
    return max(pool, key=lambda p: (p.stream_info.resolution[1], p.stream_info.bandwidth))


def _select_audio_alternative(master, audio_type: str):
    groups = master.media or []
    audio = [m for m in groups if m.type == "AUDIO" and m.uri]
    if not audio:
        return None
    priority = {
        "atmos": ["audio-atmos", "audio-ac3", "audio-stereo-256"],
        "ac3": ["audio-ac3", "audio-stereo-256"],
        "aac": ["audio-stereo-256", "audio-stereo-128"],
    }.get(audio_type, ["audio-atmos", "audio-ac3", "audio-stereo-256"])
    for p in priority:
        for a in audio:
            if a.group_id == p:
                return a
    return max(audio, key=lambda a: int(re.search(r"(\d+)$", a.group_id or "").group(1))
               if re.search(r"(\d+)$", a.group_id or "") else 0)


def _describe_video(variant) -> str:
    """e.g. ``1920x1080, 8.52 Mbps, avc1.640028``."""
    info = variant.stream_info
    parts = ["x".join(map(str, info.resolution))]
    if info.bandwidth:
        parts.append(f"{info.bandwidth / 1_000_000:.2f} Mbps")
    if info.codecs:
        video_codecs = [c for c in info.codecs.split(",") if not c.startswith(("mp4a", "ec-3", "ac-3"))]
        parts.append(",".join(video_codecs) or info.codecs)
    return ", ".join(parts)


def _describe_audio(media) -> str:
    """e.g. ``audio-atmos, 16/JOC channels``."""
    desc = media.group_id or media.name or "unknown"
    if media.channels:
        desc += f", {media.channels} channels"
    return desc


class MVRipper:
    async def rip(self, url: MusicVideo, flags=None, codec: Optional[str] = None,
                  playlist=None, album_id: Optional[str] = None):
        """Download one music video.

        ``codec`` is set when the MV is ripped as a track of an album or
        playlist: it is then saved beside the songs (song dir/name formats,
        ``codec`` filling ``{codec}``), in the context of ``playlist`` or of
        album ``album_id``.  Otherwise it goes to ``[mv] saveDir``.
        """
        logger = RipLogger(URLType.MusicVideo, url.id)
        force_save = bool(flags and flags.force_save)
        language = flags.language if flags else it(Config).region.language
        try:
            manifest = await self._fetch_manifest(url, language)
            attrs = manifest["data"][0]["attributes"]
            artist_name = attrs.get("artistName") or ""
            mv_name = attrs.get("name") or url.id
            logger.set_fullname(artist_name, mv_name)
            logger.create()
            # Register MV node in the TUI task tree.
            try:
                from creart import it as _it
                from src.tui.task_tree import TaskTree, NodeStatus
                _mv_tree = _it(TaskTree)
                _mv_tree.register_mv(url.id, f"{artist_name} - {mv_name}")
                _mv_tree.update_mv_status(url.id, NodeStatus.RUNNING)
            except Exception:
                _mv_tree = None

            cfg = it(Config).mv
            final_path = None
            if codec:
                final_path = await self._track_path(manifest, url, language, codec, playlist, album_id)
            as_track = final_path is not None
            if final_path is None:
                safe = get_valid_filename(f"{artist_name} - {mv_name}")
                final_path = Path(cfg.saveDir) / f"{safe}.m4v"
            save_dir = final_path.parent
            part_path = final_path.with_name(final_path.name + ".part")
            if not force_save and final_path.exists():
                logger.already_exist()
                if _mv_tree is not None:
                    _mv_tree.update_mv_status(url.id, NodeStatus.EXIST)
                return
            low_memory = it(Config).download.lowMemory

            master_url = await it(WrapperClient).webplayback(url.id)
            # Fetched without the browser User-Agent of the API client: with a
            # browser UA Apple omits the 4K (PlayReady SL3000) variants.
            master_resp = await it(WebAPI)._get_download_client().get(master_url)
            master_resp.raise_for_status()
            master_txt = master_resp.text
            master = m3u8.loads(master_txt, uri=master_url)
            vv = _select_video_variant(master, cfg.maxHeight)
            logger.logger.info(f"Selected video: {_describe_video(vv)}")
            # SL3000-only variants (e.g. 4K) have no software Widevine key:
            # decrypt them with the PlayReady SL3000 device instead.
            use_playready = "SL3000" in _allowed_cpc(master_txt, vv.uri).upper()
            if use_playready:
                logger.logger.info("Video DRM: PlayReady")
            audio_alt = _select_audio_alternative(master, cfg.audioType)
            if audio_alt is not None:
                logger.logger.info(f"Selected audio: {_describe_audio(audio_alt)}")
            else:
                logger.logger.warning("No audio stream found, saving video only")

            v_txt = await it(WebAPI).download_m3u8(vv.absolute_uri)
            v_media = m3u8.loads(v_txt, uri=vv.absolute_uri)
            v_init_url = _find_map_url(v_txt, vv.absolute_uri)
            if use_playready:
                v_key = _find_playready_key(v_media)
                if v_key is None:
                    raise RuntimeError("MV video stream has no PlayReady key")
            else:
                v_key = _find_widevine_key(v_media)
                if v_key is None:
                    raise RuntimeError("MV video stream has no Widevine key")

            a_media = a_init_url = a_key = None
            if audio_alt is not None:
                a_txt = await it(WebAPI).download_m3u8(audio_alt.absolute_uri)
                a_media = m3u8.loads(a_txt, uri=audio_alt.absolute_uri)
                a_init_url = _find_map_url(a_txt, audio_alt.absolute_uri)
                a_key = _find_widevine_key(a_media)
                if a_key is None:
                    raise RuntimeError("MV audio stream has no Widevine key")

            client = it(WebAPI)._get_download_client()
            v_init_data = (await client.get(v_init_url)).content
            a_init_data = (await client.get(a_init_url)).content if a_init_url else None

            if use_playready:
                v_content = await it(Decryptor).mv_playready_content_key(url.id, v_key)
            else:
                v_content = await it(Decryptor).mv_content_key(url.id, v_key)
            a_content = await it(Decryptor).mv_content_key(url.id, a_key) if a_key else None

            v_init, _ = parse_init(v_init_data)
            a_init, _ = parse_init(a_init_data) if a_init_data else (None, 0)
            if v_init is None:
                raise MP4ParseError("MV video init segment did not parse")
            if a_content is not None and a_init is None:
                raise MP4ParseError("MV audio init segment did not parse")

            save_dir.mkdir(parents=True, exist_ok=True)

            # Segments are decrypted as they arrive, so this covers both.
            segments = f"{len(v_media.segments)} video"
            if a_content is not None:
                segments += f" + {len(a_media.segments)} audio"
            logger.downloading(f"{segments} segments")
            if low_memory:
                await self._rip_low_memory(logger, v_init, a_init, v_media, a_media,
                                           v_content, a_content, part_path)
            else:
                await self._rip_in_memory(logger, v_init, a_init, v_media, a_media,
                                          v_content, a_content, part_path)

            final_path, cover = await self._save(part_path, final_path, mv_name, artist_name, attrs,
                                                 save_cover_file=not as_track)
            logger.saved()
        except Exception as e:
            logger.logger.exception(f"Failed to download music video: {e}")
            try:
                from creart import it as _it
                from src.tui.task_tree import TaskTree, NodeStatus
                _it(TaskTree).update_mv_status(url.id, NodeStatus.FAILED)
            except Exception:
                pass
            raise
        else:
            try:
                from creart import it as _it
                from src.tui.task_tree import TaskTree, NodeStatus
                _it(TaskTree).update_mv_status(url.id, NodeStatus.DONE)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # download / decrypt / mux
    # ------------------------------------------------------------------ #
    async def _download_segment(self, client, url: str, retries: int) -> bytes:
        for attempt in range(retries + 1):
            try:
                # Stream so the status bar's download speed reflects MV
                # segment traffic (a plain .get() bypasses the Measurer).
                buf = bytearray()
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(WebAPI.DOWNLOAD_CHUNK_SIZE):
                        it(Measurer).record_download(len(chunk))
                        buf.extend(chunk)
                return bytes(buf)
            except httpx.HTTPError:
                if attempt >= retries:
                    raise
                await asyncio.sleep(min(2 ** attempt, 15))
        raise RuntimeError("segment download failed")

    async def _decrypt_segment(self, seg: bytes, init, content_key) -> list[bytes]:
        """Decrypt every fragment contained in one CDN segment.

        Apple MV media segments are ~6s long and can contain several
        moof/mdat pairs.  The previous implementation only decrypted the
        first fragment of each segment, silently dropping ~2/3 of video
        frames and causing broken seeking / playback in desktop players.
        """
        ti = list(init.tracks.values())[0]
        results: list[bytes] = []
        offset = 0
        seq = 0
        while offset < len(seg):
            frag, next_offset = parse_next_fragment(seg, offset, seq)
            if frag is None:
                break
            seq += 1
            offset = next_offset
            decrypted = []
            for spec in frag.samples:
                sample = frag.mdat_payload[spec.offset:spec.offset + spec.length]
                iv = spec.iv if spec.iv is not None else (ti.constant_iv or b"\x00" * 16)
                pats = [(p.bytes_of_clear_data, p.bytes_of_protected_data) for p in spec.sub_sample_patterns]
                decrypted.append(_decrypt_cbcs_sample(sample, iv, content_key, ti, pats))
                it(Measurer).record_decrypt(len(sample))
            results.append(rebuild_fragment_bytes(frag, b"".join(decrypted)))
        return results


    async def _rip_in_memory(self, logger, v_init, a_init, v_media, a_media,
                             v_content, a_content, part_path):
        cfg = it(Config).mv
        client = it(WebAPI)._get_download_client()
        sem = asyncio.Semaphore(cfg.segmentConcurrency)

        async def get(seg):
            async with sem:
                return await self._download_segment(client, seg.absolute_uri, cfg.segmentRetries)

        v_segs = await asyncio.gather(*[get(s) for s in v_media.segments])
        a_segs = await asyncio.gather(*[get(s) for s in a_media.segments]) if a_media else []

        async def dec(seg, init, key):
            return await self._decrypt_segment(seg, init, key)

        v_frags = [f for flist in (await asyncio.gather(*[dec(s, v_init, v_content) for s in v_segs])) for f in flist]
        a_frags = [f for flist in (await asyncio.gather(*[dec(s, a_init, a_content) for s in a_segs])) for f in flist] if a_content else []
        if not v_frags:
            raise RuntimeError("No video fragments downloaded")
        from src.mp4 import parse_fragment_timing, patch_tfdt_delta
        # Normalise timestamps per stream so VLC starts at 0.
        for flist in (v_frags, a_frags):
            if not flist:
                continue
            base = min((parse_fragment_timing(f)[1] or 0) for f in flist)
            if base:
                for i, f in enumerate(flist):
                    flist[i] = patch_tfdt_delta(f, base)
        logger.logger.info("Muxing video and audio...")
        out = mux_mv(v_init, a_init, v_frags, a_frags)
        await run_sync(_write_bytes, part_path, out)

    async def _rip_low_memory(self, logger, v_init, a_init, v_media, a_media,
                              v_content, a_content, part_path):
        cfg = it(Config).mv
        ftyp, moov, v_old, a_old, v_ts, a_ts = build_container(v_init, a_init)
        with tempfile.TemporaryDirectory() as td:
            store = FragmentStore(os.path.join(td, "frags.bin"))
            try:
                client = it(WebAPI)._get_download_client()
                sem = asyncio.Semaphore(cfg.segmentConcurrency)

                async def process(kind, seg, init, content, old_id, new_id, ts_sec):
                    async with sem:
                        data = await self._download_segment(client, seg.absolute_uri, cfg.segmentRetries)
                    frags = await self._decrypt_segment(data, init, content)
                    for frag in frags:
                        t, _, frag = fragment_entry(frag, old_id, new_id, ts_sec, kind)
                        store.add(kind, frag, t, ts_sec)

                tasks = [process(0, s, v_init, v_content, v_old, 1, v_ts) for s in v_media.segments]
                if a_content is not None and a_media is not None:
                    tasks += [process(1, s, a_init, a_content, a_old, 2, a_ts) for s in a_media.segments]
                await asyncio.gather(*tasks)
                # Apple MV fragments start at a non-zero timeline (~10s);
                # normalise so VLC / players start at 0.
                store.normalize_timestamps()
                logger.logger.info("Muxing video and audio...")
                mux_mv_streamed(v_init, a_init, store, part_path)
            finally:
                store.close()

    async def _fetch_manifest(self, url: MusicVideo, language: str):
        resp = await it(WebAPI)._request(
            "GET", f"https://amp-api.music.apple.com/v1/catalog/{url.storefront}/music-videos/{url.id}",
            params={"include": "artists,albums", "l": language})
        return resp.json()

    async def _track_path(self, manifest, url: MusicVideo, language: str, codec: str,
                          playlist=None, album_id: Optional[str] = None) -> Optional[Path]:
        """Path of an album/playlist MV, built like its songs' paths.

        None when the MV has no album to take the metadata from.
        """
        data = manifest["data"][0]
        albums = ((data.get("relationships") or {}).get("albums") or {}).get("data") or []
        if not albums:
            return None
        album = next((a for a in albums if a["id"] == album_id), albums[0])
        album_attrs = album.get("attributes") or {}
        attrs = data["attributes"]
        metadata = SongMetadata(
            song_id=data["id"], title=attrs.get("name"), artist=attrs.get("artistName"),
            album_id=album["id"], album=album_attrs.get("name") or attrs.get("albumName"),
            album_artist=album_attrs.get("artistName") or attrs.get("albumArtistName"),
            album_created=album_attrs.get("releaseDate"), composer=attrs.get("composerName"),
            genre=attrs.get("genreNames"), created=attrs.get("releaseDate"),
            track=attrs.get("name"), tracknum=attrs.get("trackNumber") or 0,
            disk=attrs.get("discNumber") or 1, copyright=album_attrs.get("copyright"),
            record_company=album_attrs.get("recordLabel"), upc=album_attrs.get("upc"),
            isrc=attrs.get("isrc"))
        metadata.parse_from_album_data(await it(WebAPI).get_album_info(album["id"], url.storefront, language))
        if playlist:
            metadata.set_playlist_index(playlist.songIdIndexMapping.get(url.id))
        name, dir_path = get_song_name_and_dir_path(codec.upper(), metadata, playlist)
        return dir_path / f"{name}.m4v"

    async def _save(self, part_path, final_path, mv_name, artist_name, attrs, save_cover_file=True):
        tags = {}
        embed = it(Config).metadata.embedMetadata
        if "title" in embed:
            tags["©nam"] = mv_name
        if "artist" in embed:
            tags["©ART"] = artist_name
        if "album" in embed and attrs.get("albumName"):
            tags["©alb"] = attrs.get("albumName")
        if "genre" in embed and attrs.get("genreNames"):
            tags["©gen"] = attrs.get("genreNames", [])[:1]
        if "created" in embed and attrs.get("releaseDate"):
            tags["©day"] = attrs["releaseDate"]
        if "isrc" in embed and attrs.get("isrc"):
            tags["----:com.apple.iTunes:ISRC"] = attrs["isrc"].encode()
        rtng = {"explicit": 1, "clean": 2}.get(attrs.get("contentRating"), 0)
        if "rtng" in embed:
            tags["rtng"] = (rtng,)
        cover = None
        artwork = attrs.get("artwork") or {}
        if artwork.get("url") and it(Config).download.saveCover:
            try:
                cover = await it(WebAPI).get_cover(artwork["url"], it(Config).download.coverFormat,
                                                   it(Config).download.coverSize)
                if "covr" in embed:
                    tags["covr"] = (mutagen.mp4.MP4Cover(cover),)
            except Exception:
                cover = None

        mp4 = mutagen.mp4.Open(str(part_path))
        mp4.update(tags)
        # mutagen rewrites the whole file (CPU+IO); run off the event loop.
        await run_sync(mp4.save)
        os.replace(part_path, final_path)
        # In an album folder, cover.<fmt> is the album's (written by its songs).
        if cover and save_cover_file:
            final_path.parent.joinpath(f"cover.{it(Config).download.coverFormat}").write_bytes(cover)
        return final_path, cover