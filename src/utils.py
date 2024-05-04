import asyncio
import time
from itertools import islice
from pathlib import Path

import m3u8
import regex
from bs4 import BeautifulSoup

from src.config import Download
from src.exceptions import NotTimeSyncedLyricsException

from src.types import *


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
            print('this function is a coroutine: {}'.format(func.__name__))
            return await func(*args, **params)
        else:
            print('this is not a coroutine')
            return func(*args, **params)

    async def helper(*args, **params):
        print('{}.time'.format(func.__name__))
        start = time.time()
        result = await process(func, *args, **params)

        # Test normal function route...
        # result = await process(lambda *a, **p: print(*a, **p), *args, **params)

        print('>>>', time.time() - start)
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


def check_song_exists(metadata, config: Download, codec: str):
    song_name = get_valid_filename(config.songNameFormat.format(**metadata.model_dump()))
    dir_path = Path(config.dirPathFormat.format(**metadata.model_dump()))
    if not config.atmosConventToM4a and codec == Codec.EC3:
        return (Path(dir_path) / Path(song_name).with_suffix(".ec3")).exists()
    else:
        return (Path(dir_path) / Path(song_name).with_suffix(".m4a")).exists()


def get_valid_filename(filename: str):
    return "".join(i for i in filename if i not in "\/:*?<>|")
