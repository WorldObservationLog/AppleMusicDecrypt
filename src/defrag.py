"""Convert a fragmented MP4 (fMP4) into a progressive (regular) MP4.

Pure Python; no external binaries.

Why
---
Our ripping pipeline writes the decrypted stream as an fMP4: an init
segment (``ftyp`` + ``moov`` with empty sample tables plus ``mvex``)
followed by ``moof``/``mdat`` fragments.  Android's media framework and a
number of players handle this poorly (v2 used an ffmpeg re-mux to work
around exactly this).  ffmpeg with ``-c:a copy`` rebuilds a *progressive*
MP4: one ``moov`` carrying complete sample tables (``stts``/``stsz``/
``stco``) and a single ``mdat`` — the audio itself is untouched.

This module does the same in pure Python using the ISO-BMFF primitives in
``src/mp4.py``:

1. Read the init segment: keep ``ftyp``; transform the ``moov`` by
   dropping ``mvex`` and inserting real sample tables built from the
   fragments.
2. Walk every fragment (``moof``/``mdat``): collect per-sample
   durations/sizes and the decrypted payload.
3. Emit ``mdat`` with all samples concatenated, and patch ``stco`` offsets
   to point at the new single ``mdat``.

The result decodes bit-identically (stream copy — no re-encode) and plays
on Android.
"""

from __future__ import annotations

import struct

from src.mp4 import (
    FragmentInfo,
    InitInfo,
    MP4ParseError,
    parse_init,
    parse_next_fragment,
    rebuild_fragment_bytes,
)


# ---------------------------------------------------------------------------
# Low-level box writers
# ---------------------------------------------------------------------------

