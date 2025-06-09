import pickle
import subprocess
import sys
import uuid
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Tuple

import m3u8
import regex
from bs4 import BeautifulSoup
from creart import it
from loguru import logger

from src.api import WebAPI
from src.config import Config
from src.exceptions import CodecNotFoundException
from src.metadata import SongMetadata
from src.types import *
from src.utils import find_best_codec, get_codec_from_codec_id, get_suffix, convent_mac_timestamp_to_datetime, \
    if_raw_atmos


def if_shell():
    if sys.platform in ('win32', 'cygwin', 'cli'):
        return False
    else:
        return True


async def get_available_codecs(m3u8_url: str) -> Tuple[list[str], list[str]]:
    parsed_m3u8 = m3u8.loads(await it(WebAPI).download_m3u8(m3u8_url), uri=m3u8_url)
    codec_ids = [playlist.stream_info.audio for playlist in parsed_m3u8.playlists]
    codecs = [get_codec_from_codec_id(codec_id) for codec_id in codec_ids]
    return codecs, codec_ids


async def extract_media(m3u8_url: str, codec: str, song_metadata: SongMetadata) -> M3U8Info:
    parsed_m3u8 = m3u8.loads(await it(WebAPI).download_m3u8(m3u8_url), uri=m3u8_url)
    specifyPlaylist = find_best_codec(parsed_m3u8, codec)
    if not specifyPlaylist and it(Config).download.codecAlternative:
        logger.warning(f"Codec {codec} of song: {song_metadata.artist} - {song_metadata.title} did not found")
        for a_codec in it(Config).download.codecPriority:
            specifyPlaylist = find_best_codec(parsed_m3u8, a_codec)
            if specifyPlaylist:
                codec = a_codec
                break
    if not specifyPlaylist:
        raise CodecNotFoundException
    selected_codec = specifyPlaylist.media[0].group_id
    stream = m3u8.loads(await it(WebAPI).download_m3u8(specifyPlaylist.absolute_uri), uri=specifyPlaylist.absolute_uri)
    skds = [key.uri for key in stream.keys if regex.match('(skd?://[^"]*)', key.uri)]
    keys = [prefetchKey]
    key_suffix = CodecKeySuffix.KeySuffixDefault
    match codec:
        case Codec.ALAC:
            key_suffix = CodecKeySuffix.KeySuffixAlac
        case Codec.EC3 | Codec.AC3:
            key_suffix = CodecKeySuffix.KeySuffixAtmos
        case Codec.AAC:
            key_suffix = CodecKeySuffix.KeySuffixAAC
        case Codec.AAC_BINAURAL:
            key_suffix = CodecKeySuffix.KeySuffixAACBinaural
        case Codec.AAC_DOWNMIX:
            key_suffix = CodecKeySuffix.KeySuffixAACDownmix
    for key in skds:
        if key.endswith(key_suffix) or key.endswith(CodecKeySuffix.KeySuffixDefault):
            keys.append(key)
    if codec == Codec.ALAC:
        sample_rate, bit_depth = specifyPlaylist.media[0].extras.values()
        sample_rate, bit_depth = int(sample_rate), int(bit_depth)
    else:
        sample_rate, bit_depth = None, None
    return M3U8Info(uri=stream.segment_map[0].absolute_uri, keys=keys, codec_id=selected_codec, bit_depth=bit_depth,
                    sample_rate=sample_rate)


