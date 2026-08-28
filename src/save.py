import os
from pathlib import Path
from typing import Tuple

import mutagen.mp4
from creart import it

from src.config import Config
from src.metadata import SongMetadata
from src.models import PlaylistInfo
from src.utils import ttml_convent, get_song_name_and_dir_path, get_suffix


def prepare_paths(codec: str, metadata: SongMetadata,
                  playlist: PlaylistInfo = None) -> Tuple[Path, Path]:
    """Return (final_path, part_path) for a song being ripped.

    The streaming pipeline writes into the ``.part`` file first, then
    ``finalize()`` writes metadata and renames it into place.
    """
    song_name, dir_path = get_song_name_and_dir_path(codec.upper(), metadata, playlist)
    dir_path.mkdir(parents=True, exist_ok=True)
    suffix = get_suffix(codec, it(Config).download.atmosConventToM4a)
    final_path = dir_path / (song_name + suffix)
    part_path = dir_path / (song_name + ".part")
    return final_path, part_path


def write_metadata_file(path: str, metadata: SongMetadata):
    """Embed tags into a completed audio file with mutagen (pure Python)."""
    mp4 = mutagen.mp4.Open(path)
    mp4.update(metadata.to_mutagen_tags(it(Config).metadata.embedMetadata))
    mp4.save()


def finalize(part_path: str, final_path: str, metadata: SongMetadata, cover_format: str):
    """Write metadata into the .part file, rename it to its final name and save
    sidecar cover / lyrics files."""
    write_metadata_file(part_path, metadata)
    os.replace(part_path, final_path)
    final = Path(final_path)
    dir_path = final.parent
    song_name = final.name

    if it(Config).download.saveCover and metadata.cover:
        cover_path = dir_path / f"cover.{it(Config).download.coverFormat}"
        cover_path.write_bytes(metadata.cover)

    if it(Config).download.saveLyrics and metadata.lyrics:
        lrc = ttml_convent(metadata.lyrics)
        if lrc:
            if it(Config).download.lyricsFormat == "ttml":
                lrc_path = dir_path / (song_name + ".ttml")
            else:
                lrc_path = dir_path / (song_name + ".lrc")
            lrc_path.write_text(lrc, encoding="utf-8")