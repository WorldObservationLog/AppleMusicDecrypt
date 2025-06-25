import asyncio
import concurrent.futures
import subprocess
import sys
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

from src.config import Config
from src.exceptions import NotTimeSyncedLyricsException
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
        it(GlobalLogger).logger.debug(f'{func.__name__}: {time.time() - start}')
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
    for dep in ["ffmpeg", "gpac", "mp4box", "mp4edit", "mp4extract"]:
        try:
            subprocess.run(dep, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            return False, dep
    return True, None


async def check_song_existence(adam_id: str, region: str):
    from src.grpc.manager import WrapperManager
    from src.api import WebAPI
    check = False
    for m_region in (await it(WrapperManager).status()).regions:
        try:
            check = await it(WebAPI).exist_on_storefront_by_song_id(adam_id, region, m_region)
            if check:
                break
        except ValidationError:
            pass
    return check


async def check_album_existence(album_id: str, region: str):
    from src.grpc.manager import WrapperManager
    from src.api import WebAPI
    check = False
    for m_region in (await it(WrapperManager).status()).regions:
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