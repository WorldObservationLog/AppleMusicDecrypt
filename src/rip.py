import asyncio
import random
import subprocess
from typing import Optional

from loguru import logger

from src.api import (get_song_info, get_song_lyrics, get_album_info, download_song,
                     get_m3u8_from_api, get_artist_info, get_songs_from_artist, get_albums_from_artist,
                     get_playlist_info_and_tracks, exist_on_storefront_by_album_id, exist_on_storefront_by_song_id)
from src.config import Config
from src.adb import Device
from src.decrypt import decrypt
from src.metadata import SongMetadata
from src.models import PlaylistInfo
from src.mp4 import extract_media, extract_song, encapsulate, write_metadata, fix_encapsulate, fix_esds_box
from src.save import save
from src.status import BaseStatus, StatusCode, ErrorCode, WarningCode
from src.types import GlobalAuthParams, Codec
from src.url import Song, Album, URLType, Artist, Playlist
from src.utils import check_song_exists, if_raw_atmos, playlist_write_song_index, get_codec_from_codec_id, timeit

task_lock = asyncio.Semaphore(16)


@logger.catch
@timeit
async def rip_song(song: Song, auth_params: GlobalAuthParams, codec: str, config: Config, device: Device, status: BaseStatus,
                   force_save: bool = False, specified_m3u8: str = "", playlist: PlaylistInfo = None, return_result: bool = False) -> Optional[tuple[bytes, SongMetadata, str]]:
    async with task_lock:
        status.set_param(song_id=song.id)
        status.set_status(StatusCode.Processing)
        token = auth_params.anonymousAccessToken
        song_data = await get_song_info(song.id, token, song.storefront, config.region.language)
        song_metadata = SongMetadata.parse_from_song_data(song_data)
        status.set_param(artist=song_metadata.artist, title=song_metadata.title,
                         song_storefront=song.storefront, storefront=auth_params.storefront)
        if playlist:
            song_metadata.set_playlist_index(playlist.songIdIndexMapping.get(song.id))
        status.set_status(StatusCode.Parsing)
        if not await exist_on_storefront_by_song_id(song.id, song.storefront, auth_params.storefront,
                                                    auth_params.anonymousAccessToken, config.region.language):
            status.set_status(ErrorCode.NotExistInStorefront)
            return
        if not force_save and check_song_exists(song_metadata, config.download, codec, playlist) and not return_result:
            status.set_status(StatusCode.AlreadyExist)
            return
        await song_metadata.get_cover(config.download.coverFormat, config.download.coverSize)
        if song_data.attributes.hasTimeSyncedLyrics:
            if song.storefront.upper() != auth_params.storefront.upper():
                status.set_warning(WarningCode.NoAvailableAccountForLyrics)
            lyrics = await get_song_lyrics(song.id, auth_params.storefront, auth_params.accountAccessToken,
                                           auth_params.dsid, auth_params.accountToken, config.region.language)
            if lyrics:
                song_metadata.lyrics = lyrics
            else:
                status.set_warning(WarningCode.UnableGetLyrics)
        if config.m3u8Api.enable and codec == Codec.ALAC and not specified_m3u8:
            m3u8_url = await get_m3u8_from_api(config.m3u8Api.endpoint, song.id, config.m3u8Api.enable)
            if m3u8_url:
                specified_m3u8 = m3u8_url
                logger.info(f"Use m3u8 from API for song: {song_metadata.artist} - {song_metadata.title}")
            elif not m3u8_url and config.m3u8Api.force:
                status.set_error(ErrorCode.ForceModeM3U8NotExist)
                return
        if not song_data.attributes.extendedAssetUrls:
            status.set_error(ErrorCode.AudioNotExist)
            return
        if not specified_m3u8 and not song_data.attributes.extendedAssetUrls.enhancedHls:
            status.set_error(ErrorCode.LosslessAudioNotExist)
            return
        if not specified_m3u8:
            device_m3u8 = await device.get_m3u8(song.id)
            if device_m3u8:
                specified_m3u8 = device_m3u8
                logger.info(f"Use m3u8 from device for song: {song_metadata.artist} - {song_metadata.title}")
        if specified_m3u8:
            song_uri, keys, codec_id = await extract_media(specified_m3u8, codec, song_metadata,
                                                           config.download.codecPriority,
                                                           config.download.codecAlternative)
        else:
            song_uri, keys, codec_id = await extract_media(song_data.attributes.extendedAssetUrls.enhancedHls, codec,
                                                           song_metadata,
                                                           config.download.codecPriority,
                                                           config.download.codecAlternative)
        status.set_param(codec=codec_id)
        codec = get_codec_from_codec_id(codec_id)
        raw_song = await download_song(song_uri, status)
        song_info = await extract_song(raw_song, codec)
        if device.hyperDecryptDevices:
            if all([hyper_device.decryptLock.locked() for hyper_device in device.hyperDecryptDevices]):
                decrypted_song = await decrypt(song_info, keys, song_data, random.choice(device.hyperDecryptDevices), status)
            else:
                for hyperDecryptDevice in device.hyperDecryptDevices:
                    if not hyperDecryptDevice.decryptLock.locked():
                        decrypted_song = await decrypt(song_info, keys, song_data, hyperDecryptDevice, status)
                        break
        else:
            decrypted_song = await decrypt(song_info, keys, song_data, device, status)
        status.set_status(StatusCode.Saving)
        song = await encapsulate(song_info, decrypted_song, config.download.atmosConventToM4a)
        if not if_raw_atmos(codec, config.download.atmosConventToM4a):
            metadata_song = await write_metadata(song, song_metadata, config.metadata.embedMetadata,
                                                 config.download.coverFormat)
            song = await fix_encapsulate(metadata_song)
            if codec == Codec.AAC or codec == Codec.AAC_DOWNMIX or codec == Codec.AAC_BINAURAL:
                song = await fix_esds_box(song_info.raw, song)
        if return_result:
            status.set_status(StatusCode.Done)
            return song, song_metadata, codec
        else:
            filename = await save(song, codec, song_metadata, config.download, playlist)
            status.set_status(StatusCode.Done)
            if config.download.afterDownloaded:
                command = config.download.afterDownloaded.format(filename=filename)
                logger.info(f"Executing command: {command}")
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@logger.catch
@timeit
async def rip_album(album: Album, auth_params: GlobalAuthParams, codec: str, config: Config, device: Device, status: BaseStatus,
                    force_save: bool = False):
    album_info = await get_album_info(album.id, auth_params.anonymousAccessToken, album.storefront,
                                      config.region.language)
    status.set_param(artist=album_info.data[0].attributes.artistName, title=album_info.data[0].attributes.name,
                     storefront=auth_params.storefront)
    status.set_status(StatusCode.Processing)
    if not await exist_on_storefront_by_album_id(album.id, album.storefront, auth_params.storefront,
                                                 auth_params.anonymousAccessToken, config.region.language):
        status.set_error(ErrorCode.NotExistInStorefront)
        return
    async with asyncio.TaskGroup() as tg:
        for track in album_info.data[0].relationships.tracks.data:
            song_status = status.new(URLType.Song)
            status.children.append(song_status)
            song = Song(id=track.id, storefront=album.storefront, url="", type=URLType.Song)
            tg.create_task(rip_song(song, auth_params, codec, config, device, song_status, force_save=force_save))
    status.set_status(StatusCode.Done)


