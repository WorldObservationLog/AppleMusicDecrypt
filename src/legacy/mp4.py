import subprocess
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

import m3u8
from creart import it

from src.api import WebAPI
from src.mp4 import if_shell
from src.types import M3U8Info, Codec


async def extract_media(m3u8_url: str):
    parsed_m3u8 = m3u8.loads(await it(WebAPI).download_m3u8(m3u8_url), uri=m3u8_url)
    return M3U8Info(uri=parsed_m3u8.segment_map[0].absolute_uri, keys=[parsed_m3u8.keys[0].absolute_uri],
                    codec_id=Codec.AAC_LEGACY)


def decrypt(song: bytes, kid: str, key: str) -> bytes:
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    song_name = Path(tmp_dir.name) / Path(f"{name}.m4a")
    new_song_name = Path(tmp_dir.name) / Path(f"{name}_fixed.m4a")
    with open(song_name.absolute(), "wb") as f:
        f.write(song)
    subprocess.run(
        f"mp4decrypt --key {kid}:{key} {song_name.absolute()} {new_song_name.absolute()}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell())
    with open(new_song_name.absolute(), "rb") as f:
        decrypted_song = f.read()
    tmp_dir.cleanup()
    return decrypted_song
