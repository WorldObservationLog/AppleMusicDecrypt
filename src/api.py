import asyncio
import logging
from io import BytesIO
from ssl import SSLError
from typing import Optional

import httpx
import regex
from async_lru import alru_cache
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, before_sleep_log, wait_random_exponential

from src.models import *
from src.models.song_data import Datum

client: httpx.AsyncClient
download_lock: asyncio.Semaphore
request_lock: asyncio.Semaphore
retry_times = 32
user_agent_browser = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
user_agent_itunes = "iTunes/12.11.3 (Windows; Microsoft Windows 10 x64 Professional Edition (Build 19041); x64) AppleWebKit/7611.1022.4001.1 (dt:2)"
user_agent_app = "Music/5.7 Android/10 model/Pixel6GR1YH build/1234 (dt:66)"


def init_client_and_lock(proxy: str, parallel_num: int):
    global client, download_lock, request_lock
    if proxy:
        client = httpx.AsyncClient(proxy=proxy)
    else:
        client = httpx.AsyncClient()
    download_lock = asyncio.Semaphore(parallel_num)
    request_lock = asyncio.Semaphore(256)


@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def get_m3u8_from_api(endpoint: str, song_id: str, wait_and_retry: bool = False, recursion_times: int = 0) -> str:
    async with request_lock:
        resp = (await client.get(endpoint, params={"songid": song_id})).text
        if resp == "no_found":
            if wait_and_retry and recursion_times <= 5:
                await asyncio.sleep(5)
                return await get_m3u8_from_api(endpoint, song_id, recursion_times=recursion_times + 1)
            return ""
        return resp


@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def upload_m3u8_to_api(endpoint: str, m3u8_url: str, song_info: Datum):
    async with request_lock:
        await client.post(endpoint, json={
            "method": "add_m3u8",
            "params": {
                "songid": song_info.id,
                "song_title": f"Disk {song_info.attributes.discNumber} Track {song_info.attributes.trackNumber} - {song_info.attributes.name}",
                "albumid": song_info.relationships.albums.data[0].id,
                "album_title": song_info.attributes.albumName,
                "m3u8": m3u8_url,
            }
        })


