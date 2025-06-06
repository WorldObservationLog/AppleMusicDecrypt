from typing import Optional

import m3u8
from creart import it
from pydantic import BaseModel

from src.api import WebAPI
from src.utils import get_codec_from_codec_id


async def get_available_audio_quality(m3u8_url: str):
    parsed_m3u8 = m3u8.loads(await it(WebAPI).download_m3u8(m3u8_url), uri=m3u8_url)
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
