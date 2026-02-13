from typing import Optional

import m3u8
from creart import it
from prompt_toolkit import print_formatted_text
from pydantic import BaseModel
from tabulate import tabulate

from src.api import WebAPI
from src.config import Config
from src.metadata import SongMetadata
from src.utils import get_codec_from_codec_id, safely_create_task, playlist_write_song_index
from src.url import Song, Album, URLType, Playlist
from src.grpc.manager import WrapperManager

Headers = [
    "Codec ID", 
    "Codec", 
    "Bitrate", 
    "Average Bitrate", 
    "Channels", 
    "Sample Rate", 
    "Bit Depth"
]

key_to_Headers = {
    "codec_id": "Codec ID",
    "codec": "Codec",
    "bitrate": "Bitrate",
    "average_bitrate": "Average Bitrate",
    "channels": "Channels",
    "sample_rate": "Sample Rate",
    "bit_depth": "Bit Depth"
}

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

async def print_song_quality(url: Song, show_fields: list[str]):
    raw_metadata = await it(WebAPI).get_song_info(url.id, url.storefront, it(Config).region.language)
    metadata = SongMetadata.parse_from_song_data(raw_metadata)
    m3u8_url = await it(WrapperManager).m3u8(url.id)
    audio_qualities = await get_available_audio_quality(m3u8_url)

    filtered_data = [
        [getattr(aq, field) for field in show_fields]
        for aq in audio_qualities
    ]
    filtered_headers = [key_to_Headers[field].strip() for field in show_fields]

    print_formatted_text(f"Available audio qualities for song: {metadata.artist} - {metadata.title}")
    print_formatted_text(tabulate(filtered_data, headers=filtered_headers, tablefmt="grid"))

async def print_playlist_quality(url: Playlist):
    playlist_info = await it(WebAPI).get_playlist_info_and_tracks(url.id, url.storefront, it(Config).region.language)
    playlist_info = playlist_write_song_index(playlist_info)
    for track in playlist_info.data[0].relationships.tracks.data:
        song = Song(id=track.id, storefront=url.storefront, url="", type=URLType.Song)
        safely_create_task(print_song_quality(song))

async def print_album_quality(url: Album):
    album_info = await it(WebAPI).get_album_info(url.id, url.storefront, it(Config).region.language)
    for track in album_info.data[0].relationships.tracks.data:
        song = Song(id=track.id, storefront=url.storefront, url="", type=URLType.Song)
        safely_create_task(print_song_quality(song))


class AudioQuality(BaseModel):
    codec_id: str
    codec: str
    bitrate: int
    average_bitrate: int
    channels: str
    sample_rate: Optional[int] = None
    bit_depth: Optional[int] = None
