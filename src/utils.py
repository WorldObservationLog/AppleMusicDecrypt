import asyncio
import subprocess
import sys
import time
from datetime import datetime, timedelta
from itertools import islice
from pathlib import Path

import m3u8
import regex
from bs4 import BeautifulSoup
from loguru import logger

from src.config import Download
from src.exceptions import NotTimeSyncedLyricsException
from src.models import PlaylistInfo
from src.types import *

from copy import deepcopy


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


def find_best_codec(parsed_m3u8: m3u8.M3U8, codec: str, alacMax, atmosMax) -> Optional[m3u8.Playlist]:
    available_medias = []
    for playlist in parsed_m3u8.playlists:
        if regex.match(CodecRegex.get_pattern_by_codec(codec), playlist.stream_info.audio):
            split_str = playlist.stream_info.audio.split('-')
            if "alac" in playlist.stream_info.audio:
                if int(split_str[3]) <= alacMax:
                    available_medias.append(playlist)
            elif "atmos" in playlist.stream_info.audio:
                if int(split_str[2]) <= atmosMax:
                    available_medias.append(playlist)
            else:
                available_medias.append(playlist)

    if not available_medias:
        return None
    available_medias.sort(key=lambda x: x.stream_info.average_bandwidth, reverse=True)

    return available_medias[0]


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
        logger.debug(f'{func.__name__}: {time.time() - start}')
        return result

    return helper


def get_digit_from_string(text: str) -> int:
    return int(''.join(filter(str.isdigit, text)))


def ttml_convent_to_lrc(ttml: str) -> str:
    b = BeautifulSoup(ttml, features="xml")
    lrc_lines = []
    for item in b.tt.body.children:
        for lyric in item.children:
            h, m, s, ms = 0, 0, 0, 0
            lyric_time: str = lyric.get("begin")
            if not lyric_time:
                raise NotTimeSyncedLyricsException
            if lyric_time.find('.') == -1:
                lyric_time += '.000'
            match lyric_time.count(":"):
                case 0:
                    split_time = lyric_time.split(".")
                    s, ms = get_digit_from_string(split_time[0]), get_digit_from_string(split_time[1])
                case 1:
                    split_time = lyric_time.split(":")
                    s_ms = split_time[-1]
                    del split_time[-1]
                    split_time.extend(s_ms.split("."))
                    m, s, ms = (get_digit_from_string(split_time[0]), get_digit_from_string(split_time[1]),
                                get_digit_from_string(split_time[2]))
                case 2:
                    split_time = lyric_time.split(":")
                    s_ms = split_time[-1]
                    del split_time[-1]
                    split_time.extend(s_ms.split("."))
                    h, m, s, ms = (get_digit_from_string(split_time[0]), get_digit_from_string(split_time[1]),
                                   get_digit_from_string(split_time[2]), get_digit_from_string(split_time[3]))
            lrc_lines.append(
                f"[{str(m + h * 60).rjust(2, '0')}:{str(s).rjust(2, '0')}.{str(int(ms / 10)).rjust(2, '0')}]{lyric.text}")
    return "\n".join(lrc_lines)


def check_song_exists(metadata, config: Download, codec: str, playlist: PlaylistInfo = None):
    song_name, dir_path = get_song_name_and_dir_path(codec, config, metadata, playlist)
    return (Path(dir_path) / Path(song_name + get_suffix(codec, config.atmosConventToM4a))).exists()


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


def get_audio_info_str(metadata, codec: str, config: Download):
    if all([bool(metadata.bit_depth), bool(metadata.sample_rate), bool(metadata.sample_rate_kHz)]):
        return config.audioInfoFormat.format(bit_depth=metadata.bit_depth, sample_rate=metadata.sample_rate,
                                             sample_rate_kHz=metadata.sample_rate_kHz, codec=codec)
    else:
        return ""


def get_path_safe_dict(param: dict):
    new_param = deepcopy(param)
    for key, val in new_param.items():
        if isinstance(val, str):
            new_param[key] = get_valid_filename(str(val))
    return new_param


def get_song_name_and_dir_path(codec: str, config: Download, metadata, playlist: PlaylistInfo = None):
    if playlist:
        safe_meta = get_path_safe_dict(metadata.model_dump())
        safe_pl_meta = get_path_safe_dict(playlist_metadata_to_params(playlist))
        song_name = config.playlistSongNameFormat.format(codec=codec, playlistSongIndex=metadata.playlistIndex,
                                                         audio_info=get_audio_info_str(metadata, codec, config),
                                                         **safe_meta, **safe_pl_meta)
        dir_path = Path(config.playlistDirPathFormat.format(codec=codec, **safe_meta, **safe_pl_meta))
    else:
        safe_meta = get_path_safe_dict(metadata.model_dump())
        song_name = config.songNameFormat.format(codec=codec, audio_info=get_audio_info_str(metadata, codec, config),
                                                 **safe_meta)
        dir_path = Path(config.dirPathFormat.format(codec=codec, **safe_meta))
    if sys.platform == "win32":
        song_name = get_valid_filename(song_name)
        dir_path = Path(*[get_valid_dir_name(part) if ":\\" not in part else part for part in dir_path.parts])
    return song_name, dir_path


def playlist_write_song_index(playlist: PlaylistInfo):
    for track_index, track in enumerate(playlist.data[0].relationships.tracks.data):
        playlist.songIdIndexMapping[track.id] = track_index + 1
    return playlist


def convent_mac_timestamp_to_datetime(timestamp: int):
    d = datetime.strptime("01-01-1904", "%m-%d-%Y")
    return d + timedelta(seconds=timestamp)


def check_dep():
    for dep in ["ffmpeg", "gpac", "mp4box", "mp4edit", "mp4extract", "adb"]:
        try:
            subprocess.run(dep, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            return False, dep
    return True, None
