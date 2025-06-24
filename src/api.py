import asyncio
import logging
from io import BytesIO
from ssl import SSLError
from typing import Type

import hishel
import httpx
import regex
from creart import AbstractCreator, CreateTargetInfo, exists_module, it
from tenacity import retry, retry_if_exception_type, wait_random_exponential, stop_after_attempt, before_sleep_log

from src.config import Config
from src.logger import GlobalLogger
from src.measurer import SpeedMeasurer
from src.models import *


class WebAPI:
    client: httpx.AsyncClient
    download_lock: asyncio.Semaphore
    request_lock: asyncio.Semaphore
    token: str

    def __init__(self, proxy: str, parallel_num: int):
        self._set_token()
        self.client = hishel.AsyncCacheClient(headers={"Authorization": f"Bearer {self.token}",
                                                       "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                                                       "Origin": "https://music.apple.com"},
                                              proxy=proxy if proxy else None)
        self.download_lock = asyncio.Semaphore(parallel_num)
        self.request_lock = asyncio.Semaphore(256)

    @retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
           wait=wait_random_exponential(multiplier=1, max=60),
           stop=stop_after_attempt(32))
    def _set_token(self):
        with httpx.Client() as client:
            resp = client.get("https://beta.music.apple.com", follow_redirects=True)
            index_js_uri = regex.findall(r"/assets/index-legacy-[^/]+\.js", resp.text)[0]
            js_resp = client.get("https://beta.music.apple.com" + index_js_uri)
            self.token = regex.search(r'eyJh([^"]*)', js_resp.text)[0]

    # DO NOT REMOVE IT
    def init(self):
        pass

    @retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
           wait=wait_random_exponential(multiplier=1, max=60),
           stop=stop_after_attempt(32), before_sleep=before_sleep_log(it(GlobalLogger).logger, "WARNING"))
    async def _request(self, *args, **kwargs):
        async with self.request_lock:
            return await self.client.request(*args, **kwargs)

    @retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
           wait=wait_random_exponential(multiplier=1, max=60),
           stop=stop_after_attempt(32), before_sleep=before_sleep_log(it(GlobalLogger).logger, "WARNING"))
    async def download_song(self, url: str) -> bytes:
        async with self.download_lock:
            result = BytesIO()
            async with self.client.stream('GET', url) as response:
                total = int(response.headers.get("Content-Length") if response.headers.get("Content-Length")
                            else response.headers.get("X-Apple-MS-Content-Length"))
                async for chunk in response.aiter_bytes():
                    it(SpeedMeasurer).record_download(len(chunk))
                    result.write(chunk)
            if len(result.getvalue()) != total:
                raise httpx.HTTPError
            return result.getvalue()

    async def get_album_info(self, album_id: str, storefront: str, lang: str):
        req = await self._request("GET",
                                  f"https://amp-api.music.apple.com/v1/catalog/{storefront}/albums/{album_id}",
                                  params={"omit[resource]": "autos", "include": "tracks,artists,record-labels",
                                          "include[songs]": "artists", "fields[artists]": "name",
                                          "fields[albums:albums]": "artistName,artwork,name,releaseDate,url",
                                          "fields[record-labels]": "name", "l": lang})
        album_info_obj = AlbumMeta.model_validate(req.json())
        if album_info_obj.data[0].relationships.tracks.next:
            all_tracks = await self.get_album_tracks(album_id, storefront, lang)
            album_info_obj.data[0].relationships.tracks.data = all_tracks
        return album_info_obj

    async def get_album_tracks(self, album_id: str, storefront: str, lang: str, offset: int = 0):
        req = await self._request("GET", f"https://amp-api.music.apple.com/v1/catalog/{storefront}/albums/{album_id}/tracks?offset={offset}")
        album_info_obj = AlbumTracks.model_validate(req.json())
        tracks = album_info_obj.data
        if album_info_obj.next:
            next_tracks = await self.get_album_tracks(album_id, storefront, lang, offset + 300)
            tracks.extend(next_tracks)
        return tracks

    async def get_playlist_info_and_tracks(self, playlist_id: str, storefront: str, lang: str):
        resp = await self._request("GET",
                                   f"https://amp-api.music.apple.com/v1/catalog/{storefront}/playlists/{playlist_id}",
                                   params={"l": lang})
        playlist_info_obj = PlaylistInfo.model_validate(resp.json())
        if playlist_info_obj.data[0].relationships.tracks.next:
            all_tracks = await self.get_playlist_tracks(playlist_id, storefront, lang)
            playlist_info_obj.data[0].relationships.tracks.data = all_tracks
        return playlist_info_obj

    async def get_playlist_tracks(self, playlist_id: str, storefront: str, lang: str, offset: int = 0):
        resp = await self._request("GET",
                                   f"https://amp-api.music.apple.com/v1/catalog/{storefront}/playlists/{playlist_id}/tracks",
                                   params={"l": lang, "offset": offset})
        playlist_tracks = PlaylistTracks.model_validate(resp.json())
        tracks = playlist_tracks.data
        if playlist_tracks.next:
            next_tracks = await self.get_playlist_tracks(playlist_id, storefront, lang, offset + 100)
            tracks.extend(next_tracks)
        return tracks

    async def get_cover(self, url: str, cover_format: str, cover_size: str):
        async with self.request_lock:
            formatted_url = regex.sub('bb.jpg', f'bb.{cover_format}', url)
            req = await self._request("GET", formatted_url.replace("{w}x{h}", cover_size))
            return req.content

    async def get_song_info(self, song_id: str, storefront: str, lang: str):
        req = await self._request("GET", f"https://amp-api.music.apple.com/v1/catalog/{storefront}/songs/{song_id}",
                                  params={"extend": "extendedAssetUrls", "include": "albums,explicit", "l": lang})
        song_data_obj = SongData.model_validate(req.json())
        for data in song_data_obj.data:
            if data.id == song_id:
                return data
        return None

    async def song_exist(self, song_id: str, storefront: str):
        req = await self._request("HEAD", f"https://amp-api.music.apple.com/v1/catalog/{storefront}/songs/{song_id}")
        if req.status_code == 200:
            return True
        return False

    async def album_exist(self, album_id: str, storefront: str):
        req = await self._request("HEAD", f"https://amp-api.music.apple.com/v1/catalog/{storefront}/albums/{album_id}")
        if req.status_code == 200:
            return True
        return False

    async def get_albums_from_artist(self, artist_id: str, storefront: str, lang: str, offset: int = 0):
        resp = await self._request("GET",
                                   f"https://amp-api.music.apple.com/v1/catalog/{storefront}/artists/{artist_id}/albums",
                                   params={"l": lang, "offset": offset})
        artist_album = ArtistAlbums.model_validate(resp.json())
        albums = [album.attributes.url for album in artist_album.data]
        if artist_album.next:
            next_albums = await self.get_albums_from_artist(artist_id, storefront, lang, offset + 25)
            albums.extend(next_albums)
        return list(set(albums))

    async def get_songs_from_artist(self, artist_id: str, storefront: str, lang: str, offset: int = 0):
        resp = await self._request("GET",
                                   f"https://amp-api.music.apple.com/v1/catalog/{storefront}/artists/{artist_id}/songs",
                                   params={"l": lang, "offset": offset})
        artist_song = ArtistSongs.model_validate(resp.json())
        songs = [song.attributes.url for song in artist_song.data]
        if artist_song.next:
            next_songs = await self.get_songs_from_artist(artist_id, storefront, lang, offset + 20)
            songs.extend(next_songs)
        return list(set(songs))

    async def get_artist_info(self, artist_id: str, storefront: str, lang: str):
        resp = await self._request("GET",
                                   f"https://amp-api.music.apple.com/v1/catalog/{storefront}/artists/{artist_id}",
                                   params={"l": lang})
        return ArtistInfo.model_validate(resp.json())

    async def download_m3u8(self, m3u8_url: str) -> str:
        resp = await self._request("GET", m3u8_url)
        return resp.text

    async def get_real_url(self, url: str):
        req = await self._request("GET", url, follow_redirects=True)
        return str(req.url)

    async def get_album_by_upc(self, upc: str, storefront: str):
        req = await self._request("GET", f"https://amp-api.music.apple.com/v1/catalog/{storefront}/albums",
                                  params={"filter[upc]": upc})
        resp = req.json()
        try:
            if resp["data"]:
                return req.json()
            else:
                return None
        except KeyError:
            return None

    async def exist_on_storefront_by_song_id(self, song_id: str, storefront: str, check_storefront: str):
        if storefront.upper() == check_storefront.upper():
            return True
        song = await self.song_exist(song_id, check_storefront)
        return bool(song)

    async def exist_on_storefront_by_album_id(self, album_id: str, storefront: str, check_storefront: str):
        if storefront.upper() == check_storefront.upper():
            return True
        album = await self.album_exist(album_id, check_storefront)
        return bool(album)


class APICreator(AbstractCreator):
    targets = (
        CreateTargetInfo("src.api", "WebAPI"),
    )

    @staticmethod
    def available() -> bool:
        return exists_module("src.api")

    @staticmethod
    def create(create_type: Type[WebAPI]) -> WebAPI:
        return create_type(it(Config).download.proxy, it(Config).download.parallelNum)
