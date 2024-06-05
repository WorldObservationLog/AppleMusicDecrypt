import os
from pathlib import Path

from src.config import Download
from src.metadata import SongMetadata
from src.models import PlaylistInfo
from src.utils import ttml_convent_to_lrc, get_song_name_and_dir_path, get_suffix


async def save(song: bytes, codec: str, metadata: SongMetadata, config: Download, playlist: PlaylistInfo = None):
    song_name, dir_path = get_song_name_and_dir_path(codec.upper(), config, metadata, playlist)
    if not dir_path.exists() or not dir_path.is_dir():
        os.makedirs(dir_path.absolute())
    song_path = dir_path / Path(song_name + get_suffix(codec, config.atmosConventToM4a))
    with open(song_path.absolute(), "wb") as f:
        f.write(song)
    if config.saveCover and not playlist:
        cover_path = dir_path / Path(f"cover.{config.coverFormat}")
        with open(cover_path.absolute(), "wb") as f:
            f.write(metadata.cover)
    if config.saveLyrics and metadata.lyrics:
        lrc_path = dir_path / Path(song_name + ".lrc")
        with open(lrc_path.absolute(), "w", encoding="utf-8") as f:
            f.write(ttml_convent_to_lrc(metadata.lyrics))
    return song_path.absolute()
