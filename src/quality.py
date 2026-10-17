from typing import Callable, Optional

import m3u8
from creart import it
from pydantic import BaseModel
from tabulate import tabulate

from src.api import WebAPI
from src.config import Config

# Accessed through the module (not `from src.logger import _safe_print`) so the
# TUI log sink can swap the implementation at runtime — a value import would
# bind the original function and leak `qa` output onto the terminal, corrupting
# the full-screen view.
from src import logger as _logger_mod
from src.metadata import SongMetadata
from src.utils import get_codec_from_codec_id, safely_create_task, playlist_write_song_index
from src.url import Song, Album, URLType, Playlist, TrackType
from src.wrapper import WrapperClient

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

# How a UI presents one quality result.  Signature:
# ``(title, headers, rows) -> None``.
QualityView = Callable[[str, "list[str]", "list[list[str]]"], None]

_quality_view: Optional[QualityView] = None


def set_quality_view(view: Optional[QualityView]) -> None:
    """Install (or clear, with ``None``) the presentation hook for `qa` output.

    Without a hook the result is printed as a ``tabulate`` grid — which is the
    right thing on a terminal and in ``--legacy-ui``.  It is the wrong thing in
    the full-screen TUI: a grid is a fixed ~90 columns wide regardless of the
    terminal, so in a 68-column log pane every border was broken across two
    lines and the columns no longer lined up.  The TUI installs a hook here and
    shows the same data in a scrollable table instead.
    """
    global _quality_view
    _quality_view = view


def _emit_quality(title: str, headers: "list[str]", rows: "list[list[str]]") -> None:
    """Hand one quality result to the active UI."""
    if _quality_view is not None:
        _quality_view(title, headers, rows)
        return
    _logger_mod._safe_print(title)
    _logger_mod._safe_print(tabulate(rows, headers=headers, tablefmt="grid"))


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
    m3u8_url = await it(WrapperClient).m3u8(url.id)
    audio_qualities = await get_available_audio_quality(m3u8_url)

    filtered_data = [
        [getattr(aq, field) for field in show_fields]
        for aq in audio_qualities
    ]
    filtered_headers = [key_to_Headers[field].strip() for field in show_fields]

    _emit_quality(f"Available audio qualities for song: {metadata.artist} - {metadata.title}",
                  filtered_headers, filtered_data)


async def print_playlist_quality(url: Playlist, show_fields: list[str]):
    playlist_info = await it(WebAPI).get_playlist_info_and_tracks(url.id, url.storefront, it(Config).region.language)
    playlist_info = playlist_write_song_index(playlist_info)
    for track in playlist_info.data[0].relationships.tracks.data:
        if track.type != TrackType.Song:  # music videos have no audio quality listing
            continue
        song = Song(id=track.id, storefront=url.storefront, url="", type=URLType.Song)
        safely_create_task(print_song_quality(song, show_fields))


async def print_album_quality(url: Album, show_fields: list[str]):
    album_info = await it(WebAPI).get_album_info(url.id, url.storefront, it(Config).region.language)
    for track in album_info.data[0].relationships.tracks.data:
        if track.type != TrackType.Song:  # music videos have no audio quality listing
            continue
        song = Song(id=track.id, storefront=url.storefront, url="", type=URLType.Song)
        safely_create_task(print_song_quality(song, show_fields))


class AudioQuality(BaseModel):
    codec_id: str
    codec: str
    bitrate: int
    average_bitrate: int
    channels: str
    sample_rate: Optional[int] = None
    bit_depth: Optional[int] = None