async def extract_song(raw_song: bytes, codec: str) -> SongInfo:
    tmp_dir = TemporaryDirectory()
    mp4_name = uuid.uuid4().hex
    raw_mp4 = Path(tmp_dir.name) / Path(f"{mp4_name}.mp4")
    with open(raw_mp4.absolute(), "wb") as f:
        f.write(raw_song)
    nhml_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.nhml')).absolute()
    media_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.media')).absolute()
    subprocess.run(f"gpac -i {raw_mp4.absolute()} nhmlw:pckp=true -o {nhml_name}",
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell())
    xml_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.xml')).absolute()
    subprocess.run(f"mp4box -diso {raw_mp4.absolute()} -out {xml_name}",
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell())
    decoder_params = None

    with open(xml_name, "r") as f:
        info_xml = BeautifulSoup(f.read(), "xml")
    with open(nhml_name, "r") as f:
        raw_nhml = f.read()
        nhml = BeautifulSoup(raw_nhml, "xml")
    with open(media_name, "rb") as f:
        media = BytesIO(f.read())

    match codec:
        case Codec.ALAC:
            alac_atom_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.atom')).absolute()
            subprocess.run(
                f"mp4extract moov/trak/mdia/minf/stbl/stsd/enca[0]/alac {raw_mp4.absolute()} {alac_atom_name}",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell())
            with open(alac_atom_name, "rb") as f:
                decoder_params = f.read()
        case Codec.AAC | Codec.AAC_DOWNMIX | Codec.AAC_BINAURAL:
            info_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.info')).absolute()
            with open(info_name, "rb") as f:
                decoder_params = f.read()

    samples = []
    moofs = info_xml.find_all("MovieFragmentBox")
    nhnt_sample_number = 0
    nhnt_samples = {}
    params = {}
    for sample in nhml.find_all("NHNTSample"):
        nhnt_samples.update({int(sample.get("number")): sample})
    for i, moof in enumerate(moofs):
        tfhd = moof.TrackFragmentBox.TrackFragmentHeaderBox
        index = 0 if not tfhd.get("SampleDescriptionIndex") else int(tfhd.get("SampleDescriptionIndex")) - 1
        truns = moof.TrackFragmentBox.find_all("TrackRunBox")
        for trun in truns:
            for sample_number in range(int(trun.get("SampleCount"))):
                nhnt_sample_number += 1
                try:
                    nhnt_sample = nhnt_samples[nhnt_sample_number]
                except KeyError as e:
                    with open("FOR_DEBUG_RAW_SONG.mp4", "wb") as f:
                        f.write(raw_song)
                    with open("FOR_DEBUG_NHNT_DUMP.bin", "wb") as f:
                        pickle.dump(nhnt_samples, f)
                    logger.error(
                        "An error occurred! Please send FOR_DEBUG_RAW_SONG.mp4 and FOR_DEBUG_NHNT_DUMP.bin to the developer!")
                    raise e
                sample_data = media.read(int(nhnt_sample.get("dataLength")))
                duration = int(nhnt_sample.get("duration"))
                samples.append(SampleInfo(descIndex=index, data=sample_data, duration=int(duration)))
    mvhd = info_xml.find("MovieHeaderBox")
    params.update({"CreationTime": convent_mac_timestamp_to_datetime(int(mvhd.get("CreationTime"))),
                   "ModificationTime": convent_mac_timestamp_to_datetime(int(mvhd.get("ModificationTime")))})
    tmp_dir.cleanup()
    return SongInfo(codec=codec, raw=raw_song, samples=samples, nhml=raw_nhml, decoderParams=decoder_params,
                    params=params)


async def encapsulate(song_info: SongInfo, decrypted_media: bytes, atmos_convent: bool) -> bytes:
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    media = Path(tmp_dir.name) / Path(name).with_suffix(".media")
    with open(media.absolute(), "wb") as f:
        f.write(decrypted_media)
    song_name = Path(tmp_dir.name) / Path(name).with_suffix(get_suffix(song_info.codec, atmos_convent))
    match song_info.codec:
        case Codec.ALAC:
            nhml_name = Path(tmp_dir.name) / Path(f"{name}.nhml")
            with open(nhml_name.absolute(), "w", encoding="utf-8") as f:
                nhml_xml = BeautifulSoup(song_info.nhml, features="xml")
                nhml_xml.NHNTStream["baseMediaFile"] = media.name
                f.write(str(nhml_xml))
            subprocess.run(f"gpac -i {nhml_name.absolute()} nhmlr -o {song_name.absolute()}",
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell())
            alac_params_atom_name = Path(tmp_dir.name) / Path(f"{name}.atom")
            with open(alac_params_atom_name.absolute(), "wb") as f:
                f.write(song_info.decoderParams)
            final_m4a_name = Path(tmp_dir.name) / Path(f"{name}_final.m4a")
            subprocess.run(
                f"mp4edit --insert moov/trak/mdia/minf/stbl/stsd/alac:{alac_params_atom_name.absolute()} {song_name.absolute()} {final_m4a_name.absolute()}",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell())
            song_name = final_m4a_name
        case Codec.EC3 | Codec.AC3:
            if not atmos_convent:
                with open(song_name.absolute(), "wb") as f:
                    f.write(decrypted_media)
            else:
                subprocess.run(f"gpac -i {media.absolute()} -o {song_name.absolute()}",
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell())
        case Codec.AAC_BINAURAL | Codec.AAC_DOWNMIX | Codec.AAC:
            nhml_name = Path(tmp_dir.name) / Path(f"{name}.nhml")
            info_name = Path(tmp_dir.name) / Path(f"{name}.info")
            with open(info_name.absolute(), "wb") as f:
                f.write(song_info.decoderParams)
            with open(nhml_name.absolute(), "w", encoding="utf-8") as f:
                nhml_xml = BeautifulSoup(song_info.nhml, features="xml")
                nhml_xml.NHNTStream["baseMediaFile"] = media.name
                nhml_xml.NHNTStream["specificInfoFile"] = info_name.name
                nhml_xml.NHNTStream["streamType"] = "5"
                f.write(str(nhml_xml))
            subprocess.run(f"gpac -i {nhml_name.absolute()} nhmlr -o {song_name.absolute()}",
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell())
    if not if_raw_atmos(song_info.codec, atmos_convent):
        subprocess.run(f'mp4box -brand "M4A " -ab "M4A " -ab "mp42" {song_name.absolute()}',
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell())
    with open(song_name.absolute(), "rb") as f:
        final_song = f.read()
    tmp_dir.cleanup()
    return final_song


