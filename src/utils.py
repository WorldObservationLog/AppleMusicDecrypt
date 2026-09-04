import asyncio
import concurrent.futures
import json
import subprocess
import time
from asyncio import AbstractEventLoop
from copy import deepcopy
from datetime import datetime, timedelta
from itertools import islice
from pathlib import Path

import m3u8
import regex
from bs4 import BeautifulSoup
from creart import it
from pydantic import ValidationError

from src.config import Config, CONFIG_VERSION
from src.logger import GlobalLogger
from src.models import PlaylistInfo
from src.models.album_meta import Tracks
from src.types import *

executor_pool = concurrent.futures.ThreadPoolExecutor()
background_tasks = set()


def check_url(url):
    pattern = regex.compile(
        r'^(?:https:\/\/(?:beta\.music|music)\.apple\.com\/(\w{2})(?:\/album|\/album\/.+))\/(?:id)?(\d[^\D]+)(?:$|\?)')
    result = regex.findall(pattern, url)
    return result[0][0], result[0][1]


def check_playlist_url(url):
    pattern = regex.compile(
        r'^(?:https:\/\/(?:beta\.music|music)\.apple\.com\/(\w{2})(?:\/playlist|\/playlist\/.+))\/(?:id)?(pl\.[\w-]+)(?:$|\?)')
    result = regex.findall(pattern, url)
    return result[0][0], result[0][1]


def byte_length(i):
    return (i.bit_length() + 7) // 8


def find_best_codec(parsed_m3u8: m3u8.M3U8, codec: str) -> Optional[m3u8.Playlist]:
    available_medias = [playlist for playlist in parsed_m3u8.playlists
                        if regex.match(CodecRegex.get_pattern_by_codec(codec), playlist.stream_info.audio)]
    available_medias.sort(key=lambda x: x.stream_info.average_bandwidth, reverse=True)
    if codec == Codec.ALAC:
        limited_medias = [media for media in available_medias
                          if int(media.media[0].extras["bit_depth"]) <= it(Config).download.maxBitDepth
                          and int(media.media[0].extras["sample_rate"]) <= it(Config).download.maxSampleRate]
    else:
        limited_medias = available_medias
    if not limited_medias:
        return None
    return limited_medias[0]


def chunk(it, size):
    it = iter(it)
    return iter(lambda: tuple(islice(it, size)), ())


def timeit(func):
    async def process(func, *args, **params):
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **params)
        else:
            return func(*args, **params)

    async def helper(*args, **params):
        start = time.time()
        result = await process(func, *args, **params)
        it(GlobalLogger).logger.debug(f'{func.__name__}: {time.time() - start}')
        return result

    return helper


def get_digit_from_string(text: str) -> int:
    return int(''.join(filter(str.isdigit, text)))


def _ttml_time_to_lrc(time_value: str) -> str:
    """Convert a TTML begin/end value to ``[mm:ss.xx]`` (or the plain ``mm:ss.xx``
    inside a tag) text, handling ``h:mm:ss.xxx`` / ``m:ss.xxx`` / ``ss.xxx``."""
    if time_value.find('.') == -1:
        time_value += '.000'
    h = m = s = ms = 0
    split_time = time_value.split(":")
    ms_part = split_time[-1].split(".")
    if len(split_time) == 1:
        s, ms = get_digit_from_string(ms_part[0]), get_digit_from_string(ms_part[1])
    elif len(split_time) == 2:
        m, s, ms = (get_digit_from_string(split_time[0]), get_digit_from_string(ms_part[0]),
                    get_digit_from_string(ms_part[1]))
    else:
        h, m, s, ms = (get_digit_from_string(split_time[0]), get_digit_from_string(split_time[1]),
                       get_digit_from_string(ms_part[0]), get_digit_from_string(ms_part[1]))
    total = m + h * 60
    return f"{str(total).rjust(2, '0')}:{str(s).rjust(2, '0')}.{str(int(ms / 10)).rjust(2, '0')}"