@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def get_token():
    async with request_lock:
        req = await client.get("https://beta.music.apple.com", follow_redirects=True)
        index_js_uri = regex.findall(r"/assets/index-legacy-[^/]+\.js", req.text)[0]
        js_req = await client.get("https://beta.music.apple.com" + index_js_uri)
        token = regex.search(r'eyJh([^"]*)', js_req.text)[0]
        return token


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def download_song(url: str) -> bytes:
    async with download_lock:
        result = BytesIO()
        async with client.stream('GET', url) as response:
            total = int(response.headers["Content-Length"])
            async for chunk in response.aiter_bytes():
                result.write(chunk)
        if len(result.getvalue()) != total:
            raise httpx.HTTPError
        return result.getvalue()


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def get_album_info(album_id: str, token: str, storefront: str, lang: str):
    async with request_lock:
        req = await client.get(f"https://amp-api.music.apple.com/v1/catalog/{storefront}/albums/{album_id}",
                               params={"omit[resource]": "autos", "include": "tracks,artists,record-labels",
                                       "include[songs]": "artists", "fields[artists]": "name",
                                       "fields[albums:albums]": "artistName,artwork,name,releaseDate,url",
                                       "fields[record-labels]": "name", "l": lang},
                               headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent_browser,
                                        "Origin": "https://music.apple.com"})
        return AlbumMeta.model_validate(req.json())


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def get_playlist_info_and_tracks(playlist_id: str, token: str, storefront: str, lang: str):
    async with request_lock:
        resp = await client.get(f"https://amp-api.music.apple.com/v1/catalog/{storefront}/playlists/{playlist_id}",
                                params={"l": lang},
                                headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent_browser,
                                         "Origin": "https://music.apple.com"})
        playlist_info_obj = PlaylistInfo.parse_obj(resp.json())
        if playlist_info_obj.data[0].relationships.tracks.next:
            all_tracks = await get_playlist_tracks(playlist_id, token, storefront, lang)
            playlist_info_obj.data[0].relationships.tracks.data = all_tracks
        return playlist_info_obj


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def get_playlist_tracks(playlist_id: str, token: str, storefront: str, lang: str, offset: int = 0):
    async with request_lock:
        resp = await client.get(
            f"https://amp-api.music.apple.com/v1/catalog/{storefront}/playlists/{playlist_id}/tracks",
            params={"l": lang, "offset": offset},
            headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent_browser,
                     "Origin": "https://music.apple.com"})
        playlist_tracks = PlaylistTracks.parse_obj(resp.json())
        tracks = playlist_tracks.data
        if playlist_tracks.next:
            next_tracks = await get_playlist_tracks(playlist_id, token, storefront, lang, offset + 100)
            tracks.extend(next_tracks)
        return tracks


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def get_cover(url: str, cover_format: str, cover_size: str):
    async with request_lock:
        formatted_url = regex.sub('bb.jpg', f'bb.{cover_format}', url)
        req = await client.get(formatted_url.replace("{w}x{h}", cover_size),
                               headers={"User-Agent": user_agent_browser})
        return req.content


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def get_song_info(song_id: str, token: str, storefront: str, lang: str):
    async with request_lock:
        req = await client.get(f"https://amp-api.music.apple.com/v1/catalog/{storefront}/songs/{song_id}",
                               params={"extend": "extendedAssetUrls", "include": "albums,explicit", "l": lang},
                               headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent_itunes,
                                        "Origin": "https://music.apple.com"})
        song_data_obj = SongData.model_validate(req.json())
        for data in song_data_obj.data:
            if data.id == song_id:
                return data
        return None


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def get_song_lyrics(song_id: str, storefront: str, token: str, dsid: str, account_token: str, lang: str) -> Optional[str]:
    async with request_lock:
        req = await client.get(f"https://amp-api.music.apple.com/v1/catalog/{storefront}/songs/{song_id}/lyrics",
                               params={"l": lang},
                               headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent_app,
                                        "X-Dsid": dsid},
                               cookies={f"mz_at_ssl-{dsid}": account_token})
        result = SongLyrics.model_validate(req.json())
        if result.data:
            return result.data[0].attributes.ttml
        else:
            return None


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def get_albums_from_artist(artist_id: str, storefront: str, token: str, lang: str, offset: int = 0):
    async with request_lock:
        resp = await client.get(f"https://amp-api.music.apple.com/v1/catalog/{storefront}/artists/{artist_id}/albums",
                                params={"l": lang, "offset": offset},
                                headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent_browser,
                                         "Origin": "https://music.apple.com"})
        artist_album = ArtistAlbums.parse_obj(resp.json())
        albums = [album.attributes.url for album in artist_album.data]
        if artist_album.next:
            next_albums = await get_albums_from_artist(artist_id, storefront, token, lang, offset + 25)
            albums.extend(next_albums)
        return list(set(albums))


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def get_songs_from_artist(artist_id: str, storefront: str, token: str, lang: str, offset: int = 0):
    async with request_lock:
        resp = await client.get(f"https://amp-api.music.apple.com/v1/catalog/{storefront}/artists/{artist_id}/songs",
                                params={"l": lang, "offset": offset},
                                headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent_browser,
                                         "Origin": "https://music.apple.com"})
        artist_song = ArtistSongs.parse_obj(resp.json())
        songs = [song.attributes.url for song in artist_song.data]
        if artist_song.next:
            next_songs = await get_songs_from_artist(artist_id, storefront, token, lang, offset + 20)
            songs.extend(next_songs)
        return list(set(songs))


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def get_artist_info(artist_id: str, storefront: str, token: str, lang: str):
    async with request_lock:
        resp = await client.get(f"https://amp-api.music.apple.com/v1/catalog/{storefront}/artists/{artist_id}",
                                params={"l": lang},
                                headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent_browser,
                                         "Origin": "https://music.apple.com"})
        return ArtistInfo.parse_obj(resp.json())


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def download_m3u8(m3u8_url: str) -> str:
    async with request_lock:
        resp = await client.get(m3u8_url)
        return resp.text


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def get_real_url(url: str):
    req = await client.get(url, follow_redirects=True, headers={"User-Agent": user_agent_browser})
    return str(req.url)


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def get_album_by_upc(upc: str, storefront: str, token: str):
    req = await client.get(f"https://amp-api.music.apple.com/v1/catalog/{storefront}/albums",
                           params={"filter[upc]": upc},
                           headers={"Authorization": f"Bearer {token}", "Origin": "https://music.apple.com"})
    resp = req.json()
    try:
        if resp["data"]:
            return req.json()
        else:
            return None
    except KeyError:
        logger.debug(f"UPC: {upc}, Storefront: {storefront}")
        return None


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def exist_on_storefront_by_song_id(song_id: str, storefront: str, check_storefront: str, token: str, lang: str):
    if storefront.upper() == check_storefront.upper():
        return True
    song = await get_song_info(song_id, token, storefront, lang)
    album_id = song.relationships.albums.data[0].id
    album = await get_album_info(album_id, token, storefront, lang)
    upc = album.data[0].attributes.upc
    upc_result = await get_album_by_upc(upc, check_storefront, token)
    return bool(upc_result)


@alru_cache
@retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
       wait=wait_random_exponential(multiplier=1, max=60),
       stop=stop_after_attempt(retry_times), before_sleep=before_sleep_log(logger, logging.WARN))
async def exist_on_storefront_by_album_id(album_id: str, storefront: str, check_storefront: str, token: str, lang: str):
    if storefront.upper() == check_storefront.upper():
        return True
    album = await get_album_info(album_id, token, storefront, lang)
    upc = album.data[0].attributes.upc
    upc_result = await get_album_by_upc(upc, check_storefront, token)
    return bool(upc_result)