async def write_metadata(song: bytes, metadata: SongMetadata, embed_metadata: list[str],
                         cover_format: str, params: dict[str, Any]) -> bytes:
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    song_name = Path(tmp_dir.name) / Path(f"{name}.m4a")
    with open(song_name.absolute(), "wb") as f:
        f.write(song)
    absolute_cover_path = ""
    if "cover" in embed_metadata:
        cover_path = Path(tmp_dir.name) / Path(f"cover.{cover_format}")
        absolute_cover_path = cover_path.absolute()
        with open(cover_path.absolute(), "wb") as f:
            f.write(metadata.cover)
    subprocess.run(["mp4box",
                    "-time", params.get("CreationTime").strftime("%d/%m/%Y-%H:%M:%S"),
                    "-mtime", params.get("ModificationTime").strftime("%d/%m/%Y-%H:%M:%S"), "-keep-utc",
                    "-name", f"1={metadata.title}", "-itags", ":".join(["tool=", f"cover={absolute_cover_path}",
                                                                        metadata.to_itags_params(embed_metadata)]),
                    song_name.absolute()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(song_name.absolute(), "rb") as f:
        embed_song = f.read()
    tmp_dir.cleanup()
    return embed_song


# There are suspected errors in M4A files encapsulated by MP4Box and GPAC,
# causing some applications to be unable to correctly process Metadata (such as Android.media, Salt Music)
# Using FFMPEG re-encapsulating solves this problem
async def fix_encapsulate(song: bytes) -> bytes:
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    song_name = Path(tmp_dir.name) / Path(f"{name}.m4a")
    new_song_name = Path(tmp_dir.name) / Path(f"{name}_fixed.m4a")
    with open(song_name.absolute(), "wb") as f:
        f.write(song)
    subprocess.run(
        f"ffmpeg -y -i {song_name.absolute()} -fflags +bitexact -map_metadata 0 -c:a copy -c:v copy {new_song_name.absolute()}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell())
    with open(new_song_name.absolute(), "rb") as f:
        encapsulated_song = f.read()
    tmp_dir.cleanup()
    return encapsulated_song


# FFMPEG will overwrite maxBitrate in DecoderConfigDescriptor
# Using raw song's esds box to fix it
# see also https://trac.ffmpeg.org/ticket/4894
async def fix_esds_box(raw_song: bytes, song: bytes) -> bytes:
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    esds_name = Path(tmp_dir.name) / Path(f"{name}.atom")
    raw_song_name = Path(tmp_dir.name) / Path(f"{name}_raw.m4a")
    song_name = Path(tmp_dir.name) / Path(f"{name}.m4a")
    final_song_name = Path(tmp_dir.name) / Path(f"{name}_final.m4a")
    with open(raw_song_name.absolute(), "wb") as f:
        f.write(raw_song)
    with open(song_name.absolute(), "wb") as f:
        f.write(song)
    subprocess.run(
        f"mp4extract moov/trak/mdia/minf/stbl/stsd/enca[0]/esds {raw_song_name.absolute()} {esds_name.absolute()}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell())
    subprocess.run(
        f"mp4edit --replace moov/trak/mdia/minf/stbl/stsd/mp4a/esds:{esds_name.absolute()} {song_name.absolute()} {final_song_name.absolute()}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell())
    with open(final_song_name.absolute(), "rb") as f:
        final_song = f.read()
    tmp_dir.cleanup()
    return final_song


async def check_song_integrity(song: bytes) -> bool:
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    song_name = Path(tmp_dir.name) / Path(f"{name}.m4a")
    with open(song_name.absolute(), "wb") as f:
        f.write(song)
    output = subprocess.run(f"ffmpeg -y -v error -i {song_name.absolute()} -c:a pcm_s16le -f null /dev/null",
                            capture_output=True)
    tmp_dir.cleanup()
    return not bool(output.stderr)
