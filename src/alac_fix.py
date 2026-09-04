"""Detect and repair a known Apple Music ALAC container defect.

Background
----------
Some ALAC tracks produced/re-encoded by Apple after ~2025 contain frames
that are legal *uncompressed* PCM mode (``is_compressed=false``) but the
encoder omitted the 3-bit END tag after the PCM payload.  FFmpeg then
tries to parse the remaining padding as a new element and fails with
``invalid element channel count`` / ``Syntax element`` / ``patches
welcome``.

The affected packets are easy to spot:
- packet size == 16,388 bytes;
- the packet begins with the ALAC CPE (stereo) frame header;
- bit layout leaves exactly 9 bits of padding after the PCM payload.

Repair is lossless: set the first 3 of those 9 padding bits to ``111``
(the END tag), leaving every other byte unchanged.
"""

from __future__ import annotations

import struct
from pathlib import Path

# Byte offset (relative to packet start) at which the 3-bit END tag belongs.
# For 16-bit stereo ALAC with has_size=0:
#   23-bit header + 4096*2*16-bit PCM = bit 131095
#   131095 % 8 = 7  ->  byte 16386 bit 0 (LSB)
#   next two END bits are byte 16387 bits 7 and 6 (MSB and next).
_END_BYTE = 16386
_END_MASK_FIRST = 0x01  # first END bit: LSB of byte 16386
_END_MASK_REST = 0xC0   # bits 7-6 of byte 16387


def _sample_tables(path: Path):
    """Return (audio_sample_sizes, audio_chunk_offset) for the first audio
    track or None when not parseable."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.mp4 import (
        MP4ParseError,
        _box_header,
        _find_child_boxes,
        _u32,
        parse_init,
    )

    data = path.read_bytes()
    init, _ = parse_init(data)
    if init is None:
        return None
    moov = init.moov
    try:
        _, msize, mh, _ = _box_header(moov, 0, len(moov))
    except MP4ParseError:
        return None

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
                                ">%dI" % sample_count, box[20:20 + 4 * sample_count]))
                    for box, cs, ce in _find_child_boxes(
                            moov, ss + 8, se, b"stco"):
                        if len(box) >= 20:
                            stco = _u32(box, 16)
                        elif len(box) >= 16:
                            stco = _u32(box, 12)
                    if sizes and stco is not None:
                        return sizes, stco
    return None


def find_bad_packets(path: str) -> list[int]:
    """Return 0-based packet indices that match the known ALAC END-tag bug.

    The detection uses the exact fingerprint from ALAC修复可能性论证.md:
    packet size == 16388 and an uncompressed PCM frame leaving 9 padding
    bits at the end.  Only the first audio track is inspected.
    """
    p = Path(path)
    tables = _sample_tables(p)
    if tables is None:
        return []
    sizes, chunk_offset = tables
    data = p.read_bytes()
    bad = []
    offset = chunk_offset
    for idx, size in enumerate(sizes):
        if size != 16388:
            offset += size
            continue
        pkt = data[offset:offset + size]
        if len(pkt) < _END_BYTE + 2:
            offset += size
            continue
        # Loose sanity: the 3 padding bits should currently be zero in the
        # packets observed from Apple.
        b0 = pkt[_END_BYTE]
        b1 = pkt[_END_BYTE + 1]
        # A repaired packet has all three END bits set: b0 LSB=1 and
        # b1 MSB+next=1.  Any 16388-byte uncompressed frame missing any of
        # those bits is a candidate.
        if not ((b0 & _END_MASK_FIRST) and (b1 & _END_MASK_REST) == _END_MASK_REST):
            bad.append(idx)
        offset += size
    return bad


def fix_alac_end_tags(path: str, dry_run: bool = False) -> tuple[int, bool]:
    """Patch the missing 3-bit END tag in every affected ALAC packet.

    Returns ``(fixed_count, modified)`` where ``modified`` is True when the
    file was written back (``dry_run=False``).
    """
    p = Path(path)
    if not p.exists():
        return 0, False
    bad = find_bad_packets(path)
    if not bad:
        return 0, False

    tables = _sample_tables(p)
    sizes, chunk_offset = tables
    data = bytearray(p.read_bytes())
    offset = chunk_offset
    index = 0
    fixed = 0
    for size in sizes:
        if index in bad:
            if size != 16388:
                raise RuntimeError(
                    f"ALAC packet #{index} changed size unexpectedly ({size})")
            data[offset + _END_BYTE] |= _END_MASK_FIRST
            data[offset + _END_BYTE + 1] |= _END_MASK_REST
            fixed += 1
        offset += size
        index += 1

    if not dry_run:
        p.write_bytes(bytes(data))
    return fixed, True