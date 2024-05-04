import asyncio
import logging
from ssl import SSLError

import httpcore
import httpx
import regex
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, before_sleep_log

from src.models import *

client = httpx.AsyncClient()
lock = asyncio.Semaphore(1)
user_agent_browser = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
user_agent_itunes = "iTunes/12.11.3 (Windows; Microsoft Windows 10 x64 Professional Edition (Build 19041); x64) AppleWebKit/7611.1022.4001.1 (dt:2)"
user_agent_app = "Music/5.7 Android/10 model/Pixel6GR1YH build/1234 (dt:66)"


@retry(retry=retry_if_exception_type((httpx.TimeoutException, httpcore.ConnectError, SSLError, FileNotFoundError)),
       stop=stop_after_attempt(5),
       before_sleep=before_sleep_log(logger, logging.WARN))
async def get_token():
    req = await client.get("https://beta.music.apple.com")
    index_js_uri = regex.findall(r"/assets/index-legacy-[^/]+\.js", req.text)[0]
    js_req = await client.get("https://beta.music.apple.com" + index_js_uri)
    token = regex.search(r'eyJh([^"]*)', js_req.text)[0]
    return token


@retry(retry=retry_if_exception_type((httpx.TimeoutException, httpcore.ConnectError, SSLError, FileNotFoundError)),
       stop=stop_after_attempt(5),
       before_sleep=before_sleep_log(logger, logging.WARN))
async def download_song(url: str) -> bytes:
    async with lock:
        return (await client.get(url)).content


@retry(retry=retry_if_exception_type((httpx.TimeoutException, httpcore.ConnectError, SSLError, FileNotFoundError)),
       stop=stop_after_attempt(5),
       before_sleep=before_sleep_log(logger, logging.WARN))
async def get_meta(album_id: str, token: str, storefront: str):
    if "pl." in album_id:
        mtype = "playlists"
    else:
        mtype = "albums"
    req = await client.get(f"https://amp-api.music.apple.com/v1/catalog/{storefront}/{mtype}/{album_id}",
                           params={"omit[resource]": "autos", "include": "tracks,artists,record-labels",
                                   "include[songs]": "artists", "fields[artists]": "name",
                                   "fields[albums:albums]": "artistName,artwork,name,releaseDate,url",
                                   "fields[record-labels]": "name"},
                           headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent_browser,
                                    "Origin": "https://music.apple.com"})
    if mtype == "albums":
        return AlbumMeta.model_validate(req.json())
    else:
        result = PlaylistMeta.model_validate(req.json())
        result.data[0].attributes.artistName = "Apple Music"
        if result.data[0].relationships.tracks.next:
            page = 0
            while True:
                page += 100
                page_req = await client.get(
                    f"https://amp-api.music.apple.com/v1/catalog/{storefront}/{mtype}/{album_id}/tracks?offset={page}",
                    headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent_browser,
                             "Origin": "https://music.apple.com"})
                page_result = TracksMeta.model_validate(page_req.json())
                result.data[0].relationships.tracks.data.extend(page_result.data)
                if not page_result.next:
                    break
        return result


@retry(retry=retry_if_exception_type((httpx.TimeoutException, httpcore.ConnectError, SSLError, FileNotFoundError)),
       stop=stop_after_attempt(5),
       before_sleep=before_sleep_log(logger, logging.WARN))
async def get_cover(url: str, cover_format: str):
    formatted_url = regex.sub('bb.jpg', f'bb.{cover_format}', url)
    req = await client.get(formatted_url.replace("{w}x{h}", "10000x10000"),
                           headers={"User-Agent": user_agent_browser})
    return req.content


@retry(retry=retry_if_exception_type((httpx.TimeoutException, httpcore.ConnectError, SSLError, FileNotFoundError)),
       stop=stop_after_attempt(5),
       before_sleep=before_sleep_log(logger, logging.WARN))
async def get_info_from_adam(adam_id: str, token: str, storefront: str):
    req = await client.get(f"https://amp-api.music.apple.com/v1/catalog/{storefront}/songs/{adam_id}",
                           params={"extend": "extendedAssetUrls", "include": "albums"},
                           headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent_itunes,
                                    "Origin": "https://music.apple.com"})
    song_data_obj = SongData.model_validate(req.json())
    for data in song_data_obj.data:
        if data.id == adam_id:
            return data
    return None


@retry(retry=retry_if_exception_type((httpx.TimeoutException, httpcore.ConnectError, SSLError, FileNotFoundError)),
       stop=stop_after_attempt(5),
       before_sleep=before_sleep_log(logger, logging.WARN))
async def get_song_lyrics(song_id: str, storefront: str, token: str, dsid: str, account_token: str) -> str:
    req = await client.get(f"https://amp-api.music.apple.com/v1/catalog/{storefront}/songs/{song_id}/lyrics",
                           headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent_app,
                                    "X-Dsid": dsid},
                           cookies={f"mz_at_ssl-{dsid}": account_token})
    result = SongLyrics.model_validate(req.json())
    return result.data[0].attributes.ttml
