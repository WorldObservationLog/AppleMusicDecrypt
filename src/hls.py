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

    segments = stream.segments if stream.segments else []
    if not segments:
        raise CodecNotFoundException("No media segment found in playlist")
    segment = segments[0]

    # Apple Music lossless HLS serves the whole song as ONE fMP4 file where
    # each playlist segment is a byte-range of that file (segment[0] is only
    # the init, the rest are audio fragments). In that case we must download
    # the WHOLE file and parse every fragment — not just segment[0]'s range.
    # A single non-range segment (also common) means the same whole-file case.
    range_start = range_length = None
    if any(getattr(s, "byterange", None) for s in segments):
        # byte-range segments -> whole file; keep range fields None
        pass
    elif len(segments) > 1:
        # Multiple non-range segment files are not supported; use the first
        # file's URI (legacy/webPlayback may produce these).
        pass

    sample_rate = bit_depth = None
    if codec == Codec.ALAC:
        extras = specifyPlaylist.media[0].extras or {}
        if extras.get("sample_rate") and extras.get("bit_depth"):
            sample_rate, bit_depth = int(extras["sample_rate"]), int(extras["bit_depth"])

    return M3U8Info(uri=segment.absolute_uri, keys=keys, codec_id=selected_codec,
                    bit_depth=bit_depth, sample_rate=sample_rate,
                    range_start=range_start, range_length=range_length)


async def legacy_extract_media(m3u8_url: str) -> M3U8Info:
    """Parse a webPlayback media playlist (AAC-legacy / Widevine CENC).

    Unlike the lossless master playlists, this is already a *media* playlist
    whose segments are byte-ranges of ONE .mp4 file (segments[0] is a
    fragment; the init prefix is the bytes before its range). The key URI is a
    Widevine ``data:;base64,<kid>`` URI (method ISO-23001-7). We download the
    whole .mp4 and let the mp4 module parse init + every fragment.
    """
    parsed = m3u8.loads(await it(WebAPI).download_m3u8(m3u8_url), uri=m3u8_url)
    segments = parsed.segments
    if not segments:
        raise CodecNotFoundException("No media segment found in legacy playlist")
    seg = segments[0]
    keys = [k.uri for k in parsed.keys if k.uri]
    return M3U8Info(uri=seg.absolute_uri, keys=keys, codec_id=Codec.AAC_LEGACY)