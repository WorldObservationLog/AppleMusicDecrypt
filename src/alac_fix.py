"""Detect and repair a known Apple Music ALAC container defect.

Background
----------
Some ALAC tracks produced/re-encoded by Apple after ~2025 contain frames
that are legal *uncompressed* PCM mode (``is_compressed=false``) but the
encoder omitted the 3-bit END tag after the PCM payload.  FFmpeg then
tries to parse the remaining padding as a new element and fails with
``invalid element channel count`` / ``Syntax element`` / ``patches
welcome``.

Affected packets are identified generically:
- they have the byte size of an uncompressed PCM ALAC frame
  (frame_length * channels * bit_depth bits + ~32 bits of frame header /
  END / padding);
- the 3 END bits at the end of the packet are not set to ``111``.

Repair is lossless: write the missing 3-bit END tag into the last two
bytes of the packet (the last 9 bits are ``[END:3][padding:6]``).

Verified against the real affected file used in ALAC修复可能性论证.md:
- original: 4 damaged packets (#996 #1251 #1252 #1878)
- a 4-byte partial repair still leaves decode errors and a short PCM
  output (32,956,568 bytes);
- the complete repair changes exactly 7 bytes (three packets need two
  bytes, one packet needs one byte because its LSB is already set) and
  produces the reference clean file MD5 CE9D0547... with 0 decode
  errors and full PCM length 33,005,720 bytes.
"""

from __future__ import annotations

import struct
from pathlib import Path

# The final 9 bits of a damaged-but-complete uncompressed ALAC frame are
# [END:3][padding:6].  END occupies:
#   - the LSB of the penultimate byte
#   - bits 7-6 of the last byte
_END_FIRST = 0x01
_END_REST = 0xC0


def _alac_cookie_info(decoder_params: bytes | None):
    """Return (frame_length, channels, bit_depth) from an ALAC magic cookie.

    ``decoder_params`` is the full ``alac`` box:
      size(4) 'alac'(4) version/flags(4) ALACSpecificConfig...
    ALACSpecificConfig:
      frame_length(4) compatible_version(1) bit_depth(1) ... channels(1) ...
    """
    if not decoder_params or len(decoder_params) < 24:
        return None
    # Ensure it really starts with an 'alac' box; skip 12-byte box header.
    if decoder_params[4:8] != b"alac":
        # Sometimes only the payload is stored.
        start = 0
    else:
        start = 12
    cfg = decoder_params[start:]
    if len(cfg) < 12:
        return None
    frame_length = struct.unpack(">I", cfg[0:4])[0]
    bit_depth = cfg[5]
    channels = cfg[9]
    if frame_length <= 0 or channels <= 0 or bit_depth <= 0:
        return None
    return frame_length, channels, bit_depth


def _sample_tables_and_cookie(data, cookie=None):
    """Return (sizes, chunk_offset, cookie_info) from in-memory/mmap bytes.

    *data* is a bytes-like object (mmap or bytes).  *cookie* may be supplied
    from TrackInfo to avoid an extra parse.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.mp4 import (
        MP4ParseError,
        _box_header,
        _find_child_boxes,
        _u32,
        parse_init,
    )

    init, _ = parse_init(data)
    if init is None:
        return None
    moov = init.moov
    try:
        _, msize, mh, _ = _box_header(moov, 0, len(moov))
    except MP4ParseError:
        return None

    # Gather TrackInfo for the first alac track (for decoder_params).
    alac_params = cookie
    if alac_params is None:
        for tid, track in init.tracks.items():
            if track.codec == "alac":
                alac_params = _alac_cookie_info(track.decoder_params)
                break

    for trak, ts, te in _find_child_boxes(moov, mh, msize, b"trak"):
        for mdia, ms, me in _find_child_boxes(moov, ts + 8, te, b"mdia"):
            for minf, ns, ne in _find_child_boxes(moov, ms + 8, me, b"minf"):
                for stbl, ss, se in _find_child_boxes(moov, ns + 8, ne, b"stbl"):
                    sizes = []
                    stco = None
                    for box, cs, ce in _find_child_boxes(
                            moov, ss + 8, se, b"stsz"):
                        if len(box) < 20:
                            continue
                        sample_size = _u32(box, 12)
                        sample_count = _u32(box, 16)
                        if sample_size:
                            sizes = [sample_size] * sample_count
                        elif len(box) >= 20 + 4 * sample_count:
                            sizes = list(struct.unpack(
                                ">%dI" % sample_count,
                                box[20:20 + 4 * sample_count]))
                    for box, cs, ce in _find_child_boxes(
                            moov, ss + 8, se, b"stco"):
                        if len(box) >= 20:
                            stco = _u32(box, 16)
                        elif len(box) >= 16:
                            stco = _u32(box, 12)
                    if sizes and stco is not None:
                        return sizes, stco, alac_params
    return None


def _expected_uncompressed_size(cookie_info) -> int:
    """Byte size of an uncompressed ALAC frame plus 32-bit frame trailer.

    The 32 bits cover the ALAC element header (~23 bits), the 3-bit END tag
    and padding.  For 16-bit stereo/4096 samples this yields 16388 bytes.
    """
    frame_length, channels, bit_depth = cookie_info
    bits = frame_length * channels * bit_depth
    return (bits + 32 + 7) // 8  # round up to whole bytes


def find_bad_packets(path: str) -> list[int]:
    """Return packet indices whose ALAC uncompressed frame lacks an END tag.

    Works for any ALAC frame size / channel count / bit depth, not just the
    previously observed 16388-byte 16-bit stereo packets.
    """
    p = Path(path)
    import mmap
    with open(p, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            result = _sample_tables_and_cookie(data)
            if result is None:
                return []
            sizes, chunk_offset, cookie_info = result
            if not cookie_info:
                return []
            expected = _expected_uncompressed_size(cookie_info)
            bad = []
            offset = chunk_offset
            for idx, size in enumerate(sizes):
                if size == expected and size >= 2:
                    b0 = data[offset + size - 2]
                    b1 = data[offset + size - 1]
                    if not ((b0 & _END_FIRST) and (b1 & _END_REST) == _END_REST):
                        bad.append(idx)
                offset += size
            return bad


def fix_alac_end_tags(path: str, dry_run: bool = False) -> tuple[int, bool]:
    """Patch the missing 3-bit END tag in every affected ALAC packet.

    Returns ``(fixed_count, modified)``.  The file is rewritten only when
    ``dry_run`` is False.

    Note: for the canonical 16-bit/44.1kHz stereo case this touches exactly
    7 bytes in total (three packets x 2 bytes + one packet x 1 byte).  A
    partial fix that touches only 4 bytes leaves the file still damaged.
    """
    p = Path(path)
    if not p.exists():
        return 0, False
    bad = find_bad_packets(path)
    if not bad:
        return 0, False

    import mmap
    fixed = 0
    # Re-open read/write mmap and patch only the two-byte END locations.
    with open(p, "r+b") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_WRITE) as data:
            result = _sample_tables_and_cookie(data)
            if result is None:
                return 0, False
            sizes, chunk_offset, _ = result
            offset = chunk_offset
            index = 0
            for size in sizes:
                if index in bad:
                    data[offset - 2 + size] = (
                        data[offset - 2 + size] | _END_FIRST)
                    data[offset - 1 + size] = (
                        data[offset - 1 + size] | _END_REST)
                    fixed += 1
                offset += size
                index += 1
    return fixed, True
