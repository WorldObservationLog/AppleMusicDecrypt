import asyncio
import subprocess

from loguru import logger

from src.api import (get_song_info, get_song_lyrics, get_album_info, download_song,
                     get_m3u8_from_api, get_artist_info, get_songs_from_artist, get_albums_from_artist,
                     get_playlist_info_and_tracks)
from src.config import Config, Device
from src.decrypt import decrypt
from src.metadata import SongMetadata
from src.models import PlaylistInfo
from src.mp4 import extract_media, extract_song, encapsulate, write_metadata
from src.save import save
from src.types import GlobalAuthParams, Codec
from src.url import Song, Album, URLType, Artist, Playlist
from src.utils import check_song_exists, if_raw_atmos


@logger.catch
async def rip_song(song: Song, auth_params: GlobalAuthParams, codec: str, config: Config, device: Device,
                   force_save: bool = False, specified_m3u8: str = "", playlist: PlaylistInfo = None):
    logger.debug(f"Task of song id {song.id} was created")
    token = auth_params.anonymousAccessToken
    song_data = await get_song_info(song.id, token, song.storefront, config.region.language)
    song_metadata = SongMetadata.parse_from_song_data(song_data)
    logger.info(f"Ripping song: {song_metadata.artist} - {song_metadata.title}")
    if not force_save and check_song_exists(song_metadata, config.download, codec, playlist):
        logger.info(f"Song: {song_metadata.artist} - {song_metadata.title} already exists")
        return
    await song_metadata.get_cover(config.download.coverFormat, config.download.coverSize)
    if song_data.attributes.hasTimeSyncedLyrics:
        lyrics = await get_song_lyrics(song.id, song.storefront, auth_params.accountAccessToken,
                                       auth_params.dsid, auth_params.accountToken, config.region.language)
        song_metadata.lyrics = lyrics
    if config.m3u8Api.enable and codec == Codec.ALAC:
        m3u8_url = await get_m3u8_from_api(config.m3u8Api.endpoint, song.id)
        if m3u8_url:
            specified_m3u8 = m3u8_url
            logger.info(f"Use m3u8 from API for song: {song_metadata.artist} - {song_metadata.title}")
    if specified_m3u8:
        song_uri, keys = await extract_media(specified_m3u8, codec, song_metadata,
                                             config.download.codecPriority, config.download.codecAlternative)
    else:
        song_uri, keys = await extract_media(song_data.attributes.extendedAssetUrls.enhancedHls, codec, song_metadata,
                                             config.download.codecPriority, config.download.codecAlternative)
    logger.info(f"Downloading song: {song_metadata.artist} - {song_metadata.title}")
    raw_song = await download_song(song_uri)
    song_info = extract_song(raw_song, codec)
    decrypted_song = await decrypt(song_info, keys, song_data, device)
    song = encapsulate(song_info, decrypted_song, config.download.atmosConventToM4a)
    if not if_raw_atmos(codec, config.download.atmosConventToM4a):
        song = write_metadata(song, song_metadata, config.metadata.embedMetadata, config.download.coverFormat)
    filename = save(song, codec, song_metadata, config.download, playlist)
    logger.info(f"Song {song_metadata.artist} - {song_metadata.title} saved!")
    if config.download.afterDownloaded:
        command = config.download.afterDownloaded.format(filename=filename)
        logger.info(f"Executing command: {command}")
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def rip_album(album: Album, auth_params: GlobalAuthParams, codec: str, config: Config, device: Device,
                    force_save: bool = False):
    album_info = await get_album_info(album.id, auth_params.anonymousAccessToken, album.storefront,
                                      config.region.language)
    logger.info(f"Ripping Album: {album_info.data[0].attributes.artistName} - {album_info.data[0].attributes.name}")
    async with asyncio.TaskGroup() as tg:
        for track in album_info.data[0].relationships.tracks.data:
            song = Song(id=track.id, storefront=album.storefront, url="", type=URLType.Song)
            tg.create_task(rip_song(song, auth_params, codec, config, device, force_save=force_save))
    logger.info(
        f"Album: {album_info.data[0].attributes.artistName} - {album_info.data[0].attributes.name} finished ripping")


async def rip_playlist(playlist: Playlist, auth_params: GlobalAuthParams, codec: str, config: Config, device: Device,
                       force_save: bool = False):
    playlist_info = await get_playlist_info_and_tracks(playlist.id, auth_params.anonymousAccessToken, playlist.storefront,
                                                       config.region.language)
    logger.info(f"Ripping Playlist: {playlist_info.data[0].attributes.curatorName} - {playlist_info.data[0].attributes.name}")
    async with asyncio.TaskGroup() as tg:
        for track in playlist_info.data[0].relationships.tracks.data:
            song = Song(id=track.id, storefront=playlist.storefront, url="", type=URLType.Song)
            tg.create_task(rip_song(song, auth_params, codec, config, device, force_save=force_save, playlist=playlist_info))
    logger.info(
        f"Playlist: {playlist_info.data[0].attributes.curatorName} - {playlist_info.data[0].attributes.name} finished ripping")


async def rip_artist(artist: Artist, auth_params: GlobalAuthParams, codec: str, config: Config, device: Device,
                     force_save: bool = False, include_participate_in_works: bool = False):
    artist_info = await get_artist_info(artist.id, artist.storefront, auth_params.anonymousAccessToken,
                                        config.region.language)
    logger.info(f"Ripping Artist: {artist_info.data[0].attributes.name}")
    async with asyncio.TaskGroup() as tg:
        if include_participate_in_works:
            songs = await get_songs_from_artist(artist.id, artist.storefront, auth_params.anonymousAccessToken,
                                                config.region.language)
            for song_url in songs:
                tg.create_task(rip_song(Song.parse_url(song_url), auth_params, codec, config, device, force_save))
        else:
            albums = await get_albums_from_artist(artist.id, artist.storefront, auth_params.anonymousAccessToken,
                                                  config.region.language)
            for album_url in albums:
                tg.create_task(rip_album(Album.parse_url(album_url), auth_params, codec, config, device, force_save))
    logger.info(f"Artist: {artist_info.data[0].attributes.name} finished ripping")
