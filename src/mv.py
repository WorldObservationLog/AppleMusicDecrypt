"""Music video downloader (v3).

MV streams are delivered as separate video + audio HLS playlists (each a
fragmented MP4 with an ``#EXT-X-MAP`` init segment and ``.m4s`` fragments),
encrypted with Widevine ``cbcs``. We download both, decrypt with the Widevine
content key from the wrapper's ``/license`` (pure-Python AES-CBC cbcs, matched
byte-for-byte against Bento4 mp4decrypt), then remux them into one MP4 with
the pure-Python muxer — no MP4Box/ffmpeg needed.
"""

import asyncio
import os
import re
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
from src.mux import mux_mv
from src.rip import _decrypt_cbcs_sample
from src.url import URLType, MusicVideo
from src.wrapper import WrapperClient
from src.utils import get_valid_filename


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
    return max(audio, key=lambda a: int(re.search(r"(\d+)$", a.group_id or "").group(1)) if re.search(r"(\d+)$", a.group_id or "") else 0)


class MVRipper:
    async def rip(self, url: MusicVideo, flags=None):
        logger = RipLogger(URLType.MusicVideo, url.id)
        try:
            manifest = await self._fetch_manifest(url)
            attrs = manifest["data"][0]["attributes"]
            rel = manifest["data"][0].get("relationships", {})
            artist_name = attrs.get("artistName") or ""
            mv_name = attrs.get("name") or url.id
            logger.set_fullname(artist_name, mv_name)
            logger.create()

            cfg = it(Config).mv
            master_url = await it(WrapperClient).webplayback(url.id)
            master_txt = await it(WebAPI).download_m3u8(master_url)
            master = m3u8.loads(master_txt, uri=master_url)
            vv = _select_video_variant(master, cfg.maxHeight)
            logger.logger.info(f"Selected video: {vv.stream_info.resolution} "
                               f"({vv.stream_info.bandwidth} bps)")
            audio_alt = _select_audio_alternative(master, cfg.audioType)
            if audio_alt is not None:
                logger.logger.info(f"Selected audio: {audio_alt.group_id}")

            logger.downloading()
            v_init_data, v_segs, v_key = await self._download_stream(vv.absolute_uri, url.id, logger)
            a_init_data = a_segs = a_key = None
            if audio_alt is not None:
                a_init_data, a_segs, a_key = await self._download_stream(audio_alt.absolute_uri, url.id, logger)
                if a_key is None:
                    raise RuntimeError("MV audio stream has no Widevine key")
            if v_key is None:
                raise RuntimeError("MV video stream has no Widevine key")
            if not v_segs:
                raise RuntimeError("No video fragments downloaded")

            logger.decrypting()
            v_content = await it(Decryptor).mv_content_key(url.id, v_key)
            a_content = await it(Decryptor).mv_content_key(url.id, a_key) if a_key else None

            v_init, v_frags = await self._decrypt_stream(v_init_data, v_segs, v_content, logger, "video")
            if a_content is not None:
                a_init, a_frags = await self._decrypt_stream(a_init_data, a_segs, a_content, logger, "audio")
            else:
                a_init, a_frags = None, []
            out = mux_mv(v_init, a_init, v_frags, a_frags)

            final_path, cover = await self._save(out, mv_name, artist_name, attrs, url)
            logger.saved()
        except Exception as e:
            logger.logger.exception(f"MV download failed: {e}")
            raise

    async def _fetch_manifest(self, url: MusicVideo):
        resp = await it(WebAPI)._request(
            "GET", f"https://amp-api.music.apple.com/v1/catalog/{url.storefront}/music-videos/{url.id}",
            params={"include": "artists,albums", "l": it(Config).region.language})
        return resp.json()

    async def _download_stream(self, media_playlist_url: str, adam_id: str, logger) -> tuple:
        """Fetch the media playlist, download init + every segment, return the
        encrypted (init_data, [segment_bytes]) and the Widevine key URI."""
        txt = await it(WebAPI).download_m3u8(media_playlist_url)
        media = m3u8.loads(txt, uri=media_playlist_url)
        init_url = _find_map_url(txt, media_playlist_url)
        key_uri = _find_widevine_key(media)
        timeout = float(it(Config).download.downloadTimeout or 60)
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            init_data = (await client.get(init_url)).content
            segs = []
            for seg in media.segments:
                segs.append((await client.get(seg.absolute_uri)).content)
        return init_data, segs, key_uri

    async def _decrypt_stream(self, init_data, segments, content_key, logger, kind):
        init, _ = parse_init(init_data)
        if init is None:
            raise MP4ParseError(f"MV {kind} init segment did not parse")
        ti = list(init.tracks.values())[0]
        frags = []
        for i, seg in enumerate(segments):
            frag, _ = parse_next_fragment(seg, 0, i)
            if frag is None:
                continue
            decrypted = []
            for spec in frag.samples:
                sample = frag.mdat_payload[spec.offset:spec.offset + spec.length]
                iv = spec.iv if spec.iv is not None else (ti.constant_iv or b"\x00" * 16)
                pats = [(p.bytes_of_clear_data, p.bytes_of_protected_data) for p in spec.sub_sample_patterns]
                decrypted.append(_decrypt_cbcs_sample(sample, iv, content_key, ti, pats))
                it(Measurer).record_decrypt(len(sample))
            frags.append(rebuild_fragment_bytes(frag, b"".join(decrypted)))
            logger.logger.debug(f"{kind} fragment {i + 1}/{len(segments)} decrypted")
        return init, frags

    async def _save(self, out: bytes, mv_name: str, artist_name: str, attrs, url):
        cfg = it(Config).mv
        save_dir = Path(cfg.saveDir)
        save_dir.mkdir(parents=True, exist_ok=True)
        safe = get_valid_filename(f"{artist_name} - {mv_name}")
        final_path = save_dir / f"{safe}.m4v"
        part_path = save_dir / f"{safe}.m4v.part"
        part_path.write_bytes(out)

        # metadata
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
        # cover
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
        mp4.save()
        os.replace(part_path, final_path)
        if cover:
            (save_dir / f"cover.{it(Config).download.coverFormat}").write_bytes(cover)
        return final_path, cover