def _lyrics_extra_lines(b, item_key: str, timestamp: str) -> list[str]:
    """Append translation / pronunciation lines for one lyric line."""
    out = []
    meta = b.tt.head.metadata.iTunesMetadata if b.tt.head and b.tt.head.metadata else None
    if not meta:
        return out
    if "translation" in it(Config).download.lyricsExtra:
        trans_type = meta.get("type")
        for translation in meta.find_all("translation"):
            # Apple syllable-lyrics/ttmlLocalizations format:
            #   <translation type="subtitle" xml:lang="ja-JP">
            #     <text for="L1">訳</text>
            #   </translation>
            if item_key == translation.get("for"):
                if trans_type == "replacement":
                    return [f"[{timestamp}]{translation.text}"]
                out.append(f"[{timestamp}]{translation.text}")
            else:
                for text_el in translation.find_all("text"):
                    if item_key == text_el.get("for"):
                        out.append(f"[{timestamp}]{text_el.text}")
    if "pronunciation" in it(Config).download.lyricsExtra:
        for transliteration in meta.find_all("transliteration"):
            # Apple ttmlLocalizations:
            #   <transliteration xml:lang="ja-Latn">
            #     <text for="L1"><span ...>word</span> <span ...>word</span></text>
            #   </transliteration>
            if item_key == transliteration.get("for"):
                out.append(f"[{timestamp}]{transliteration.text}")
            else:
                for text_el in transliteration.find_all("text"):
                    if item_key == text_el.get("for"):
                        # Remove span tags but keep spaces between words.
                        trans_text = "".join(
                            text_el.get_text() for _ in [0])
                        out.append(f"[{timestamp}]{trans_text}")
    return out


def ttml_convent_karaoke(ttml: str) -> str:
    """Convert word-timed (syllable) TTML into karaoke-style LRC.

    Lines that carry per-word ``<span begin end>`` children become
    ``[line_begin]<word_end>word <word_end>word ...`` (player-highlightable).
    Lines without word timing fall back to plain ``[begin]text``.
    """
    b = BeautifulSoup(ttml, features="xml")
    lrc_lines = []
    body = b.tt.body
    for div in body.children:
        for item in div.children:
            if item is None or getattr(item, "get", None) is None:
                continue
            line_begin = item.get("begin")
            if not line_begin:
                continue
            line_ts = _ttml_time_to_lrc(line_begin)
            spans = [c for c in item.children
                     if getattr(c, "name", None) == "span" and c.get("begin") and c.get("end")]
            if spans:
                words = []
                for child in item.children:
                    if getattr(child, "name", None) == "span":
                        if child.get("begin") is None or child.get("end") is None:
                            continue
                        word_ts = _ttml_time_to_lrc(child["end"])
                        text = child.get("text") or child.get_text() or ""
                        words.append(f"<{word_ts}>{text}")
                    else:
                        if words:
                            words.append(" ")
                lrc_lines.append(f"[{line_ts}]{''.join(words)}".rstrip())
            else:
                lrc_lines.append(f"[{line_ts}]{item.get_text() or ''}")
            for extra in _lyrics_extra_lines(b, item.get("itunes:key"), line_ts):
                lrc_lines.append(extra)
    return "\n".join(lrc_lines)


def ttml_convent(ttml: str) -> str:
    if it(Config).download.lyricsFormat == "ttml":
        return ttml
    if it(Config).download.lyricsSyllable:
        return ttml_convent_karaoke(ttml)

    b = BeautifulSoup(ttml, features="xml")
    lrc_lines = []

    for item in b.tt.body.children:
        for lyric in item.children:
            if lyric is None or getattr(lyric, "get", None) is None:
                continue
            lyric_time: str = lyric.get("begin")
            if not lyric_time:
                return ""
            ts = _ttml_time_to_lrc(lyric_time)
            lrc_lines.append(f"[{ts}]{lyric.text}")
            for extra in _lyrics_extra_lines(b, lyric.get("itunes:key"), ts):
                lrc_lines.append(extra)
    return "\n".join(lrc_lines)


def check_song_exists(metadata, codec: str, playlist: PlaylistInfo = None):
    song_name, dir_path = get_song_name_and_dir_path(codec, metadata, playlist)
    return (Path(dir_path) / Path(song_name + get_suffix(codec, it(Config).download.atmosConventToM4a))).exists()


def get_valid_filename(filename: str):
    return "".join(i for i in filename if i not in ["<", ">", ":", "\"", "/", "\\", "|", "?", "*"])


def get_valid_dir_name(dirname: str):
    return regex.sub(r"\.+$", "", get_valid_filename(dirname))