@logger.catch
@timeit
async def rip_playlist(playlist: Playlist, auth_params: GlobalAuthParams, codec: str, config: Config, device: Device, status: BaseStatus,
                       force_save: bool = False):
    playlist_info = await get_playlist_info_and_tracks(playlist.id, auth_params.anonymousAccessToken,
                                                       playlist.storefront,
                                                       config.region.language)
    playlist_info = playlist_write_song_index(playlist_info)
    status.set_param(artist=playlist_info.data[0].attributes.curatorName, title=playlist_info.data[0].attributes.name)
    status.set_status(StatusCode.Processing)
    async with asyncio.TaskGroup() as tg:
        for track in playlist_info.data[0].relationships.tracks.data:
            song_status = status.new(URLType.Song)
            status.children.append(song_status)
            song = Song(id=track.id, storefront=playlist.storefront, url="", type=URLType.Song)
            tg.create_task(
                rip_song(song, auth_params, codec, config, device, song_status, force_save=force_save, playlist=playlist_info))
    status.set_status(StatusCode.Done)


@logger.catch
@timeit
async def rip_artist(artist: Artist, auth_params: GlobalAuthParams, codec: str, config: Config, device: Device, status: BaseStatus,
                     force_save: bool = False, include_participate_in_works: bool = False):
    artist_info = await get_artist_info(artist.id, artist.storefront, auth_params.anonymousAccessToken,
                                        config.region.language)
    status.set_param(artist=artist_info.data[0].attributes.name)
    status.set_status(StatusCode.Processing)
    async with asyncio.TaskGroup() as tg:
        if include_participate_in_works:
            songs = await get_songs_from_artist(artist.id, artist.storefront, auth_params.anonymousAccessToken,
                                                config.region.language)
            for song_url in songs:
                song_status = status.new(URLType.Song)
                status.children.append(song_status)
                tg.create_task(rip_song(Song.parse_url(song_url), auth_params, codec, config, device, song_status, force_save=force_save))
        else:
            albums = await get_albums_from_artist(artist.id, artist.storefront, auth_params.anonymousAccessToken,
                                                  config.region.language)
            for album_url in albums:
                album_status = status.new(URLType.Song)
                status.children.append(album_status)
                tg.create_task(rip_album(Album.parse_url(album_url), auth_params, codec, config, device, album_status, force_save=force_save))
    status.set_status(StatusCode.Done)