def _box(btype: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + btype + payload


def _full_box(btype: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return _box(btype, struct.pack(">I", (version << 24) | flags) + payload)


def _u32(v: int) -> bytes:
    return struct.pack(">I", v)


def _u16(v: int) -> bytes:
    return struct.pack(">H", v)


def _u64(v: int) -> bytes:
    return struct.pack(">Q", v)


def _find_box(data: bytes, btype: bytes, start: int = 0, end: int | None = None):
    """Return (payload_start, payload_end, full_box_bytes) of *btype* at the
    top level of *data[start:end]*, or None."""
    if end is None:
        end = len(data)
    off = start
    while off + 8 <= end:
        size = struct.unpack(">I", data[off:off + 4])[0]
        b = data[off + 4:off + 8]
        header = 8
        if size == 1:
            if off + 16 > end:
                return None
            size = struct.unpack(">Q", data[off + 8:off + 16])[0]
            header = 16
        elif size == 0:
            size = end - off
        if size < header or off + size > end:
            return None
        if b == btype:
            return off + header, off + size, data[off:off + size]
        off += size
    return None


def _children(data: bytes):
    """Yield (btype, payload_start, payload_end, header_size) of top-level
    boxes in *data*."""
    off = 0
    n = len(data)
    while off + 8 <= n:
        size = struct.unpack(">I", data[off:off + 4])[0]
        b = data[off + 4:off + 8]
        header = 8
        if size == 1:
            size = struct.unpack(">Q", data[off + 8:off + 16])[0]
            header = 16
        elif size == 0:
            size = n - off
        if size < header or off + size > n:
            return
        yield b, off + header, off + size, header
        off += size


def _extract_child(container: bytes, btype: bytes) -> bytes | None:
    """Return the full bytes of the first child box *btype* in *container*."""
    for b, ps, pe, hs in _children(container):
        if b == btype:
            return container[ps - hs:pe]
    return None


# ---------------------------------------------------------------------------
# moov surgery
# ---------------------------------------------------------------------------

def _parse_mvhd_times(moov: bytes):
    # NOTE: moov includes its own 8-byte header; _find_box starts scanning
    # at the first *child* box.
    header = 8
    r = _find_box(moov, b"mvhd", start=header)
    if r is None:
        return None, None
    ps, pe, _full = r
    vaf = struct.unpack(">I", moov[ps:ps + 4])[0]
    version = vaf >> 24
    timescale = struct.unpack(">I", moov[ps + 12:ps + 16])[0]
    if version == 1:
        creation = struct.unpack(">Q", moov[ps + 4:ps + 12])[0]
        modification = struct.unpack(">Q", moov[ps + 12:ps + 20])[0]
        duration = struct.unpack(">Q", moov[ps + 24:ps + 32])[0]
    else:
        creation = struct.unpack(">I", moov[ps + 4:ps + 8])[0]
        modification = struct.unpack(">I", moov[ps + 8:ps + 12])[0]
        duration = struct.unpack(">I", moov[ps + 16:ps + 20])[0]
    return {"timescale": timescale, "creation": creation,
            "modification": modification, "duration": duration}, timescale


def _make_stts(runs: list[tuple[int, int]]) -> bytes:
    """runs: list of (count, delta)."""
    payload = _u32(len(runs))
    for count, delta in runs:
        payload += _u32(count) + _u32(delta)
    return _full_box(b"stts", 0, 0, payload)


def _make_stsz(sizes: list[int]) -> bytes:
    if not sizes:
        uniform = 0
    else:
        uniform = sizes[0] if all(s == sizes[0] for s in sizes) else 0
    payload = _u32(uniform) + _u32(len(sizes))
    if uniform:
        return _full_box(b"stsz", 0, 0, payload)
    payload += b"".join(_u32(s) for s in sizes)
    return _full_box(b"stsz", 0, 0, payload)


def _make_stco(offset: int) -> bytes:
    return _full_box(b"stco", 0, 0, _u32(1) + _u32(offset))


def _make_stsc(sample_count: int, desc_index: int = 1) -> bytes:
    # one chunk, all samples, description index 1, no per-sample desc
    return _full_box(b"stsc", 0, 0,
                     _u32(1) + _u32(1) + _u32(desc_index) + _u32(sample_count) + _u32(1))


def _patch_box_duration(box: bytes, duration: int) -> bytes:
    """Patch the duration field of mvhd/tkhd/mdhd (handles v0/v1)."""
    vaf = struct.unpack(">I", box[4:8])[0]
    version = vaf >> 24
    m = bytearray(box)
    if version == 1:
        # duration is the last 8 bytes before the trailing reserved/box end
        # mvhd v1: creation(8) modification(8) timescale(4) duration(8)
        if len(m) >= 40 and box[4:8] == b"\x01\x00\x00\x00":
            struct.pack_into(">Q", m, 28, duration)
        # tkhd v1 duration at offset 108 typically; we patch genericly below
        else:
            struct.pack_into(">Q", m, len(m) - 8, duration)
    else:
        if len(m) >= 28 and box[4:8] == b"\x00\x00\x00\x00":
            struct.pack_into(">I", m, 20, duration)
        else:
            struct.pack_into(">I", m, len(m) - 4, duration)
    return bytes(m)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def defragment_file(path: str) -> bytes:
    """Read a fragmented MP4 from *path* and return progressive MP4 bytes.

    The file layout matches our streaming output: an init segment
    (``ftyp`` + ``moov``) followed by ``moof``/``mdat`` pairs whose payloads
    are already decrypted (the streaming pipeline strips the encryption
    boxes before writing).
    """
    with open(path, "rb") as f:
        data = f.read()
    return defragment_bytes(data)


def defragment_bytes(data: bytes) -> bytes:
    """Rebuild a fragmented MP4 (init + fragments) as a progressive MP4.

    Fragment payloads must already be decrypted.
    Returns the complete progressive MP4 file bytes.
    """
    init, off = parse_init(data)
    if init is None:
        raise MP4ParseError("defragment: no init segment found")

    sample_sizes: list[int] = []
    sample_deltas: list[int] = []
    payload_parts: list[bytes] = []

    seq = 0
    while off < len(data):
        frag, off = parse_next_fragment(data, off, seq)
        if frag is None:
            break
        seq += 1
        for spec in frag.samples:
            chunk = frag.mdat_payload[spec.offset:spec.offset + spec.length]
            payload_parts.append(chunk)
            sample_sizes.append(spec.length)
            sample_deltas.append(spec.duration or 0)

    if not sample_sizes:
        raise MP4ParseError("no samples found in fragments")

    total_samples = len(sample_sizes)
    total_duration = sum(sample_deltas)

    # ---- timescale --------------------------------------------------------
    mvhd_info, mvhd_timescale = _parse_mvhd_times(init.moov)
    if mvhd_timescale is None:
        raise MP4ParseError("init moov lacks mvhd/timescale")

    # media (mdhd) timescale of the first trak — sample deltas live on this
    # timebase.
    media_timescale = 0
    for b, ps, pe, hs in _children(init.moov[8:]):
        ps += 8
        pe += 8
        if b != b"trak":
            continue
        trak = init.moov[ps - hs:pe]
        for b2, ps2, pe2, hs2 in _children(trak[8:]):
            ps2 += 8
            pe2 += 8
            if b2 == b"mdia":
                mdia = trak[ps2 - hs2:pe2]
                for b3, ps3, pe3, hs3 in _children(mdia[8:]):
                    ps3 += 8
                    pe3 += 8
                    if b3 == b"mdhd":
                        mdhd = mdia[ps3 - hs3:pe3]
                        media_timescale = struct.unpack(
                            ">I", mdhd[hs3 + 12:hs3 + 16])[0]
                        break
            if media_timescale:
                break
        if media_timescale:
            break
    if not media_timescale:
        media_timescale = mvhd_timescale

    # ---- build sample tables ---------------------------------------------
    # stts: compress consecutive equal deltas into runs
    runs: list[tuple[int, int]] = []
    for d in sample_deltas:
        if runs and runs[-1][1] == d:
            runs[-1] = (runs[-1][0] + 1, d)
        else:
            runs.append((1, d))
    stts = _make_stts(runs)
    stsz = _make_stsz(sample_sizes)

    # ---- layout: mdat starts after ftyp + moov ---------------------------
    # We need the moov size before writing stco (which contains the mdat
    # offset).  Compute moov size iteratively: stco holds a fixed-size u32
    # so its own size doesn't depend on the offset value.
    stsc = _make_stsc(total_samples, desc_index=1)

    ftyp = init.ftyp

    if mvhd_timescale and mvhd_timescale != media_timescale:
        movie_duration = int(total_duration * mvhd_timescale / media_timescale)
    else:
        movie_duration = total_duration

    # Patch the moov: remove mvex, insert sample tables into stbl.
    moov = _patch_moov(init.moov, stts, stsc, stsz, stco_placeholder=None,
                       movie_duration=movie_duration,
                       media_duration=total_duration)

    mdat_offset = len(ftyp) + len(moov) + 8  # +8 for the mdat header itself

    stco = _make_stco(mdat_offset)
    moov = _patch_moov(init.moov, stts, stsc, stsz, stco_placeholder=stco,
                       movie_duration=movie_duration,
                       media_duration=total_duration)
    # moov size may change by 0 bytes since stco is fixed size; verify
    if len(moov) != mdat_offset - len(ftyp) - 8:
        # recompute once more with the actual moov size
        mdat_offset = len(ftyp) + len(moov) + 8
        stco = _make_stco(mdat_offset)
        moov = _patch_moov(init.moov, stts, stsc, stsz, stco_placeholder=stco,
                           movie_duration=movie_duration,
                           media_duration=total_duration)

    mdat_payload = b"".join(payload_parts)
    mdat = _box(b"mdat", mdat_payload)

    return ftyp + moov + mdat


def _patch_moov(moov: bytes, stts: bytes, stsc: bytes, stsz: bytes,
                stco_placeholder: bytes | None,
                movie_duration: int, media_duration: int) -> bytes:
    """Rebuild the init moov as a progressive moov.

    - drop ``mvex``;
    - patch mvhd/tkhd/mdhd durations to *total_duration*;
    - insert stts/stsc/stsz/stco into stbl (replacing any placeholder
      empty sample tables the init carried).
    """
    out = bytearray()
    out += struct.pack(">I", 0) + b"moov"  # size patched at the end

    # moov carries its own header; iterate children starting past it.
    for b, ps, pe, hs in _children(moov[8:]):
        ps += 8
        pe += 8
        if b == b"mvex":
            continue  # no implicit sample tables in a progressive file
        if b == b"mvhd":
            m = bytearray(moov[ps - hs:pe])
            vaf = struct.unpack(">I", m[hs:hs + 4])[0]
            # mvhd v0: ver/flags(4) creation(4) mod(4) timescale(4) dur(4)
            # mvhd v1: ver/flags(4) creation(8) mod(8) timescale(4) dur(8)
            if vaf >> 24 == 1:
                struct.pack_into(">Q", m, hs + 32, movie_duration)
            else:
                struct.pack_into(">I", m, hs + 16, movie_duration)
            out += m
            continue

        if b == b"trak":
            # Patch tkhd/mdhd durations, inject sample tables into stbl.
            out += _patch_trak(moov[ps - hs:pe], stts, stsc, stsz,
                               stco_placeholder, media_duration)
            continue

        out += moov[ps - hs:pe]

    struct.pack_into(">I", out, 0, len(out))
    return bytes(out)


def _patch_trak(trak: bytes, stts: bytes, stsc: bytes, stsz: bytes,
                stco_placeholder: bytes | None, media_duration: int) -> bytes:
    out = bytearray()
    out += struct.pack(">I", 0) + b"trak"

    # trak carries its own 8-byte header; children start past it.
    for b, ps, pe, hs in _children(trak[8:]):
        ps += 8
        pe += 8
        if b == b"tkhd":
            m = bytearray(trak[ps - hs:pe])
            vaf = struct.unpack(">I", m[hs:hs + 4])[0]
            # tkhd v0: ver/flags(4) creation(4) mod(4) trackID(4) resv(4) dur(4)
            # tkhd v1: ver/flags(4) creation(8) mod(8) trackID(4) resv(4) dur(8)
            if vaf >> 24 == 1:
                struct.pack_into(">Q", m, hs + 32, media_duration)
            else:
                struct.pack_into(">I", m, hs + 20, media_duration)
            out += m
            continue

        if b == b"mdia":
            out += _patch_mdia(mdia := trak[ps - hs:pe], stts, stsc, stsz,
                               stco_placeholder, media_duration)
            continue

        out += trak[ps - hs:pe]

    struct.pack_into(">I", out, 0, len(out))
    return bytes(out)


def _patch_mdia(mdia: bytes, stts: bytes, stsc: bytes, stsz: bytes,
                stco_placeholder: bytes | None, media_duration: int) -> bytes:
    out = bytearray()
    out += struct.pack(">I", 0) + b"mdia"

    for b, ps, pe, hs in _children(mdia[8:]):
        ps += 8
        pe += 8
        if b == b"mdhd":
            m = bytearray(mdia[ps - hs:pe])
            vaf = struct.unpack(">I", m[hs:hs + 4])[0]
            # mdhd v0: ver/flags(4) creation(4) mod(4) timescale(4) dur(4)
            # mdhd v1: ver/flags(4) creation(8) mod(8) timescale(4) dur(8)
            if vaf >> 24 == 1:
                struct.pack_into(">Q", m, hs + 32, media_duration)
            else:
                struct.pack_into(">I", m, hs + 16, media_duration)
            out += m
            continue

        if b == b"minf":
            minf = mdia[ps - hs:pe]
            out += _patch_minf(minf, stts, stsc, stsz, stco_placeholder)
            continue

        out += mdia[ps - hs:pe]

    struct.pack_into(">I", out, 0, len(out))
    return bytes(out)


def _patch_minf(minf: bytes, stts: bytes, stsc: bytes, stsz: bytes,
                stco_placeholder: bytes | None) -> bytes:
    out = bytearray()
    out += struct.pack(">I", 0) + b"minf"

    for b, ps, pe, hs in _children(minf[8:]):
        ps += 8
        pe += 8
        if b == b"stbl":
            stbl = minf[ps - hs:pe]
            out += _patch_stbl(stbl, stts, stsc, stsz, stco_placeholder)
            continue
        out += minf[ps - hs:pe]

    struct.pack_into(">I", out, 0, len(out))
    return bytes(out)


def _patch_stbl(stbl: bytes, stts: bytes, stsc: bytes, stsz: bytes,
                stco_placeholder: bytes | None) -> bytes:
    out = bytearray()
    out += struct.pack(">I", 0) + b"stbl"

    for b, ps, pe, hs in _children(stbl[8:]):
        ps += 8
        pe += 8
        if b in (b"stts", b"stsc", b"stsz", b"stco", b"co64"):
            continue  # replaced below
        out += stbl[ps - hs:pe]

    out += stts
    out += stsc
    out += stsz
    if stco_placeholder is not None:
        out += stco_placeholder

    struct.pack_into(">I", out, 0, len(out))
    return bytes(out)
