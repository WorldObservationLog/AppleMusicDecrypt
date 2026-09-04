import os
import shutil
import subprocess
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


def ffmpeg_reencapsulate(path: Path) -> bool:
    """Re-mux an M4A with ffmpeg (stream copy) for better player compatibility.

    v2 observed that M4As encapsulated by our pure-Python ISO-BMFF writer can
    be mishandled by some players (e.g. Android media framework).  ffmpeg
    re-encapsulation with ``-c:a copy -c:v copy`` keeps the audio bit-identical
    while producing a container that Android plays reliably.

    Returns True on success, False if ffmpeg is unavailable or failed (the
    original file is left untouched in that case).
    """
    if shutil.which("ffmpeg") is None:
        return False
    # The temp output must keep a container extension ffmpeg can infer
    # (.ffmpeg is unknown); v2 used '<name>_fixed.m4a'.
    tmp = path.with_name(path.stem + "_fixed" + path.suffix)
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(path),
                "-fflags", "+bitexact",
                "-map_metadata", "0",
                "-c:a", "copy", "-c:v", "copy",
                str(tmp),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0 or not tmp.exists():
            return False
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def finalize(part_path: str, final_path: str, metadata: SongMetadata, cover_format: str):
    """Write metadata into the .part file, rename it to its final name and save
    sidecar cover / lyrics files."""
    part = Path(part_path)
    final = Path(final_path)

    # Android's media framework mishandles fragmented MP4 (our streaming
    # output).  Rebuild it as a progressive MP4 in pure Python first; if the
    # input is not fragmented (or conversion fails) fall back to the ffmpeg
    # re-mux, then to the raw output.
    converted = False
    try:
        from src.defrag import defragment_file_streaming
        # Stream the fMP4 -> progressive conversion to a temp file.  This
        # never reads the whole .part into Python memory; it mmaps the input
        # and copies each fragment payload as it goes.
        tmp = Path(final_path).with_name(Path(final_path).stem + "_prog" +
                                         Path(final_path).suffix)
        defragment_file_streaming(str(part), str(tmp))
        os.replace(tmp, final_path)
        converted = True
        # part is no longer needed; remove so the final os.replace below
        # doesn't clobber the converted file.
        os.remove(part)
    except Exception:
        converted = False

    if not converted:
        # ffmpeg re-mux fallback (stream copy, no re-encode).
        ffmpeg_reencapsulate(part)
        os.replace(part, final)

    write_metadata_file(str(final), metadata)
    dir_path = final.parent
    song_name = final.name
    song_stem = final.stem   # filename without the audio extension

    if it(Config).download.saveCover and metadata.cover:
        cover_path = dir_path / f"cover.{it(Config).download.coverFormat}"
        cover_path.write_bytes(metadata.cover)

    if it(Config).download.saveLyrics and metadata.lyrics:
        lrc = ttml_convent(metadata.lyrics)
        if lrc:
            if it(Config).download.lyricsFormat == "ttml":
                lrc_path = dir_path / (song_stem + ".ttml")
            else:
                lrc_path = dir_path / (song_stem + ".lrc")
            lrc_path.write_text(lrc, encoding="utf-8")