from typing import Optional

import m3u8
from loguru import logger
from pydantic import BaseModel

from src.api import get_song_info, get_m3u8_from_api, download_m3u8
from src.config import Config, Device
from src.exceptions import CodecNotFoundException
from src.metadata import SongMetadata
from src.types import GlobalAuthParams
from src.url import Song
from src.utils import get_codec_from_codec_id


async def get_available_audio_quality(m3u8_url: str):
    parsed_m3u8 = m3u8.loads(await download_m3u8(m3u8_url), uri=m3u8_url)
    result = []
    for playlist in parsed_m3u8.playlists:
        if get_codec_from_codec_id(playlist.stream_info.audio):
            result.append(AudioQuality(codec_id=playlist.stream_info.audio,
                                       codec=get_codec_from_codec_id(playlist.stream_info.audio),
                                       bitrate=playlist.stream_info.bandwidth,
                                       average_bitrate=playlist.stream_info.average_bandwidth,
                                       channels=playlist.media[0].channels,
                                       sample_rate=playlist.media[0].extras.get("sample_rate", None),
                                       bit_depth=playlist.media[0].extras.get("bit_depth", None)))
    return result


class AudioQuality(BaseModel):
    codec_id: str
    codec: str
    bitrate: int
    average_bitrate: int
    channels: str
    sample_rate: Optional[int] = None
    bit_depth: Optional[int] = None


async def get_available_song_audio_quality(song: Song, config: Config, auth_params: GlobalAuthParams,
                                           device: Device) -> tuple[SongMetadata, list[AudioQuality]]:
    specified_m3u8 = None
    token = auth_params.anonymousAccessToken
    song_data = await get_song_info(song.id, token, song.storefront, config.region.language)
    song_metadata = SongMetadata.parse_from_song_data(song_data)
    if config.m3u8Api.enable:
        m3u8_url = await get_m3u8_from_api(config.m3u8Api.endpoint, song.id, config.m3u8Api.enable)
        if m3u8_url:
            specified_m3u8 = m3u8_url
            logger.info(f"Use m3u8 from API for song: {song_metadata.artist} - {song_metadata.title}")
    if not song_data.attributes.extendedAssetUrls:
        logger.error(
            f"Failed to get audio quality fo song: {song_metadata.artist} - {song_metadata.title}. Audio does not exist")
        raise CodecNotFoundException
    if not song_data.attributes.extendedAssetUrls.enhancedHls:
        logger.error(
            f"Failed to get audio quality for song: {song_metadata.artist} - {song_metadata.title}. Lossless audio does not exist")
        raise CodecNotFoundException
    if not specified_m3u8 and config.download.getM3u8FromDevice:
        device_m3u8 = await device.get_m3u8(song.id)
        if device_m3u8:
            specified_m3u8 = device_m3u8
            logger.info(f"Use m3u8 from device for song: {song_metadata.artist} - {song_metadata.title}")
    if specified_m3u8:
        audio_qualities = await get_available_audio_quality(specified_m3u8)
    else:
        audio_qualities = await get_available_audio_quality(song_data.attributes.extendedAssetUrls.enhancedHls)
    return song_metadata, audio_qualities
