import subprocess
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Tuple

import m3u8
import regex
from bs4 import BeautifulSoup
from loguru import logger

from src.api import download_m3u8
from src.exceptions import CodecNotFoundException
from src.metadata import SongMetadata
from src.types import *
from src.utils import find_best_codec, get_codec_from_codec_id, get_suffix


async def get_available_codecs(m3u8_url: str) -> Tuple[list[str], list[str]]:
    parsed_m3u8 = m3u8.loads(await download_m3u8(m3u8_url), uri=m3u8_url)
    codec_ids = [playlist.stream_info.audio for playlist in parsed_m3u8.playlists]
    codecs = [get_codec_from_codec_id(codec_id) for codec_id in codec_ids]
    return codecs, codec_ids


async def extract_media(m3u8_url: str, codec: str, song_metadata: SongMetadata,
                        codec_priority: list[str], alternative_codec: bool = False) -> Tuple[str, list[str]]:
    parsed_m3u8 = m3u8.loads(await download_m3u8(m3u8_url), uri=m3u8_url)
    specifyPlaylist = find_best_codec(parsed_m3u8, codec)
    if not specifyPlaylist and alternative_codec:
        logger.warning(f"Codec {codec} of song: {song_metadata.artist} - {song_metadata.title} did not found")
        for a_codec in codec_priority:
            specifyPlaylist = find_best_codec(parsed_m3u8, a_codec)
            if specifyPlaylist:
                codec = a_codec
                break
    if not specifyPlaylist:
        raise CodecNotFoundException
    selected_codec = specifyPlaylist.media[0].group_id
    logger.info(f"Selected codec: {selected_codec} for song: {song_metadata.artist} - {song_metadata.title}")
    stream = m3u8.loads(await download_m3u8(specifyPlaylist.absolute_uri), uri=specifyPlaylist.absolute_uri)
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
    return stream.segment_map[0].absolute_uri, keys


def extract_song(raw_song: bytes, codec: str) -> SongInfo:
    tmp_dir = TemporaryDirectory()
    mp4_name = uuid.uuid4().hex
    raw_mp4 = Path(tmp_dir.name) / Path(f"{mp4_name}.mp4")
    with open(raw_mp4.absolute(), "wb") as f:
        f.write(raw_song)
    nhml_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.nhml')).absolute()
    media_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.media')).absolute()
    subprocess.run(f"gpac -i {raw_mp4.absolute()} nhmlw:pckp=true -o {nhml_name}",
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    xml_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.xml')).absolute()
    subprocess.run(f"mp4box -diso {raw_mp4.absolute()} -out {xml_name}",
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    decoder_params = None

    with open(xml_name, "r") as f:
        info_xml = BeautifulSoup(f.read(), "xml")
    with open(nhml_name, "r") as f:
        raw_nhml = f.read()
        nhml = BeautifulSoup(raw_nhml, "xml")
    with open(media_name, "rb") as f:
        media = BytesIO(f.read())

    if codec == Codec.ALAC:
        alac_atom_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.atom')).absolute()
        subprocess.run(f"mp4extract moov/trak/mdia/minf/stbl/stsd/enca[0]/alac {raw_mp4.absolute()} {alac_atom_name}",
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(alac_atom_name, "rb") as f:
            decoder_params = f.read()

    samples = []
    moofs = info_xml.find_all("MovieFragmentBox")
    nhnt_sample_number = 0
    nhnt_samples = {}
    for sample in nhml.find_all("NHNTSample"):
        nhnt_samples.update({int(sample.get("number")): sample})
    for i, moof in enumerate(moofs):
        tfhd = moof.TrackFragmentBox.TrackFragmentHeaderBox
        index = 0 if not tfhd.get("SampleDescriptionIndex") else int(tfhd.get("SampleDescriptionIndex")) - 1
        truns = moof.TrackFragmentBox.find_all("TrackRunBox")
        for trun in truns:
            for sample_number in range(int(trun.get("SampleCount"))):
                nhnt_sample_number += 1
                nhnt_sample = nhnt_samples[nhnt_sample_number]
                sample_data = media.read(int(nhnt_sample.get("dataLength")))
                duration = int(nhnt_sample.get("duration"))
                samples.append(SampleInfo(descIndex=index, data=sample_data, duration=int(duration)))
    tmp_dir.cleanup()
    return SongInfo(codec=codec, raw=raw_song, samples=samples, nhml=raw_nhml, decoderParams=decoder_params)


def encapsulate(song_info: SongInfo, decrypted_media: bytes, atmos_convent: bool) -> bytes:
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
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            alac_params_atom_name = Path(tmp_dir.name) / Path(f"{name}.atom")
            with open(alac_params_atom_name.absolute(), "wb") as f:
                f.write(song_info.decoderParams)
            final_m4a_name = Path(tmp_dir.name) / Path(f"{name}_final.m4a")
            subprocess.run(
                f"mp4edit --insert moov/trak/mdia/minf/stbl/stsd/alac:{alac_params_atom_name.absolute()} {song_name.absolute()} {final_m4a_name.absolute()}",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            song_name = final_m4a_name
        case Codec.EC3 | Codec.AC3:
            if not atmos_convent:
                with open(song_name.absolute(), "wb") as f:
                    f.write(decrypted_media)
            else:
                subprocess.run(f"gpac -i {media.absolute()} -o {song_name.absolute()}",
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        case Codec.AAC_BINAURAL | Codec.AAC_DOWNMIX | Codec.AAC:
            nhml_name = Path(tmp_dir.name) / Path(f"{name}.nhml")
            with open(nhml_name.absolute(), "w", encoding="utf-8") as f:
                nhml_xml = BeautifulSoup(song_info.nhml, features="xml")
                nhml_xml.NHNTStream["baseMediaFile"] = media.name
                del nhml_xml.NHNTStream["streamType"]
                del nhml_xml.NHNTStream["objectTypeIndication"]
                del nhml_xml.NHNTStream["specificInfoFile"]
                nhml_xml.NHNTStream["mediaType"] = "soun"
                nhml_xml.NHNTStream["mediaSubType"] = "mp4a"
                f.write(str(nhml_xml))
            subprocess.run(f"gpac -i {nhml_name.absolute()} nhmlr -o {song_name.absolute()}",
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(song_name.absolute(), "rb") as f:
        final_song = f.read()
    tmp_dir.cleanup()
    return final_song


def write_metadata(song: bytes, metadata: SongMetadata, embed_metadata: list[str], cover_format: str) -> bytes:
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
    time = datetime.strptime(metadata.created, "%Y-%m-%d").strftime("%d/%m/%Y")
    subprocess.run(["mp4box", "-time", time, "-mtime", time, "-keep-utc", "-name", f"1={metadata.title}", "-itags",
                    ":".join(["tool=", f"cover={absolute_cover_path}",
                              metadata.to_itags_params(embed_metadata)]),
                    song_name.absolute()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(song_name.absolute(), "rb") as f:
        embed_song = f.read()
    tmp_dir.cleanup()
    return embed_song