def get_codec_from_codec_id(codec_id: str) -> str:
    codecs = [Codec.AC3, Codec.EC3, Codec.AAC, Codec.ALAC, Codec.AAC_BINAURAL, Codec.AAC_DOWNMIX]
    for codec in codecs:
        if regex.match(CodecRegex.get_pattern_by_codec(codec), codec_id):
            return codec
    return ""


def get_song_id_from_m3u8(m3u8_url: str) -> str:
    parsed_m3u8 = m3u8.load(m3u8_url)
    return regex.search(r"_A(\d*)_", parsed_m3u8.playlists[0].uri)[1]


def if_raw_atmos(codec: str, convent_atmos: bool):
    if (codec == Codec.EC3 or codec == Codec.AC3) and not convent_atmos:
        return True
    return False


def get_suffix(codec: str, convent_atmos: bool):
    if not convent_atmos and codec == Codec.EC3:
        return ".ec3"
    elif not convent_atmos and codec == Codec.AC3:
        return ".ac3"
    else:
        return ".m4a"


def playlist_metadata_to_params(playlist: PlaylistInfo):
    return {"playlistName": playlist.data[0].attributes.name,
            "playlistCuratorName": playlist.data[0].attributes.curatorName}


def get_audio_info_str(metadata, codec: str):
    if all([bool(metadata.bit_depth), bool(metadata.sample_rate), bool(metadata.sample_rate_kHz)]):
        return it(Config).download.audioInfoFormat.format(bit_depth=metadata.bit_depth,
                                                          sample_rate=metadata.sample_rate,
                                                          sample_rate_kHz=metadata.sample_rate_kHz, codec=codec)
    else:
        return ""


def get_path_safe_dict(param: dict):
    new_param = deepcopy(param)
    for key, val in new_param.items():
        if isinstance(val, str):
            new_param[key] = get_valid_filename(str(val))
    return new_param


def get_song_name_and_dir_path(codec: str, metadata, playlist: PlaylistInfo = None):
    if playlist:
        safe_meta = get_path_safe_dict(metadata.model_dump())
        safe_pl_meta = get_path_safe_dict(playlist_metadata_to_params(playlist))
        song_name = it(Config).download.playlistSongNameFormat.format(codec=codec,
                                                                      playlistSongIndex=metadata.playlist_index,
                                                                      audio_info=get_audio_info_str(metadata, codec),
                                                                      total_tracks=metadata.track_total[metadata.disk],
                                                                      total_disks=metadata.disk_total,
                                                                      **safe_meta, **safe_pl_meta)
        dir_path = Path(it(Config).download.playlistDirPathFormat.format(codec=codec, **safe_meta, **safe_pl_meta))
    else:
        safe_meta = get_path_safe_dict(metadata.model_dump())
        song_name = it(Config).download.songNameFormat.format(codec=codec,
                                                              total_tracks=metadata.track_total[metadata.disk],
                                                              total_disks=metadata.disk_total,
                                                              audio_info=get_audio_info_str(metadata, codec),
                                                              **safe_meta)
        dir_path = Path(it(Config).download.dirPathFormat.format(codec=codec, **safe_meta))

    song_name = get_valid_filename(song_name)
    is_abs = dir_path.is_absolute()
    sanitized_parts = [
        part if i == 0 and is_abs else get_valid_dir_name(part)
        for i, part in enumerate(dir_path.parts)
    ]
    dir_path = Path(*sanitized_parts)
    return song_name, dir_path


def playlist_write_song_index(playlist: PlaylistInfo):
    for track_index, track in enumerate(playlist.data[0].relationships.tracks.data):
        playlist.songIdIndexMapping[track.id] = track_index + 1
    return playlist


def convent_mac_timestamp_to_datetime(timestamp: int):
    d = datetime.strptime("01-01-1904", "%m-%d-%Y")
    return d + timedelta(seconds=timestamp)


