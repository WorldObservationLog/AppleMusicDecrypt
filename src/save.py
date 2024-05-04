import os
from pathlib import Path

from src.config import Download
from src.metadata import SongMetadata
from src.types import Codec
from src.utils import ttml_convent_to_lrc, get_valid_filename


def save(song: bytes, codec: str, metadata: SongMetadata, config: Download):
    song_name = get_valid_filename(config.songNameFormat.format(**metadata.model_dump()))
    dir_path = Path(config.dirPathFormat.format(**metadata.model_dump()))
    if not dir_path.exists() or not dir_path.is_dir():
        os.makedirs(dir_path.absolute())
    if codec == Codec.EC3 and not config.atmosConventToM4a:
        song_path = dir_path / Path(song_name).with_suffix(".ec3")
    elif codec == Codec.AC3 and not config.atmosConventToM4a:
        song_path = dir_path / Path(song_name).with_suffix(".ac3")
    else:
        song_path = dir_path / Path(song_name).with_suffix(".m4a")
    with open(song_path.absolute(), "wb") as f:
        f.write(song)
    if config.saveCover:
        cover_path = dir_path / Path(f"cover.{config.coverFormat}")
        with open(cover_path.absolute(), "wb") as f:
            f.write(metadata.cover)
    if config.saveLyrics and metadata.lyrics:
        lrc_path = dir_path / Path(song_name).with_suffix(".lrc")
        with open(lrc_path.absolute(), "w", encoding="utf-8") as f:
            f.write(ttml_convent_to_lrc(metadata.lyrics))
    return song_path.absolute()
