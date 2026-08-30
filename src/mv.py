"""Music video downloader (v3).

MV streams are delivered as separate video + audio HLS playlists (each a
fragmented MP4 with an ``#EXT-X-MAP`` init segment and ``.m4s`` fragments),
encrypted with Widevine ``cbcs``. We download both, decrypt with the Widevine
content key from the wrapper's ``/license`` (pure-Python AES-CBC cbcs, matched
byte-for-byte against Bento4 mp4decrypt), then remux them into one MP4 with
the pure-Python muxer — no MP4Box/ffmpeg needed.

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
from src.mp4 import (MP4ParseError, parse_init, parse_next_fragment, rebuild_fragment_bytes)
from src.mux import (FragmentStore, build_container, fragment_entry, mux_mv, mux_mv_streamed)
from src.rip import _decrypt_cbcs_sample
from src.url import URLType, MusicVideo
from src.wrapper import WrapperClient
from src.utils import get_valid_filename, run_sync


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


class MVRipper:
    async def rip(self, url: MusicVideo, flags=None):
        logger = RipLogger(URLType.MusicVideo, url.id)
        try:
            manifest = await self._fetch_manifest(url)
            attrs = manifest["data"][0]["attributes"]
            artist_name = attrs.get("artistName") or ""
            mv_name = attrs.get("name") or url.id
            logger.set_fullname(artist_name, mv_name)
            logger.create()

            cfg = it(Config).mv
            low_memory = it(Config).download.lowMemory

            master_url = await it(WrapperClient).webplayback(url.id)
            master = m3u8.loads(await it(WebAPI).download_m3u8(master_url), uri=master_url)
            vv = _select_video_variant(master, cfg.maxHeight)
            logger.logger.info(f"Selected video: {vv.stream_info.resolution} ({vv.stream_info.bandwidth} bps)")
            audio_alt = _select_audio_alternative(master, cfg.audioType)
            if audio_alt is not None:
                logger.logger.info(f"Selected audio: {audio_alt.group_id}")

            logger.downloading()
            v_txt = await it(WebAPI).download_m3u8(vv.absolute_uri)
            v_media = m3u8.loads(v_txt, uri=vv.absolute_uri)
            v_init_url = _find_map_url(v_txt, vv.absolute_uri)
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

            timeout = float(it(Config).download.downloadTimeout or 60)
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                v_init_data = (await client.get(v_init_url)).content
                a_init_data = (await client.get(a_init_url)).content if a_init_url else None

            logger.decrypting()
            v_content = await it(Decryptor).mv_content_key(url.id, v_key)
            a_content = await it(Decryptor).mv_content_key(url.id, a_key) if a_key else None

            v_init, _ = parse_init(v_init_data)
            a_init, _ = parse_init(a_init_data) if a_init_data else (None, 0)
            if v_init is None:
                raise MP4ParseError("MV video init segment did not parse")
            if a_content is not None and a_init is None:
                raise MP4ParseError("MV audio init segment did not parse")

            save_dir = Path(cfg.saveDir)
            save_dir.mkdir(parents=True, exist_ok=True)
            safe = get_valid_filename(f"{artist_name} - {mv_name}")
            final_path = save_dir / f"{safe}.m4v"
            part_path = save_dir / f"{safe}.m4v.part"

            if low_memory:
                await self._rip_low_memory(logger, v_init, a_init, v_media, a_media,
                                           v_content, a_content, part_path)
            else:
                await self._rip_in_memory(logger, v_init, a_init, v_media, a_media,
                                          v_content, a_content, part_path)

            final_path, cover = await self._save(part_path, final_path, mv_name, artist_name, attrs)
            logger.saved()
        except Exception as e:
            logger.logger.exception(f"MV download failed: {e}")
            raise

    # ------------------------------------------------------------------ #
    # download / decrypt / mux
    # ------------------------------------------------------------------ #
    async def _download_segment(self, client, url: str, retries: int) -> bytes:
        for attempt in range(retries + 1):
            try:
                return (await client.get(url)).content
            except httpx.HTTPError:
                if attempt >= retries:
                    raise
                await asyncio.sleep(min(2 ** attempt, 15))
        raise RuntimeError("segment download failed")

    async def _decrypt_segment(self, seg: bytes, init, content_key):
        frag, _ = parse_next_fragment(seg, 0, 0)
        if frag is None:
            return None
        ti = list(init.tracks.values())[0]
        decrypted = []
        for spec in frag.samples:
            sample = frag.mdat_payload[spec.offset:spec.offset + spec.length]
            iv = spec.iv if spec.iv is not None else (ti.constant_iv or b"\x00" * 16)
            pats = [(p.bytes_of_clear_data, p.bytes_of_protected_data) for p in spec.sub_sample_patterns]
            decrypted.append(_decrypt_cbcs_sample(sample, iv, content_key, ti, pats))
            it(Measurer).record_decrypt(len(sample))
        return rebuild_fragment_bytes(frag, b"".join(decrypted))

    async def _rip_in_memory(self, logger, v_init, a_init, v_media, a_media,
                             v_content, a_content, part_path):
        cfg = it(Config).mv
        timeout = float(it(Config).download.downloadTimeout or 60)
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            sem = asyncio.Semaphore(cfg.segmentConcurrency)

            async def get(seg):
                async with sem:
                    return await self._download_segment(client, seg.absolute_uri, cfg.segmentRetries)

            v_segs = await asyncio.gather(*[get(s) for s in v_media.segments])
            a_segs = await asyncio.gather(*[get(s) for s in a_media.segments]) if a_media else []

        async def dec(seg, init, key):
            return await self._decrypt_segment(seg, init, key)

        v_frags = [f for f in (await asyncio.gather(*[dec(s, v_init, v_content) for s in v_segs])) if f]
        a_frags = [f for f in (await asyncio.gather(*[dec(s, a_init, a_content) for s in a_segs])) if f] if a_content else []
        if not v_frags:
            raise RuntimeError("No video fragments downloaded")
        out = mux_mv(v_init, a_init, v_frags, a_frags)
        await run_sync(_write_bytes, part_path, out)

    async def _rip_low_memory(self, logger, v_init, a_init, v_media, a_media,
                              v_content, a_content, part_path):
        cfg = it(Config).mv
        ftyp, moov, v_old, a_old, v_ts, a_ts = build_container(v_init, a_init)
        with tempfile.TemporaryDirectory() as td:
            store = FragmentStore(os.path.join(td, "frags.bin"))
            try:
                timeout = float(it(Config).download.downloadTimeout or 60)
                async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                    sem = asyncio.Semaphore(cfg.segmentConcurrency)

                    async def process(kind, seg, init, content, old_id, new_id, ts_sec):
                        async with sem:
                            data = await self._download_segment(client, seg.absolute_uri, cfg.segmentRetries)
                        frag = await self._decrypt_segment(data, init, content)
                        if frag is None:
                            return
                        t, _, frag = fragment_entry(frag, old_id, new_id, ts_sec, kind)
                        store.add(kind, frag, t)

                    tasks = [process(0, s, v_init, v_content, v_old, 1, v_ts) for s in v_media.segments]
                    if a_content is not None and a_media is not None:
                        tasks += [process(1, s, a_init, a_content, a_old, 2, a_ts) for s in a_media.segments]
                    await asyncio.gather(*tasks)
                mux_mv_streamed(v_init, a_init, store, part_path)
            finally:
                store.close()

    async def _fetch_manifest(self, url: MusicVideo):
        resp = await it(WebAPI)._request(
            "GET", f"https://amp-api.music.apple.com/v1/catalog/{url.storefront}/music-videos/{url.id}",
            params={"include": "artists,albums", "l": it(Config).region.language})
        return resp.json()

    async def _save(self, part_path, final_path, mv_name, artist_name, attrs):
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
        if cover:
            final_path.parent.joinpath(f"cover.{it(Config).download.coverFormat}").write_bytes(cover)
        return final_path, cover