def ensure_temari_library() -> None:
    """Work around temari's arm64/aarch64 bundle-directory mismatch.

    temari's ``_platform_key()`` normalises ``aarch64`` to ``arm64`` but the
    wheel ships the cdylibs under ``linux-aarch64`` / ``macos-x86_64``-style
    directories, so on Linux/macOS ARM machines the bundled library is never
    found (only x86_64 keys match).  If the default lookup fails, retry with
    the alternate spelling and export ``TEMARI_LIB`` so every later
    ``temari.load()`` (decryptor streaming etc.) picks it up.
    """
    import os
    import platform as _platform

    import temari

    if os.environ.get("TEMARI_LIB"):
        return
    try:
        temari.load()
        return
    except Exception:
        pass

    key = temari._platform_key() or ""
    aliases = []
    if key.endswith("-arm64"):
        aliases.append(key[:-6] + "-aarch64")
    elif key.endswith("-aarch64"):
        aliases.append(key[:-8] + "-arm64")
    machine = _platform.machine().lower()
    for alias in aliases:
        lib_dir = os.path.join(os.path.dirname(temari.__file__), "lib", alias)
        if not os.path.isdir(lib_dir):
            continue
        name = ("temari.dll" if alias.startswith("windows")
                else "libtemari.dylib" if alias.startswith("macos")
                else "libtemari.so")
        candidate = os.path.join(lib_dir, name)
        if os.path.exists(candidate):
            os.environ["TEMARI_LIB"] = candidate
            return


def check_dep():
    """Verify all required Python dependencies (and the bundled Temari cdylib)
    are importable. No external binaries are needed in v3."""
    import importlib

    deps = ["httpx", "regex", "pydantic", "loguru", "m3u8", "tenacity", "prompt_toolkit",
            "mutagen", "temari", "async_lru", "tabulate", "creart"]
    for dep in deps:
        try:
            importlib.import_module(dep)
        except ImportError:
            return False, dep
    try:
        import temari
        ensure_temari_library()
        temari.load()
    except Exception as e:
        # Help diagnose platform mismatches (e.g. Android vs proot Linux:
        # the wheel ships both android-arm64 [bionic] and linux-aarch64
        # [glibc] cdylibs; which one is picked depends on the Python build).
        try:
            key = temari._platform_key()
        except Exception:
            key = "?"
        import sys as _sys
        android = hasattr(_sys, "getandroidapilevel")
        hint = (f"temari runtime ({e}) [platform key: {key}, "
                f"android python: {android}]")
        return False, hint
    return True, None


async def check_song_existence(adam_id: str, region: str):
    from src.wrapper import WrapperClient
    from src.api import WebAPI
    check = False
    for m_region in (await it(WrapperClient).status()).get("regions", []):
        try:
            check = await it(WebAPI).exist_on_storefront_by_song_id(adam_id, region, m_region)
            if check:
                break
        except ValidationError:
            pass
    return check


async def check_album_existence(album_id: str, region: str):
    from src.wrapper import WrapperClient
    from src.api import WebAPI
    check = False
    for m_region in (await it(WrapperClient).status()).get("regions", []):
        try:
            check = await it(WebAPI).exist_on_storefront_by_album_id(album_id, region, m_region)
            if check:
                break
        except ValidationError:
            pass
    return check


async def run_sync(task: Callable, *args):
    return await it(AbstractEventLoop).run_in_executor(executor_pool, task, *args)


def safely_create_task(coro):
    task = it(AbstractEventLoop).create_task(coro)
    background_tasks.add(task)

    def done_callback(*args):
        background_tasks.remove(task)
        if task.exception():
            try:
                raise task.exception()
            except Exception as e:
                it(GlobalLogger).logger.exception(e)

    task.add_done_callback(done_callback)


def count_total_track_and_disc(tracks: Tracks):
    disc_count = tracks.data[-1].attributes.discNumber
    track_count = {}
    for track in tracks.data:
        if track_count.get(track.attributes.discNumber, 0) < track.attributes.trackNumber:
            track_count[track.attributes.discNumber] = track.attributes.trackNumber
    return disc_count, track_count


def get_tasks_num():
    return len(background_tasks)


def query_language(region: str):
    with open("assets/storefronts.json", "r") as f:
        storefronts = json.load(f)
        for storefront in storefronts["data"]:
            if storefront["id"].upper() == region.upper():
                return storefront["attributes"]["defaultLanguageTag"], storefront["attributes"]["supportedLanguageTags"]
        return None


def language_exist(region: str, language: str):
    _, languages = query_language(region)
    return language in languages


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for part in version.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def config_outdated():
    return _version_tuple(it(Config).version) < _version_tuple(CONFIG_VERSION)
