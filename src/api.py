import asyncio
from io import BytesIO
from ssl import SSLError
from typing import AsyncIterator, Optional, Type

import httpx
import regex
from creart import AbstractCreator, CreateTargetInfo, exists_module, it
from httpx import Request, Response, AsyncHTTPTransport, AsyncClient
from tenacity import retry, retry_if_exception_type, wait_random_exponential, stop_after_attempt, before_sleep_log

from src.config import Config
from src.logger import GlobalLogger
from src.measurer import Measurer
from src.models import *


class NameSolver:
    def get(self, name: str) -> str:
        if name == "aod.itunes.apple.com":
            return it(Config).download.appleCDNIP
        return ''

    def resolve(self, request: Request) -> Request:
        host = request.url.host
        ip = self.get(host)

        if ip:
            request.extensions["sni_hostname"] = host
            request.url = request.url.copy_with(host=ip)

        return request


class AsyncCustomHost(AsyncHTTPTransport):
    def __init__(self, solver: NameSolver, *args, **kwargs) -> None:
        self.solver = solver
        super().__init__(*args, **kwargs)

    async def handle_async_request(self, request: Request) -> Response:
        request = self.solver.resolve(request)
        return await super().handle_async_request(request)


class WebAPI:
    client: httpx.AsyncClient
    download_client: Optional[httpx.AsyncClient]
    download_lock: asyncio.Semaphore
    request_lock: asyncio.Semaphore
    token: str

    # Aggregated chunk size for CDN streaming: bounds the number of
    # Python-layer callbacks (Measurer / parser.feed) per second without
    # affecting throughput.  1 MiB measured 63x fewer chunks at equal speed.
    DOWNLOAD_CHUNK_SIZE = 1024 * 1024

    def __init__(self, proxy: str, parallel_num: int):
        self._set_token()
        self.client = AsyncClient(headers={"Authorization": f"Bearer {self.token}",
                                           "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                                           "Origin": "https://music.apple.com"},
                                  proxy=proxy if proxy else None)
        # Shared streaming client for CDN downloads.  Lazily created on the
        # first stream_song call (needs a running loop for the transport);
        # keepalive connections are REUSED across songs — measured 80% lower
        # TTFB and several-fold higher steady-state throughput versus
        # creating a fresh AsyncClient per song (which forced a TCP+TLS
        # handshake plus TCP slow-start ramp for every track).
        self.download_client = None
        self.download_proxy = proxy if proxy else None
        self.download_lock = asyncio.Semaphore(parallel_num)
        self.request_lock = asyncio.Semaphore(256)

    def _get_download_client(self) -> httpx.AsyncClient:
        """Return the shared CDN download client, creating it on first use."""
        if self.download_client is None or self.download_client.is_closed:
            timeout_sec = float(it(Config).download.downloadTimeout or 60.0)
            timeout = httpx.Timeout(15.0, read=timeout_sec, connect=15.0, pool=60.0)
            self.download_client = httpx.AsyncClient(
                transport=AsyncCustomHost(NameSolver()),
                timeout=timeout,
                # Keep idle keepalive connections open between songs.
                limits=httpx.Limits(max_connections=64,
                                    max_keepalive_connections=16,
                                    keepalive_expiry=120.0),
            )
        return self.download_client

    @retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
           wait=wait_random_exponential(multiplier=1, max=it(Config).download.maxWaitTime),
           stop=stop_after_attempt(it(Config).download.retryTime))
    def _set_token(self):
        with httpx.Client() as client:
            resp = client.get("https://music.apple.com", follow_redirects=True)
            index_js_uri = regex.findall(r"/assets/index~[^/]+\.js", resp.text)[0]
            js_resp = client.get("https://music.apple.com" + index_js_uri)
            self.token = regex.search(r'(eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+)', js_resp.text)[0]

    # DO NOT REMOVE IT
    def init(self):
        pass

    @retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
           wait=wait_random_exponential(multiplier=1, max=it(Config).download.maxWaitTime),
           stop=stop_after_attempt(it(Config).download.retryTime),
           before_sleep=before_sleep_log(it(GlobalLogger).logger, "WARNING"))
    async def _request(self, *args, **kwargs):
        async with self.request_lock:
            return await self.client.request(*args, **kwargs)

    @retry(retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
           wait=wait_random_exponential(multiplier=1, max=it(Config).download.maxWaitTime),
           stop=stop_after_attempt(it(Config).download.retryTime),
           before_sleep=before_sleep_log(it(GlobalLogger).logger, "WARNING"))
    async def _download_song_internal(self, url: str) -> bytes:
        result = BytesIO()
        timeout = httpx.Timeout(15.0, read=60.0, connect=15.0, pool=20.0)
        async with httpx.AsyncClient(transport=AsyncCustomHost(NameSolver()), timeout=timeout) as client:
            async with client.stream('GET', url) as response:
                total = int(response.headers.get("Content-Length") if response.headers.get("Content-Length")
                            else response.headers.get("X-Apple-MS-Content-Length"))
                async for chunk in response.aiter_bytes():
                    it(Measurer).record_download(len(chunk))
                    result.write(chunk)
            if len(result.getvalue()) != total:
                raise httpx.HTTPError
            return result.getvalue()

    async def download_song(self, url: str) -> bytes:
        async with self.download_lock:
            return await self._download_song_internal(url)

    async def stream_song(self, url: str, resume_from: int = 0,
                          extra_headers: dict = None) -> AsyncIterator[bytes]:
        """Stream the encrypted media file from the Apple CDN (边下边解).

        Applies CDN IP pinning, holds the download semaphore for the whole
        stream and yields bounded chunks. Transport / idle-timeout errors
        propagate as httpx errors so the caller can resume from its parser's
        last complete box boundary by passing ``resume_from`` (a byte offset).
        ``extra_headers`` may carry an explicit ``Range`` (byte-range media
        segments). A ``416`` on a resume request means the file is fully read.
        """
        async with self.download_lock:
            client = self._get_download_client()
            headers = dict(extra_headers or {})
            if resume_from and "Range" not in headers:
                headers["Range"] = f"bytes={resume_from}-"
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 416 and (resume_from or "Range" in headers):
                    return
                response.raise_for_status()
                async for chunk in response.aiter_bytes(self.DOWNLOAD_CHUNK_SIZE):
                    it(Measurer).record_download(len(chunk))
                    yield chunk

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
        req = await self._request("GET",
                                  f"https://amp-api.music.apple.com/v1/catalog/{storefront}/albums/{album_id}/tracks?offset={offset}")
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
        exist = await self.song_exist(song_id, check_storefront)
        return exist

    async def exist_on_storefront_by_album_id(self, album_id: str, storefront: str, check_storefront: str):
        if storefront.upper() == check_storefront.upper():
            return True
        exist = await self.album_exist(album_id, check_storefront)
        return exist


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
