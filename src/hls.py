"""HLS (m3u8) helpers: parse the master/media playlists and pick the codec.

This is the pure-Python replacement for the m3u8 part of the old gpac flow.
"""

import regex
import m3u8
from creart import it

from src.api import WebAPI
from src.config import Config
from src.exceptions import CodecNotFoundException
from src.task import Task
from src.types import M3U8Info, Codec, CodecKeySuffix, prefetchKey
from src.utils import find_best_codec, get_codec_from_codec_id


async def get_available_codecs(m3u8_url: str) -> tuple[list[str], list[str]]:
    parsed_m3u8 = m3u8.loads(await it(WebAPI).download_m3u8(m3u8_url), uri=m3u8_url)
    codec_ids = [playlist.stream_info.audio for playlist in parsed_m3u8.playlists]
    codecs = [get_codec_from_codec_id(codec_id) for codec_id in codec_ids]
    return codecs, codec_ids


async def extract_media(m3u8_url: str, codec: str, task: Task) -> M3U8Info:
    """Parse the master playlist, select the best media playlist for ``codec``
    and return the media file location plus the FairPlay key URIs."""
    parsed_m3u8 = m3u8.loads(await it(WebAPI).download_m3u8(m3u8_url), uri=m3u8_url)
    specifyPlaylist = find_best_codec(parsed_m3u8, codec)
    if not specifyPlaylist and it(Config).download.codecAlternative:
        for a_codec in it(Config).download.codecPriority:
            specifyPlaylist = find_best_codec(parsed_m3u8, a_codec)
            if specifyPlaylist:
                codec = a_codec
                task.logger.codec_alternative()
                break
    if not specifyPlaylist:
        raise CodecNotFoundException
    selected_codec = specifyPlaylist.media[0].group_id
    stream = m3u8.loads(await it(WebAPI).download_m3u8(specifyPlaylist.absolute_uri),
                        uri=specifyPlaylist.absolute_uri)
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

    segment = stream.segment_map[0] if stream.segment_map else (stream.segments[0] if stream.segments else None)
    if segment is None:
        raise CodecNotFoundException("No media segment found in playlist")

    range_start = range_length = None
    byterange = getattr(segment, "byterange", None)
    if byterange:
        if isinstance(byterange, str) and "@" in byterange:
            # m3u8 returns EXT-X-BYTERANGE as "length@offset"
            try:
                range_length, range_start = (int(x) for x in byterange.split("@", 1))
            except ValueError:
                range_start = range_length = None
        else:
            try:
                range_length, range_start = byterange
            except (TypeError, ValueError):
                range_start = range_length = None

    sample_rate = bit_depth = None
    if codec == Codec.ALAC:
        extras = specifyPlaylist.media[0].extras or {}
        if extras.get("sample_rate") and extras.get("bit_depth"):
            sample_rate, bit_depth = int(extras["sample_rate"]), int(extras["bit_depth"])

    return M3U8Info(uri=segment.absolute_uri, keys=keys, codec_id=selected_codec,
                    bit_depth=bit_depth, sample_rate=sample_rate,
                    range_start=range_start, range_length=range_length)