import asyncio
import subprocess
import httpx

from loguru import logger

from src.api import get_info_from_adam, get_song_lyrics, get_meta, download_song
from src.config import Config, Device
from src.decrypt import decrypt
from src.metadata import SongMetadata
from src.mp4 import extract_media, extract_song, encapsulate, write_metadata
from src.save import save
from src.types import GlobalAuthParams, Codec
from src.url import Song, Album, URLType
from src.utils import check_song_exists


@logger.catch
async def rip_song(song: Song, auth_params: GlobalAuthParams, codec: str, config: Config, device: Device,
                   force_save: bool = False, specified_m3u8: str = ""):
    logger.debug(f"Task of song id {song.id} was created")
    token = auth_params.anonymousAccessToken
    song_data = await get_info_from_adam(song.id, token, song.storefront, config.region.language)
    song_metadata = SongMetadata.parse_from_song_data(song_data)
    logger.info(f"Ripping song: {song_metadata.artist} - {song_metadata.title}")
    if not force_save and check_song_exists(song_metadata, config.download, codec):
        logger.info(f"Song: {song_metadata.artist} - {song_metadata.title} already exists")
        return
    await song_metadata.get_cover(config.download.coverFormat, config.download.coverSize)
    if song_data.attributes.hasTimeSyncedLyrics:
        lyrics = await get_song_lyrics(song.id, song.storefront, auth_params.accountAccessToken,
                                       auth_params.dsid, auth_params.accountToken, config.region.language)
        song_metadata.lyrics = lyrics
    if "http" in config.download.check:
        params = (
            ('songid', song.id),
        )
        m3u8_url = httpx.get(config.download.check, params=params).text
        if "m3u8" in m3u8_url:
            song_data.attributes.extendedAssetUrls.enhancedHls = m3u8_url
            logger.info("Find m3u8 from API")
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
    if codec != Codec.EC3 or (codec == Codec.EC3 and config.download.atmosConventToM4a):
        song = write_metadata(song, song_metadata, config.metadata.embedMetadata, config.download.coverFormat)
    if codec != Codec.AC3 or (codec == Codec.AC3 and config.download.atmosConventToM4a):
        song = write_metadata(song, song_metadata, config.metadata.embedMetadata, config.download.coverFormat)
    filename = save(song, codec, song_metadata, config.download)
    logger.info(f"Song {song_metadata.artist} - {song_metadata.title} saved!")
    if config.download.afterDownloaded:
        command = config.download.afterDownloaded.format(filename=filename)
        logger.info(f"Executing command: {command}")
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def rip_album(album: Album, auth_params: GlobalAuthParams, codec: str, config: Config, device: Device,
                    force_save: bool = False):
    album_info = await get_meta(album.id, auth_params.anonymousAccessToken, album.storefront, config.region.language)
    logger.info(f"Ripping Album: {album_info.data[0].attributes.artistName} - {album_info.data[0].attributes.name}")
    async with asyncio.TaskGroup() as tg:
        for track in album_info.data[0].relationships.tracks.data:
            song = Song(id=track.id, storefront=album.storefront, url="", type=URLType.Song)
            tg.create_task(rip_song(song, auth_params, codec, config, device, force_save=force_save))
    logger.info(
        f"Album: {album_info.data[0].attributes.artistName} - {album_info.data[0].attributes.name} finished ripping")


async def rip_playlist():
    pass


async def rip_artist():
    pass
