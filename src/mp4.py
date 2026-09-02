"""Pure-Python ISO-BMFF / fragmented MP4 (fMP4) parser, transformer and rebuilder.

Stdlib only (struct/memoryview); no third-party imports.

Implements the v3 contract in docs/v3-INTERFACES.md section "2) src/mp4.py",
with semantics aligned to the Go reference implementation
(zhaarey/apple-music-downloader utils/runv4/runv4.go, mp4ff based).

Key behaviour
-------------
* Generic box iteration: 32-bit size, ``size == 1`` (64-bit large size),
  ``size == 0`` (extends to end of stream) and ``uuid`` boxes
  (16-byte usertype).
* Init segment = ``ftyp`` + ``moov``. Per-track decryption info is extracted
  from ``moov/trak/mdia/minf/stbl/stsd`` sample entries (``enca`` with
  ``sinf {schm, schi {tenc}}``); codec and decoder params are derived from the
  codec child boxes inside the sample entry (``alac`` / ``mp4a`` (containing
  ``esds``) / ``ec-3``/``ac-3`` (containing ``dac3``)).
* Transform init for output: re-type ``enca -> alac|mp4a|ec-3|ac-3`` (by the
  actual codec), strip ``sinf``, remove encryption ``sbgp``/``sgpd``
  (grouping_type ``seig``/``seam``) in ``stbl``, dedupe stsd to 1 entry when
  2 identical entries exist, keep decoder params. Produces ``output_init``.
* Fragments: ``moof`` (mfhd + traf{tfhd, tfdt, trun, senc[, saiz, saio, sbgp,
  sgpd, pssh]}) + ``mdat``. Sample layout comes from trun and senc; ``senc``
  may live directly in traf or inside a PIFF ``uuid`` box — both are handled.
* Rebuild a fragment: remove encryption boxes from moof
  (``senc/saiz/saio/sbgp/sgpd`` + ``pssh``), adjust each ``trun.data_offset``
  by the total removed bytes, re-emit ``moof`` then ``mdat`` (payload replaced
  by decrypted bytes).

Assumptions / notes (see also the report)
------------------------------------------
* Audio sample entries use the ISO layout: 8-byte generic sample-entry header
  + 20 bytes of audio fields (28 bytes before child boxes). This holds for
  Apple Music files (enca/alac/mp4a/ec-3/ac-3). QuickTime v1/v2 sound
  description extensions are not handled.
* The PIFF ``senc`` UUID is ``a2394f52-5a9b-4f14-a244-6c427c648df4``.
* When the per-sample IV size is unknown (batch path without an init segment)
  it is inferred from the senc payload and, when present, the saiz box.
  The streaming parser uses the authoritative tenc IV size from the init.
* ``rebuild_fragment_bytes`` re-emits the ``mdat`` box with a 32-bit header
  wrapping the decrypted payload; trun data offsets are corrected by the
  number of removed bytes. (Original mdats in Apple Music are 32-bit.)
* ``aac-binaural``/``aac-downmix`` are m3u8-level distinctions; the sample
  entry only reveals ``mp4a``, so ``TrackInfo.codec`` reports ``'aac'``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

__all__ = [
    "MP4ParseError",
    "SubSamplePattern",
    "SampleSpec",
    "TrackInfo",
    "InitInfo",
    "FragmentInfo",
    "parse_init",
    "parse_next_fragment",
    "rebuild_fragment_bytes",
    "patch_mvhd_times",
    "mac_epoch_to_datetime",
    "StreamingMP4Parser",
]

# --------------------------------------------------------------------------
# Public exceptions / dataclasses (exactly the contract in v3-INTERFACES.md)
# --------------------------------------------------------------------------


class MP4ParseError(Exception):
    """Raised on malformed ISO-BMFF input. Messages include offsets / box ids."""


@dataclass
class SubSamplePattern:
    bytes_of_clear_data: int
    bytes_of_protected_data: int


@dataclass
class SampleSpec:
    desc_index: int
    offset: int                      # offset within this fragment's mdat payload
    length: int
    iv: bytes | None                 # None if unencrypted sample
    sub_sample_patterns: list[SubSamplePattern] = field(default_factory=list)
    duration: int | None = None
    cts: int | None = None


@dataclass
class TrackInfo:
    track_id: int
    codec: str                       # 'alac'|'aac'|'ec3'|'ac3'|'aac-binaural'|'aac-downmix'|'unknown'
    decoder_params: bytes | None     # ALAC: the 'alac' child atom; AAC: the 'esds' box; EC3: 'dac3' box
    iv_size: int = 0                 # tenc default_per_sample_iv_size
    crypt_byte_block: int = 0        # tenc default_crypt_byte_block
    skip_byte_block: int = 0         # tenc default_skip_byte_block
    default_kid: bytes | None = None
    constant_iv: bytes | None = None  # tenc default_constant_iv (cbcs without per-sample IVs)
    scheme_type: str | None = None   # 'cenc' | 'cbcs' | ... from sinf/schm
    protected: bool = False


@dataclass
class InitInfo:
    ftyp: bytes
    moov: bytes
    tracks: dict[int, TrackInfo]     # by track_id
    output_init: bytes               # ftyp + transformed moov
    creation_time: int | None        # mvhd (Mac epoch seconds)
    modification_time: int | None
    next_offset: int                 # offset just past moov


@dataclass
class FragmentInfo:
    seq: int
    rebuilt_moof: bytes              # moof with encryption boxes removed & data offsets fixed
    mdat_payload: bytes              # raw (encrypted) mdat payload
    samples: list[SampleSpec]        # file order
    next_offset: int                 # offset just past mdat


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

_MAC_EPOCH = datetime(1904, 1, 1)
_PIFF_SENC_UUID = bytes.fromhex("a2394f525a9b4f14a2446c427c648df4")

# codec string -> sample entry type used after transformation
_CODE_TO_ENTRY = {
    "alac": b"alac",
    "aac": b"mp4a",
    "ec3": b"ec-3",
    "ac3": b"ac-3",
    "avc1": b"avc1",
    "hvc1": b"hvc1",
    "hev1": b"hev1",
    "vp09": b"vp09",
}
# sample entry type -> base codec string
_CODEC_ENTRY_TYPES = {
    b"alac": "alac",
    b"mp4a": "aac",
    b"ec-3": "ec3",
    b"ac-3": "ac3",
    b"avc1": "avc1",
    b"hvc1": "hvc1",
    b"hev1": "hev1",
    b"vp09": "vp09",
}
# sample entry types with the 70-byte video header (children start after 78)
_VIDEO_ENTRY_TYPES = {b"encv", b"avc1", b"hvc1", b"hev1", b"vp09", b"encv"}
# video codec-config child box -> codec string
_VIDEO_CONFIG_TYPES = {
    b"avcC": "avc1",
    b"hvcC": "hvc1",
    b"vpcC": "vp09",
}

# Box types treated as containers when building transform / rebuild trees
_TRANSFORM_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsd"}
_FRAGMENT_CONTAINERS = {b"moof", b"traf"}


# --------------------------------------------------------------------------
# Low-level byte helpers
# --------------------------------------------------------------------------


def _u16(data, off):
    return struct.unpack_from(">H", data, off)[0]


def _u32(data, off):
    return struct.unpack_from(">I", data, off)[0]


def _s32(data, off):
    return struct.unpack_from(">i", data, off)[0]


def _u64(data, off):
    return struct.unpack_from(">Q", data, off)[0]


def _type_str(t: bytes) -> str:
    try:
        return t.decode("latin1")
    except Exception:  # pragma: no cover - defensive
        return repr(t)


def _box_header(data, offset: int, end: int | None = None):
    """Parse a box header at *offset*.

    Returns ``(box_type: bytes, size: int, header_size: int, usertype: bytes|None)``.
    ``size == 0`` means "extends to end" (resolved against *end*/len(data)).
    Raises :class:`MP4ParseError` on malformed or truncated headers.
    """
    if offset + 8 > len(data):
        raise MP4ParseError(
            f"truncated box header at offset {offset} (need 8 bytes, have {len(data) - offset})")
    size32 = _u32(data, offset)
    btype = bytes(data[offset + 4:offset + 8])
    header_size = 8
    usertype = None
    if size32 == 1:
        if offset + 16 > len(data):
            raise MP4ParseError(f"truncated 64-bit box header at offset {offset}")
        size = _u64(data, offset + 8)
        header_size = 16
    elif size32 == 0:
        if end is None:
            end = len(data)
        size = end - offset
    else:
        size = size32
    if btype == b"uuid":
        if offset + header_size + 16 > len(data):
            raise MP4ParseError(f"truncated uuid box header at offset {offset}")
        usertype = bytes(data[offset + header_size:offset + header_size + 16])
        header_size += 16
    if size < header_size:
        raise MP4ParseError(
            f"box '{_type_str(btype)}' at offset {offset} has size {size} < header {header_size}")
    return btype, size, header_size, usertype


@dataclass
class _Box:
    type: bytes
    start: int
    size: int
    header_size: int
    usertype: bytes | None = None

    @property
    def payload_start(self) -> int:
        return self.start + self.header_size

    @property
    def payload_end(self) -> int:
        return self.start + self.size

    @property
    def end(self) -> int:
        return self.start + self.size


def _iter_children(data, start: int, end: int):
    """Yield :class:`_Box` for each child box in ``[start, end)``."""
    pos = start
    while pos < end:
        btype, size, header_size, usertype = _box_header(data, pos, end)
        if pos + size > end:
            raise MP4ParseError(
                f"child box '{_type_str(btype)}' at offset {pos} overruns parent end {end}")
        yield _Box(btype, pos, size, header_size, usertype)
        pos += size


def _make_box(btype: bytes, prefix: bytes, payload: bytes, usertype: bytes | None = None) -> bytes:
    """Build a box with a freshly computed size (32-bit unless it does not fit)."""
    body = prefix + payload
    hdr_extra = 16 if usertype is not None else 0
    if len(body) + 8 + hdr_extra < 0xFFFFFFFF:
        hdr = struct.pack(">I4s", 8 + hdr_extra + len(body), btype)
    else:
        hdr = struct.pack(">I4sQ", 1, btype, 16 + hdr_extra + len(body))
    if usertype is not None:
        hdr += usertype
    return hdr + body


class _Node:
    """Box tree node used by the transform / rebuild (byte-exact preservation).

    ``raw`` keeps the original full box bytes; untouched nodes are re-emitted
    verbatim. Only dirty nodes are re-serialised from ``prefix`` + children.
    """

    __slots__ = ("btype", "raw", "children", "prefix", "usertype", "dirty", "parent")

    def __init__(self, btype, raw=None, children=None, prefix=None, usertype=None, parent=None):
        self.btype = btype
        self.raw = raw
        self.children = children
        self.prefix = prefix if prefix is not None else b""
        self.usertype = usertype
        self.dirty = False
        self.parent = parent

    def mark_dirty(self):
        n = self
        while n is not None and not n.dirty:
            n.dirty = True
            n = n.parent


def _encode_node(node: _Node) -> bytes:
    if node.raw is not None and not node.dirty:
        return node.raw
    if node.children is None:
        payload = b""
    else:
        payload = b"".join(_encode_node(c) for c in node.children)
    return _make_box(node.btype, node.prefix, payload, node.usertype)


def _raw_node(raw: bytes) -> _Node:
    btype, size, header_size, usertype = _box_header(raw, 0, len(raw))
    return _Node(btype, raw=raw, usertype=usertype)


def _grouping_is_encryption(raw: bytes) -> bool:
    """True when a raw sbgp/sgpd box has grouping_type 'seig' or 'seam'."""
    try:
        btype, size, header_size, _ = _box_header(raw, 0, len(raw))
    except MP4ParseError:
        return False
    if btype not in (b"sbgp", b"sgpd"):
        return False
    if header_size + 8 > len(raw):
        return False
    g = bytes(raw[header_size + 4:header_size + 8])
    return g == b"seig" or g == b"seam"


# --------------------------------------------------------------------------
# Sample entry / sinf / tenc parsing
# --------------------------------------------------------------------------


@dataclass
class _SampleEntryParse:
    codec: str
    decoder_params: bytes | None
    frma: bytes | None
    scheme_type: bytes | None
    tenc_iv_size: int
    tenc_crypt_byte_block: int
    tenc_skip_byte_block: int
    tenc_default_kid: bytes | None
    tenc_constant_iv: bytes | None
    protected: bool
    new_type: bytes | None          # target sample-entry type for the transform
    kept_children: list[bytes]      # child boxes to keep after removing encryption
    entry_header_size: int
    entry_header_len: int           # 28 (audio) or 78 (video) fixed bytes before children


def _find_child_box(raw: bytes, want: bytes) -> bytes | None:
    """Locate a child box of type *want* inside a raw box.

    Codec sample entries (mp4a/ec-3/ac-3) carry 28 fixed bytes before their
    children, so we scan from the generic child offset and, if not found,
    from the sample-entry offset (header + 28).
    """
    try:
        _, size, header_size, _ = _box_header(raw, 0, len(raw))
    except MP4ParseError:
        return None
    for start in (header_size, header_size + 28):
        pos = start
        while pos + 8 <= len(raw):
            try:
                cb, csz, chdr, _ = _box_header(raw, pos, len(raw))
            except MP4ParseError:
                break
            if pos + csz > len(raw):
                break
            if cb == want:
                return bytes(raw[pos:pos + csz])
            pos += csz
    return None


def _parse_tenc_raw(raw: bytes) -> dict | None:
    try:
        _, size, header_size, _ = _box_header(raw, 0, len(raw))
    except MP4ParseError:
        return None
    if header_size + 24 > len(raw):
        return None
    vaf = _u32(raw, header_size)
    version = vaf >> 24
    p = header_size + 4
    p += 1  # reserved
    crypt = skip = 0
    if version > 0:
        cs = raw[p]
        crypt = cs >> 4
        skip = cs & 0x0F
    p += 1
    is_protected = raw[p]
    p += 1
    iv_size = raw[p]
    p += 1
    kid = bytes(raw[p:p + 16])
    p += 16
    constant_iv = None
    if version > 0 and p < len(raw):
        csize = raw[p]
        p += 1
        if csize > 0 and p + csize <= len(raw):
            constant_iv = bytes(raw[p:p + csize])
    return {
        "version": version,
        "is_protected": is_protected,
        "iv_size": iv_size,
        "crypt_byte_block": crypt,
        "skip_byte_block": skip,
        "default_kid": kid,
        "constant_iv": constant_iv,
    }


def _parse_schi_raw(raw: bytes) -> dict | None:
    try:
        _, size, header_size, _ = _box_header(raw, 0, len(raw))
    except MP4ParseError:
        return None
    pos = header_size
    while pos + 8 <= len(raw):
        try:
            cb, csz, chdr, _ = _box_header(raw, pos, len(raw))
        except MP4ParseError:
            break
        if pos + csz > len(raw):
            break
        if cb == b"tenc":
            return _parse_tenc_raw(raw[pos:pos + csz])
        pos += csz
    return None


def _parse_sinf_raw(raw: bytes):
    """Parse a raw sinf box -> (frma, scheme_type, tenc_dict|None)."""
    frma = None
    scheme_type = None
    tenc = None
    try:
        _, size, header_size, _ = _box_header(raw, 0, len(raw))
    except MP4ParseError:
        return frma, scheme_type, tenc
    pos = header_size
    while pos + 8 <= len(raw):
        try:
            cb, csz, chdr, _ = _box_header(raw, pos, len(raw))
        except MP4ParseError:
            break
        if pos + csz > len(raw):
            break
        if cb == b"frma":
            if chdr + 4 <= csz:
                frma = bytes(raw[pos + chdr:pos + chdr + 4])
        elif cb == b"schm":
            if chdr + 8 <= csz:
                scheme_type = bytes(raw[pos + chdr + 4:pos + chdr + 8])
        elif cb == b"schi":
            tenc = _parse_schi_raw(raw[pos:pos + csz])
        pos += csz
    return frma, scheme_type, tenc


def _parse_sample_entry_raw(raw: bytes) -> _SampleEntryParse | None:
    """Analyse one raw sample entry box (enca/encv/alac/mp4a/avc1/ec-3/...)."""
    try:
        btype, size, header_size, _ = _box_header(raw, 0, len(raw))
    except MP4ParseError:
        return None
    if size > len(raw):
        return None

    # Audio entries carry 28 fixed bytes before their children, video 70.
    is_video = btype in _VIDEO_ENTRY_TYPES
    header_len = 78 if is_video else 28
    children: list[tuple[bytes, bytes]] = []
    pos = header_size + header_len
    while pos + 8 <= len(raw):
        try:
            cb, csz, chdr, _ = _box_header(raw, pos, len(raw))
        except MP4ParseError:
            break
        if pos + csz > len(raw):
            break
        children.append((cb, bytes(raw[pos:pos + csz])))
        pos += csz

    frma = None
    scheme_type = None
    tenc = None
    has_sinf = False
    codec_child = None       # 'alac' | 'aac'(esds) | 'dac3' | 'avc1'(avcC) | ...
    config_raw = None        # decoder-params box bytes (alac/esds/dac3/avcC/...)
    nested = None            # (box_type, raw) for a nested codec sample entry

    for cb, cr in children:
        if cb == b"sinf":
            has_sinf = True
            frma, scheme_type, tenc = _parse_sinf_raw(cr)
        elif cb == b"alac":
            codec_child = "alac"
            config_raw = cr
        elif cb == b"esds":
            codec_child = "aac"
            config_raw = cr
        elif cb == b"dac3":
            codec_child = "dac3"
            config_raw = cr
        elif cb in _VIDEO_CONFIG_TYPES:
            codec_child = _VIDEO_CONFIG_TYPES[cb]
            config_raw = cr
        elif cb in (b"mp4a", b"ec-3", b"ac-3") or cb in _VIDEO_ENTRY_TYPES:
            nested = (cb, cr)

    final_codec = "unknown"
    config = config_raw
    used_nested = None

    if codec_child == "alac":
        final_codec = "alac"
    elif codec_child == "aac":
        final_codec = "aac"
    elif codec_child == "dac3":
        if btype == b"ac-3" or frma == b"ac-3":
            final_codec = "ac3"
        else:
            final_codec = "ec3"
    elif codec_child in ("avc1", "hvc1", "vp09"):
        final_codec = codec_child
    elif nested is not None:
        nt, nr = nested
        if nt == b"mp4a":
            final_codec = "aac"
            inner = _find_child_box(nr, b"esds")
            if inner is not None:
                config = inner
            used_nested = nr
        elif nt in (b"ec-3", b"ac-3"):
            final_codec = "ec3" if nt == b"ec-3" else "ac3"
            inner = _find_child_box(nr, b"dac3")
            if inner is not None:
                config = inner
            used_nested = nr
        elif nt in (b"avc1", b"hvc1", b"hev1", b"vp09"):
            final_codec = _CODEC_ENTRY_TYPES[nt]
            for want in (b"avcC", b"hvcC", b"vpcC"):
                inner = _find_child_box(nr, want)
                if inner is not None:
                    config = inner
                    break
            used_nested = nr
    elif btype in _CODEC_ENTRY_TYPES:
        final_codec = _CODEC_ENTRY_TYPES[btype]
    elif frma is not None:
        final_codec = _CODEC_ENTRY_TYPES.get(frma, "unknown")

    new_type = _CODE_TO_ENTRY.get(final_codec)
    kept: list[bytes] = []
    for cb, cr in children:
        if cb == b"sinf":
            continue
        if used_nested is not None and cr == used_nested:
            continue
        kept.append(cr)
    if config is not None and config not in kept:
        kept.append(config)

    return _SampleEntryParse(
        codec=final_codec,
        decoder_params=config,
        frma=frma,
        scheme_type=scheme_type,
        tenc_iv_size=tenc["iv_size"] if tenc else 0,
        tenc_crypt_byte_block=tenc["crypt_byte_block"] if tenc else 0,
        tenc_skip_byte_block=tenc["skip_byte_block"] if tenc else 0,
        tenc_default_kid=tenc["default_kid"] if tenc else None,
        tenc_constant_iv=tenc["constant_iv"] if tenc else None,
        protected=has_sinf,
        new_type=new_type,
        kept_children=kept,
        entry_header_size=header_size,
        entry_header_len=header_len,
    )


# --------------------------------------------------------------------------
# moov parsing (tracks / times)
# --------------------------------------------------------------------------


def _parse_mvhd_times(data, box):
    payload = box.payload_start
    if payload + 4 > box.payload_end:
        raise MP4ParseError(f"mvhd at offset {box.start} too small")
    version = data[payload]
    if version == 1:
        if payload + 20 > box.payload_end:
            raise MP4ParseError(f"mvhd v1 at offset {box.start} too small")
        return _u64(data, payload + 4), _u64(data, payload + 12)
    if payload + 12 > box.payload_end:
        raise MP4ParseError(f"mvhd at offset {box.start} too small")
    return _u32(data, payload + 4), _u32(data, payload + 8)


def _parse_tkhd(data, box) -> int | None:
    payload = box.payload_start
    if payload + 4 > box.payload_end:
        return None
    version = data[payload]
    if version == 1:
        if payload + 24 > box.payload_end:
            return None
        return _u64(data, payload + 20)
    if payload + 16 > box.payload_end:
        return None
    return _u32(data, payload + 12)


def _descend_first(data, box, path):
    cur = box
    for want in path:
        found = None
        for child in _iter_children(data, cur.payload_start, cur.payload_end):
            if child.type == want:
                found = child
                break
        if found is None:
            return None
        cur = found
    return cur


def _parse_trak_into(data, trak_box, tracks: dict):
    track_id = None
    stsd_box = None
    for child in _iter_children(data, trak_box.payload_start, trak_box.payload_end):
        if child.type == b"tkhd":
            track_id = _parse_tkhd(data, child)
        elif child.type == b"mdia":
            stsd_box = _descend_first(data, child, [b"minf", b"stbl", b"stsd"])
    if track_id is None or stsd_box is None:
        return
    if stsd_box.payload_start + 8 > stsd_box.payload_end:
        return
    for entry in _iter_children(data, stsd_box.payload_start + 8, stsd_box.payload_end):
        entry_raw = bytes(data[entry.start:entry.end])
        p = _parse_sample_entry_raw(entry_raw)
        if p is None:
            continue
        tracks[track_id] = TrackInfo(
            track_id=track_id,
            codec=p.codec,
            decoder_params=p.decoder_params,
            iv_size=p.tenc_iv_size,
            crypt_byte_block=p.tenc_crypt_byte_block,
            skip_byte_block=p.tenc_skip_byte_block,
            default_kid=p.tenc_default_kid,
            constant_iv=p.tenc_constant_iv,
            scheme_type=p.scheme_type.decode("latin1") if p.scheme_type else None,
            protected=p.protected,
        )
        break


def _parse_moov(data, moov_box):
    tracks: dict[int, TrackInfo] = {}
    creation = None
    modification = None
    for child in _iter_children(data, moov_box.payload_start, moov_box.payload_end):
        if child.type == b"mvhd":
            creation, modification = _parse_mvhd_times(data, child)
        elif child.type == b"trak":
            _parse_trak_into(data, child, tracks)
    return tracks, creation, modification


def _build_init(ftyp: bytes, moov_raw: bytes, next_offset: int) -> InitInfo:
    btype, size, header_size, usertype = _box_header(moov_raw, 0, len(moov_raw))
    moov_box = _Box(btype, 0, size, header_size, usertype)
    tracks, creation, modification = _parse_moov(moov_raw, moov_box)
    output_moov = _transform_moov(moov_raw)
    return InitInfo(
        ftyp=ftyp,
        moov=moov_raw,
        tracks=tracks,
        output_init=ftyp + output_moov,
        creation_time=creation,
        modification_time=modification,
        next_offset=next_offset,
    )


# --------------------------------------------------------------------------
# Transform init -> output_init
# --------------------------------------------------------------------------


def _build_transform_tree(data, box: _Box, parent: _Node | None = None) -> _Node:
    node = _Node(box.type, raw=bytes(data[box.start:box.end]), usertype=box.usertype, parent=parent)
    if box.type in _TRANSFORM_CONTAINERS:
        try:
            child_start = box.payload_start + (8 if box.type == b"stsd" else 0)
            children = [
                _build_transform_tree(data, c, node)
                for c in _iter_children(data, child_start, box.payload_end)
            ]
            node.children = children
            if box.type == b"stsd":
                node.prefix = bytes(data[box.payload_start:box.payload_start + 8])
        except MP4ParseError:
            node.children = None
    return node


def _transform_stsd(stsd: _Node):
    if stsd.children is None:
        return
    new_entries = []
    changed = False
    for entry in stsd.children:
        p = _parse_sample_entry_raw(entry.raw)
        if (p is not None and p.codec != "unknown" and p.new_type is not None
                and entry.btype in (b"enca", b"encv")):
            body = entry.raw[p.entry_header_size:p.entry_header_size + p.entry_header_len]
            kept = [_raw_node(b) for b in p.kept_children]
            node = _Node(p.new_type, raw=None, children=kept, prefix=body, parent=stsd)
            new_entries.append(node)
            changed = True
        else:
            new_entries.append(entry)
    if len(new_entries) == 2:
        if _encode_node(new_entries[0]) == _encode_node(new_entries[1]):
            new_entries = new_entries[:1]
            changed = True
    if changed:
        stsd.children = new_entries
        stsd.prefix = stsd.prefix[:4] + struct.pack(">I", len(new_entries))
        stsd.mark_dirty()


def _transform_stbl(stbl: _Node):
    changed = False
    new_children = []
    for c in stbl.children:
        if c.btype in (b"sbgp", b"sgpd") and _grouping_is_encryption(c.raw):
            changed = True
        else:
            new_children.append(c)
    if changed:
        stbl.children = new_children
        stbl.mark_dirty()
    for c in stbl.children:
        if c.btype == b"stsd" and c.children is not None:
            _transform_stsd(c)


def _transform_moov_tree(node: _Node):
    if node.children is None:
        return
    for c in node.children:
        if c.btype == b"stbl" and c.children is not None:
            _transform_stbl(c)
        elif c.children is not None:
            _transform_moov_tree(c)


def _transform_moov(moov_raw: bytes) -> bytes:
    """Return the moov bytes with encryption info removed (byte-exact otherwise)."""
    btype, size, header_size, usertype = _box_header(moov_raw, 0, len(moov_raw))
    moov_box = _Box(btype, 0, size, header_size, usertype)
    root = _build_transform_tree(moov_raw, moov_box)
    _transform_moov_tree(root)
    return _encode_node(root)


# --------------------------------------------------------------------------
# Fragment parsing / rebuilding
# --------------------------------------------------------------------------


def _parse_tfhd(data, box) -> dict:
    payload = box.payload_start
    vaf = _u32(data, payload)
    flags = vaf & 0xFFFFFF
    d = {"flags": flags, "track_id": _u32(data, payload + 4)}
    p = payload + 8
    if flags & 0x1:
        d["base_data_offset"] = _u64(data, p)
        p += 8
    if flags & 0x2:
        d["sample_description_index"] = _u32(data, p)
        p += 4
    if flags & 0x8:
        d["default_sample_duration"] = _u32(data, p)
        p += 4
    if flags & 0x10:
        d["default_sample_size"] = _u32(data, p)
        p += 4
    if flags & 0x20:
        d["default_sample_flags"] = _u32(data, p)
        p += 4
    return d


def _parse_tfdt(data, box) -> int:
    payload = box.payload_start
    vaf = _u32(data, payload)
    if (vaf >> 24) == 0:
        return _u32(data, payload + 4)
    return _u64(data, payload + 4)


def _parse_trun(data, box) -> dict:
    payload = box.payload_start
    vaf = _u32(data, payload)
    flags = vaf & 0xFFFFFF
    sample_count = _u32(data, payload + 4)
    d = {"flags": flags, "version": vaf >> 24, "sample_count": sample_count, "samples": []}
    p = payload + 8
    if flags & 0x1:
        d["data_offset"] = _s32(data, p)
        p += 4
    if flags & 0x4:
        d["first_sample_flags"] = _u32(data, p)
        p += 4
    for _ in range(sample_count):
        dur = size = sflags = cto = None
        if flags & 0x100:
            dur = _u32(data, p)
            p += 4
        if flags & 0x200:
            size = _u32(data, p)
            p += 4
        if flags & 0x400:
            sflags = _u32(data, p)
            p += 4
        if flags & 0x800:
            cto = _s32(data, p)
            p += 4
        d["samples"].append({"dur": dur, "size": size, "flags": sflags, "cto": cto})
    return d


def _parse_saiz(data, box) -> dict:
    payload = box.payload_start
    vaf = _u32(data, payload)
    flags = vaf & 0xFFFFFF
    p = payload + 4
    if flags & 0x1:
        p += 8  # aux_info_type + aux_info_type_parameter
    if p + 5 > box.payload_end:
        raise MP4ParseError(f"saiz at offset {box.start} too small")
    default_size = data[p]
    p += 1
    sample_count = _u32(data, p)
    p += 4
    infos = None
    if default_size == 0:
        infos = bytes(data[p:p + sample_count])
    return {
        "flags": flags,
        "default_sample_info_size": default_size,
        "sample_count": sample_count,
        "sample_info": infos,
    }


def _saiz_matches(saiz: dict, sample_count: int, iv_size: int) -> bool:
    default = saiz["default_sample_info_size"]
    infos = saiz["sample_info"]
    for i in range(sample_count):
        if default != 0:
            sz = default
        elif infos is not None and i < len(infos):
            sz = infos[i]
        else:
            sz = 0
        if sz == 0:
            continue  # unprotected sample
        rem = sz - iv_size - 2
        if rem < 0 or rem % 6 != 0:
            return False
    return True


def _infer_senc_iv_size(payload_len: int, sample_count: int, flags: int, saiz: dict | None) -> int:
    if sample_count == 0:
        return 0
    if flags & 0x2:
        candidates: list[int] = []
        if saiz is not None:
            candidates = [iv for iv in (8, 16, 0) if _saiz_matches(saiz, sample_count, iv)]
        if not candidates:
            candidates = [8, 16, 0]
        for iv in candidates:
            rem = payload_len - sample_count * (iv + 2)
            if rem >= 0 and rem % 6 == 0:
                return iv
        raise MP4ParseError(
            f"cannot determine senc IV size: {payload_len} payload bytes, "
            f"{sample_count} samples, subsample-encryption flag set")
    if payload_len % sample_count != 0:
        raise MP4ParseError(
            f"cannot determine senc IV size: {payload_len} payload bytes "
            f"not divisible by {sample_count} samples")
    return payload_len // sample_count


def _parse_senc(data, senc_box: _Box, iv_size: int | None, saiz: dict | None) -> dict:
    payload = senc_box.payload_start
    end = senc_box.payload_end
    if payload + 8 > end:
        raise MP4ParseError(f"senc at offset {senc_box.start} too small")
    vaf = _u32(data, payload)
    version = vaf >> 24
    if version != 0:
        raise MP4ParseError(
            f"senc at offset {senc_box.start}: version {version} not supported")
    flags = vaf & 0xFFFFFF
    sample_count = _u32(data, payload + 4)
    p = payload + 8
    if iv_size is None:
        iv_size = _infer_senc_iv_size(end - p, sample_count, flags, saiz)
    ivs = []
    subsamples = []
    for i in range(sample_count):
        if iv_size and iv_size > 0:
            if p + iv_size > end:
                raise MP4ParseError(
                    f"senc at offset {senc_box.start}: truncated IV for sample {i}")
            ivs.append(bytes(data[p:p + iv_size]))
            p += iv_size
        else:
            ivs.append(None)
        if flags & 0x2:
            if p + 2 > end:
                raise MP4ParseError(
                    f"senc at offset {senc_box.start}: truncated subsample count for sample {i}")
            n = _u16(data, p)
            p += 2
            ss = []
            for _ in range(n):
                if p + 6 > end:
                    raise MP4ParseError(
                        f"senc at offset {senc_box.start}: truncated subsample entry")
                ss.append(SubSamplePattern(_u16(data, p), _u32(data, p + 2)))
                p += 6
            subsamples.append(ss)
        else:
            subsamples.append([])
    return {
        "version": version,
        "flags": flags,
        "sample_count": sample_count,
        "iv_size": iv_size,
        "ivs": ivs,
        "subsamples": subsamples,
    }


def _parse_traf(data, traf_box: _Box, iv_size_by_track: dict | None = None) -> dict:
    tfhd = None
    tfdt = None
    truns = []
    senc = None      # ('senc' | 'uuid', _Box)
    saiz = None
    for child in _iter_children(data, traf_box.payload_start, traf_box.payload_end):
        if child.type == b"tfhd":
            tfhd = _parse_tfhd(data, child)
        elif child.type == b"tfdt":
            tfdt = _parse_tfdt(data, child)
        elif child.type == b"trun":
            truns.append(_parse_trun(data, child))
        elif child.type == b"senc":
            senc = ("senc", child)
        elif child.type == b"saiz":
            saiz = _parse_saiz(data, child)
        elif child.type == b"uuid":
            if child.usertype == _PIFF_SENC_UUID:
                senc = ("uuid", child)
    if tfhd is None:
        raise MP4ParseError(f"traf at offset {traf_box.start} has no tfhd")
    senc_info = None
    if senc is not None:
        kind, box = senc
        if kind == "senc":
            senc_box = box
        else:
            # PIFF uuid senc: content starts right after the 16-byte usertype
            senc_box = _Box(b"senc", box.payload_start, box.payload_end - box.payload_start, 0)
        iv_size = None
        if iv_size_by_track is not None:
            iv_size = iv_size_by_track.get(tfhd["track_id"])
        senc_info = _parse_senc(data, senc_box, iv_size, saiz)
    return {
        "track_id": tfhd["track_id"],
        "tfhd": tfhd,
        "tfdt": tfdt,
        "truns": truns,
        "senc": senc_info,
        "saiz": saiz,
    }


def _parse_moof(data, moof_box: _Box, iv_size_by_track: dict | None = None):
    seq = None
    trafs = []
    for child in _iter_children(data, moof_box.payload_start, moof_box.payload_end):
        if child.type == b"mfhd":
            seq = _u32(data, child.payload_start + 4)
        elif child.type == b"traf":
            trafs.append(_parse_traf(data, child, iv_size_by_track))
    return seq, trafs


def _build_specs_abs(moof_start: int, trafs: list[dict]) -> list[SampleSpec]:
    """Sample specs with *absolute* file offsets (offset field is absolute here)."""
    specs: list[SampleSpec] = []
    for traf in trafs:
        tfhd = traf["tfhd"]
        base = moof_start
        if tfhd.get("base_data_offset") is not None:
            base = tfhd["base_data_offset"]
        senc = traf.get("senc")
        ivs = senc["ivs"] if senc else []
        subs = senc["subsamples"] if senc else []
        default_dur = tfhd.get("default_sample_duration")
        default_size = tfhd.get("default_sample_size")
        desc_index = tfhd.get("sample_description_index", 1)
        base_decode = traf.get("tfdt") or 0
        idx = 0
        for trun in traf["truns"]:
            trun_base = base
            if trun.get("data_offset") is not None:
                trun_base = base + trun["data_offset"]
            off = trun_base
            acc_dur = 0
            for s in trun["samples"]:
                size = s["size"] if s["size"] is not None else default_size
                if size is None:
                    raise MP4ParseError(
                        f"track {tfhd['track_id']}: trun sample {idx} has no size "
                        f"(no per-sample size and no default)")
                dur = s["dur"] if s["dur"] is not None else default_dur
                iv = ivs[idx] if idx < len(ivs) else None
                ss = subs[idx] if idx < len(subs) else []
                cto = s["cto"]
                dtime = base_decode + acc_dur
                cts = (dtime + cto) if cto is not None else None
                specs.append(SampleSpec(
                    desc_index=desc_index, offset=off, length=size, iv=iv,
                    sub_sample_patterns=ss, duration=dur, cts=cts))
                off += size
                if dur is not None:
                    acc_dur += dur
                idx += 1
            base_decode += acc_dur
    specs.sort(key=lambda s: s.offset)   # file order
    return specs


def _build_specs_relative(moof_start: int, mdat_payload_start: int,
                          mdat_payload_len: int, trafs: list[dict]) -> list[SampleSpec]:
    specs = _build_specs_abs(moof_start, trafs)
    for s in specs:
        s.offset = s.offset - mdat_payload_start
        if s.offset < 0 or s.offset + s.length > mdat_payload_len:
            raise MP4ParseError(
                f"sample at mdat offset {s.offset} len {s.length} "
                f"outside mdat payload (len {mdat_payload_len})")
    return specs


def _is_encryption_child(node: _Node) -> bool:
    t = node.btype
    if t in (b"senc", b"saiz", b"saio", b"pssh"):
        return True
    if t in (b"sbgp", b"sgpd"):
        return _grouping_is_encryption(node.raw)
    if t == b"uuid":
        return node.usertype == _PIFF_SENC_UUID
    return False


def _patch_trun_data_offset(trun_raw: bytes, delta: int) -> bytes | None:
    if delta == 0:
        return None
    try:
        btype, size, header_size, _ = _box_header(trun_raw, 0, len(trun_raw))
    except MP4ParseError:
        return None
    if btype != b"trun" or header_size + 12 > len(trun_raw):
        return None
    vaf = _u32(trun_raw, header_size)
    if not (vaf & 0x1):  # no data-offset field
        return None
    field_off = header_size + 8
    if field_off + 4 > len(trun_raw):
        return None
    out = bytearray(trun_raw)
    new_val = _s32(out, field_off) - delta
    struct.pack_into(">i", out, field_off, new_val)
    return bytes(out)


def _patch_tfhd_sample_desc_index(tfhd_raw: bytes, index: int = 1) -> bytes | None:
    """Return a copy of *tfhd_raw* with ``sample_description_index`` set to
    *index*, or ``None`` when the field is absent or already equal to *index*.

    The sanitized output init keeps exactly one sample entry in ``stsd``, so
    fragments that originally referenced the second (pre-removal) entry must
    be rewritten to reference entry 1.  Without this, players see a
    sample_description_index of 2 in later fragments and ignore them, which
    makes the track appear to be only the first fragment (~15 s) long.
    """
    try:
        btype, size, header_size, _ = _box_header(tfhd_raw, 0, len(tfhd_raw))
    except MP4ParseError:
        return None
    if btype != b"tfhd" or header_size + 8 > len(tfhd_raw):
        return None
    flags = _u32(tfhd_raw, header_size) & 0xFFFFFF
    if not (flags & 0x2):  # no sample_description_index field
        return None
    field_off = header_size + 8  # version/flags + track_ID
    if flags & 0x1:
        field_off += 8  # base_data_offset is present
    if field_off + 4 > len(tfhd_raw):
        return None
    if _u32(tfhd_raw, field_off) == index:
        return None
    out = bytearray(tfhd_raw)
    struct.pack_into(">I", out, field_off, index)
    return bytes(out)


def _build_fragment_tree(data, box: _Box, parent: _Node | None = None) -> _Node:
    node = _Node(box.type, raw=bytes(data[box.start:box.end]), usertype=box.usertype, parent=parent)
    if box.type in _FRAGMENT_CONTAINERS:
        try:
            node.children = [
                _build_fragment_tree(data, c, node)
                for c in _iter_children(data, box.payload_start, box.payload_end)
            ]
        except MP4ParseError:
            node.children = None
    return node


def _rebuild_moof_bytes(data, moof_box: _Box) -> bytes:
    """moof bytes with encryption boxes removed and trun data offsets adjusted."""
    root = _build_fragment_tree(data, moof_box)
    if root.children is None:
        return root.raw
    total_removed = 0

    # moof level: remove pssh boxes
    changed = False
    new_children = []
    for c in root.children:
        if c.btype == b"pssh":
            total_removed += len(c.raw)
            changed = True
        else:
            new_children.append(c)
    if changed:
        root.children = new_children
        root.mark_dirty()

    # traf level: remove encryption boxes
    trafs = [c for c in root.children if c.btype == b"traf" and c.children is not None]
    for traf in trafs:
        changed = False
        new_children = []
        for c in traf.children:
            if _is_encryption_child(c):
                total_removed += len(c.raw)
                changed = True
            else:
                new_children.append(c)
        if changed:
            traf.children = new_children
            traf.mark_dirty()

    # adjust trun data offsets by the total removed bytes
    if total_removed:
        for traf in trafs:
            for c in traf.children:
                if c.btype == b"trun":
                    patched = _patch_trun_data_offset(c.raw, total_removed)
                    if patched is not None:
                        c.raw = patched
                        traf.mark_dirty()

    # The sanitized init keeps a single stsd entry, so rewrite any tfhd that
    # still references the pre-sanitization second entry (sample_description_index 2)
    # to entry 1.  Leaving it at 2 makes demuxers ignore all later fragments.
    for traf in trafs:
        for c in traf.children:
            if c.btype == b"tfhd":
                patched = _patch_tfhd_sample_desc_index(c.raw, 1)
                if patched is not None:
                    c.raw = patched
                    traf.mark_dirty()
    return _encode_node(root)


# --------------------------------------------------------------------------
# Batch API
# --------------------------------------------------------------------------


def parse_init(data: bytes, offset: int = 0) -> tuple[InitInfo | None, int]:
    """Parse the init segment (``ftyp`` + ``moov``) starting at *offset*.

    Returns ``(InitInfo, next_offset)`` or ``(None, offset)`` when there is no
    ``ftyp`` box at *offset* (EOF / not an init segment here).
    """
    try:
        btype, size, header_size, usertype = _box_header(data, offset, len(data))
    except MP4ParseError:
        return None, offset
    if btype != b"ftyp":
        return None, offset
    if offset + size > len(data):
        return None, offset
    ftyp = bytes(data[offset:offset + size])
    offset += size
    try:
        btype, size, header_size, usertype = _box_header(data, offset, len(data))
    except MP4ParseError:
        return None, offset
    if btype != b"moov":
        raise MP4ParseError(
            f"expected 'moov' after 'ftyp' at offset {offset}, got '{_type_str(btype)}'")
    if offset + size > len(data):
        raise MP4ParseError(f"moov box at offset {offset} overruns data end")
    moov_raw = bytes(data[offset:offset + size])
    next_offset = offset + size
    return _build_init(ftyp, moov_raw, next_offset), next_offset


def parse_next_fragment(data: bytes, offset: int, seq: int) -> tuple[FragmentInfo | None, int]:
    """Parse the next fragment (``moof`` ... ``mdat``) starting at *offset*.

    Returns ``(FragmentInfo, next_offset)`` or ``(None, offset)`` at EOF.
    """
    pos = offset
    moof_box = None
    mdat_box = None
    while True:
        if pos >= len(data):
            break
        btype, size, header_size, usertype = _box_header(data, pos, len(data))
        if pos + size > len(data):
            raise MP4ParseError(
                f"box '{_type_str(btype)}' at offset {pos} overruns data end")
        box = _Box(btype, pos, size, header_size, usertype)
        if btype == b"moof":
            if moof_box is None:
                moof_box = box
        elif btype == b"mdat":
            mdat_box = box
            break
        # emsg / prft / free / sidx / unknown: skip
        pos += size
    if moof_box is None:
        return None, pos
    if mdat_box is None:
        raise MP4ParseError(
            f"fragment at offset {offset}: found moof but no mdat before end of data")
    _seq, trafs = _parse_moof(data, moof_box)
    rebuilt = _rebuild_moof_bytes(data, moof_box)
    mdat_payload_start = mdat_box.payload_start
    mdat_payload_len = mdat_box.payload_end - mdat_payload_start
    specs = _build_specs_relative(moof_box.start, mdat_payload_start, mdat_payload_len, trafs)
    payload = bytes(data[mdat_payload_start:mdat_box.payload_end])
    next_offset = mdat_box.payload_end
    return (
        FragmentInfo(seq=seq, rebuilt_moof=rebuilt, mdat_payload=payload,
                     samples=specs, next_offset=next_offset),
        next_offset,
    )


def rebuild_fragment_bytes(frag: FragmentInfo, decrypted_payload: bytes) -> bytes:
    """Re-emit ``moof`` then ``mdat`` with the decrypted payload.

    The ``rebuilt_moof`` in *frag* already has encryption boxes removed and
    trun data offsets corrected by the removed byte count.
    """
    if len(decrypted_payload) + 8 < 0xFFFFFFFF:
        mdat = struct.pack(">I4s", 8 + len(decrypted_payload), b"mdat") + decrypted_payload
    else:
        mdat = struct.pack(">I4sQ", 1, b"mdat", 16 + len(decrypted_payload)) + decrypted_payload
    return frag.rebuilt_moof + mdat


def mac_epoch_to_datetime(ts: int) -> datetime:
    """Convert a Mac epoch (1904-01-01) timestamp in seconds to a datetime."""
    return _MAC_EPOCH + timedelta(seconds=ts)


def _datetime_to_mac_epoch(dt: datetime) -> int:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return int((dt - _MAC_EPOCH).total_seconds())


def patch_mvhd_times(data: bytes, creation: datetime | None, modification: datetime | None) -> bytes:
    """Patch the mvhd creation/modification times (Mac epoch) in *data*.

    Fields whose value is ``None`` are left untouched. Box sizes do not change.
    """
    if creation is None and modification is None:
        return data
    out = bytearray(data)
    pos = 0
    while pos + 8 <= len(out):
        try:
            btype, size, header_size, usertype = _box_header(out, pos, len(out))
        except MP4ParseError:
            break
        if btype == b"moov":
            cpos = pos + header_size
            while cpos + 8 <= pos + size:
                try:
                    cb, csz, chdr, _ = _box_header(out, cpos, pos + size)
                except MP4ParseError:
                    break
                if cb == b"mvhd":
                    payload = cpos + chdr
                    version = out[payload]
                    if creation is not None:
                        if version == 1:
                            struct.pack_into(">Q", out, payload + 4, _datetime_to_mac_epoch(creation))
                        else:
                            struct.pack_into(">I", out, payload + 4, _datetime_to_mac_epoch(creation))
                    if modification is not None:
                        off = payload + (12 if version == 1 else 8)
                        if version == 1:
                            struct.pack_into(">Q", out, off, _datetime_to_mac_epoch(modification))
                        else:
                            struct.pack_into(">I", out, off, _datetime_to_mac_epoch(modification))
                    return bytes(out)
                cpos += csz
        pos += size
    raise MP4ParseError("patch_mvhd_times: no mvhd box found")


# --------------------------------------------------------------------------
# Streaming API
# --------------------------------------------------------------------------


def _try_stream_header(buf: bytearray):
    """Best-effort box header read for streaming.

    Returns ``(type, size|None, header_size, usertype)`` or ``None`` when more
    bytes are needed. ``size`` is ``None`` for boxes extending to end of stream.
    """
    if len(buf) < 8:
        return None
    size32 = _u32(buf, 0)
    btype = bytes(buf[4:8])
    header_size = 8
    usertype = None
    size = size32
    if size32 == 1:
        if len(buf) < 16:
            return None
        size = _u64(buf, 8)
        header_size = 16
    elif size32 == 0:
        size = None  # extends to end of stream
    if btype == b"uuid":
        if len(buf) < header_size + 16:
            return None
        usertype = bytes(buf[header_size:header_size + 16])
        header_size += 16
    if size is not None and size < header_size:
        raise MP4ParseError(
            f"box '{_type_str(btype)}' has size {size} < header {header_size}")
    return btype, size, header_size, usertype


class StreamingMP4Parser:
    """Streaming fMP4 parser with bounded memory.

    * ``on_init(init: InitInfo)`` fires once after ftyp+moov are complete.
    * ``on_fragment_moof(seq: int, rebuilt_moof: bytes)`` fires when a moof is
      parsed (encryption boxes already removed, trun offsets fixed).
    * ``on_sample(seq: int, spec: SampleSpec, data: bytes)`` fires as soon as a
      sample's bytes are fully buffered (mdat is streamed).
    """

    def __init__(self, on_init=None, on_fragment_moof=None, on_sample=None):
        self._on_init = on_init
        self._on_fragment_moof = on_fragment_moof
        self._on_sample = on_sample
        self._buf = bytearray()
        self._abs = 0                 # absolute file offset of self._buf[0]
        self._cur = None              # in-progress box dict, or None
        self._ftyp = None
        self._init_sent = False
        self._tenc_iv_sizes: dict[int, int] = {}
        self._frag_counter = 0        # 0-based fragment sequence for callbacks
        self._fragment = None         # pending fragment while mdat streams
        self._finished = False
        self._complete = False

    # -- public ------------------------------------------------------------

    def feed(self, chunk: bytes) -> None:
        if self._finished:
            raise MP4ParseError("StreamingMP4Parser.feed() called after finish()")
        self._buf.extend(chunk)
        self._process()

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        cur = self._cur
        if cur is not None and cur["size"] is None:
            # box extends to end of stream: process whatever was buffered
            size = len(self._buf)
            self._handle_complete_box(cur["type"], size, cur["header"], cur["usertype"])
            del self._buf[:]
            self._abs += size
            self._cur = None
        self._complete = (
            len(self._buf) == 0 and self._cur is None and self._fragment is None)

    @property
    def next_box_offset(self) -> int:
        """Absolute offset where parsing would resume (next unstarted box)."""
        if self._cur is not None:
            return self._cur["start_abs"]
        return self._abs

    @property
    def complete(self) -> bool:
        return self._complete

    # -- internals ---------------------------------------------------------

    def _process(self):
        while True:
            if self._cur is None:
                hdr = _try_stream_header(self._buf)
                if hdr is None:
                    return
                btype, size, header_size, usertype = hdr
                self._cur = {
                    "type": btype, "size": size, "header": header_size,
                    "usertype": usertype, "start_abs": self._abs,
                }
                if btype == b"mdat":
                    if self._fragment is None:
                        raise MP4ParseError(
                            f"mdat at offset {self._abs} without preceding moof")
                    self._fragment["mdat_payload_start_abs"] = self._abs + header_size
                    self._emit_ready_samples()
            cur = self._cur
            size = cur["size"]
            if size is None:
                return  # to-EOF box; wait for finish()
            if len(self._buf) < size:
                if cur["type"] == b"mdat" and self._fragment is not None:
                    self._emit_ready_samples()
                return
            btype = cur["type"]
            self._handle_complete_box(btype, size, cur["header"], cur["usertype"])
            del self._buf[:size]
            self._abs += size
            self._cur = None

    def _emit_ready_samples(self):
        frag = self._fragment
        if frag is None or frag["mdat_payload_start_abs"] is None:
            return
        header = self._cur["header"]
        avail_payload = len(self._buf) - header
        mps = frag["mdat_payload_start_abs"]
        pending = frag["pending"]
        seq = frag["seq"]
        while pending:
            abs_off, length, desc_index, iv, subs, dur, cts = pending[0]
            rel = abs_off - mps
            if rel < 0:
                raise MP4ParseError(
                    f"sample absolute offset {abs_off} before mdat payload start {mps}")
            if rel + length > avail_payload:
                break
            data = bytes(self._buf[header + rel:header + rel + length])
            spec = SampleSpec(
                desc_index=desc_index, offset=rel, length=length, iv=iv,
                sub_sample_patterns=subs, duration=dur, cts=cts)
            if self._on_sample is not None:
                self._on_sample(seq, spec, data)
            pending.pop(0)

    def _handle_complete_box(self, btype, size, header_size, usertype):
        if btype == b"ftyp":
            self._ftyp = bytes(self._buf[:size])
            return
        if btype == b"moov":
            if not self._init_sent:
                moov_raw = bytes(self._buf[:size])
                ftyp = self._ftyp if self._ftyp is not None else b""
                init = _build_init(ftyp, moov_raw, self._abs + size)
                self._tenc_iv_sizes = {tid: ti.iv_size for tid, ti in init.tracks.items()}
                self._init_sent = True
                if self._on_init is not None:
                    self._on_init(init)
            return
        if btype == b"moof":
            moof_box = _Box(b"moof", 0, size, header_size, usertype)
            _seq, trafs = _parse_moof(self._buf, moof_box, self._tenc_iv_sizes or None)
            rebuilt = _rebuild_moof_bytes(self._buf, moof_box)
            specs_abs = _build_specs_abs(self._abs, trafs)
            seq = self._frag_counter
            self._frag_counter += 1
            self._fragment = {
                "seq": seq,
                "rebuilt_moof": rebuilt,
                "pending": [
                    (s.offset, s.length, s.desc_index, s.iv,
                     s.sub_sample_patterns, s.duration, s.cts)
                    for s in specs_abs
                ],
                "mdat_payload_start_abs": None,
            }
            if self._on_fragment_moof is not None:
                self._on_fragment_moof(seq, rebuilt)
            return
        if btype == b"mdat":
            if self._fragment is None:
                raise MP4ParseError(
                    f"mdat at offset {self._abs} without preceding moof")
            self._emit_ready_samples()
            if self._fragment["pending"]:
                raise MP4ParseError("internal error: fragment samples not fully emitted")
            self._fragment = None
            return
        # emsg / prft / free / sidx / unknown: ignore


# --------------------------------------------------------------------------
# Muxing helpers (MV video+audio merge)
# --------------------------------------------------------------------------

def _find_child_boxes(data: bytes, start: int, end: int, want: bytes) -> list[tuple[bytes, int, int]]:
    """Return [(raw_child, child_start, child_end)] of type *want* within [start, end)."""
    out = []
    for c in _iter_children(data, start, end):
        if c.type == want:
            out.append((bytes(data[c.start:c.end]), c.start, c.end))
    return out


def read_moof_track_ids(moof: bytes) -> list[int]:
    """Return the traf track IDs present in one moof box."""
    try:
        _, size, header_size, _ = _box_header(moof, 0, len(moof))
    except MP4ParseError:
        return []
    ids = []
    for traf, _, _ in _find_child_boxes(moof, header_size, size, b"traf"):
        for tfhd, _, te in _find_child_boxes(traf, 8, len(traf), b"tfhd"):
            if len(tfhd) >= 16:
                ids.append(_u32(tfhd, 12))
    return ids


def patch_moof_track_id(moof: bytes, old_id: int, new_id: int) -> bytes:
    """Rewrite ``tfhd.track_ID`` in every traf of one moof (in place, same size)."""
    try:
        _, size, header_size, _ = _box_header(moof, 0, len(moof))
    except MP4ParseError:
        return moof
    out = bytearray(moof)
    for traf, tstart, tend in _find_child_boxes(moof, header_size, size, b"traf"):
        for tfhd, ts, te in _find_child_boxes(out, tstart + 8, tend, b"tfhd"):
            if len(tfhd) >= 16 and _u32(tfhd, 12) == old_id:
                struct.pack_into(">I", out, ts + 12, new_id)
    return bytes(out)


def parse_fragment_timing(moof: bytes) -> tuple[int | None, int | None]:
    """Return (track_id, tfdt decode_time) of the first traf in one moof."""
    try:
        _, size, header_size, _ = _box_header(moof, 0, len(moof))
    except MP4ParseError:
        return None, None
    for traf, tstart, tend in _find_child_boxes(moof, header_size, size, b"traf"):
        track_id = None
        tfdt = None
        for tfhd, _, _ in _find_child_boxes(moof, tstart + 8, tend, b"tfhd"):
            if len(tfhd) >= 16:
                track_id = _u32(tfhd, 12)
        for tfdt_box, _, _ in _find_child_boxes(moof, tstart + 8, tend, b"tfdt"):
            if len(tfdt_box) >= 12:
                # tfdt_box is a full box: size(4) type(4) ver/flags(4) value(4/8)
                vaf = _u32(tfdt_box, 8)
                version = vaf >> 24
                if version == 0 and len(tfdt_box) >= 16:
                    tfdt = _u32(tfdt_box, 12)
                elif version == 1 and len(tfdt_box) >= 20:
                    tfdt = _u64(tfdt_box, 12)
        if track_id is not None:
            return track_id, tfdt
    return None, None


def patch_tfdt_delta(moof: bytes, delta: int) -> bytes:
    """Rewrite every ``traf/tfdt`` decode time in one moof by subtracting
    *delta* (used to normalise the first fragment to timestamp 0).

    Handles tfdt v0 (32-bit) and v1 (64-bit).  If *delta* is 0 the moof is
    returned unchanged.

    This implementation scans for literal b"tfdt" inside the moof and writes
    the baseMediaDecodeTime field in place; it is simpler and more robust than
    navigating the nested box tree (the box type string does not appear as a
    plain value inside valid tfdt payloads).
    """
    if delta == 0:
        return moof
    out = bytearray(moof)
    search_from = 0
    while True:
        i = out.find(b"tfdt", search_from)
        if i < 0:
            break
        # i points at the type field.  A full box is:
        #   size(4) "tfdt"(4) version+flags(4) value(4 or 8)
        # Verify the size field makes sense before touching it.
        if i >= 4:
            size = struct.unpack(">I", out[i - 4:i])[0]
            if size < 16 or i - 4 + size > len(out):
                search_from = i + 4
                continue
            vaf = _u32(out, i + 4)
            version = vaf >> 24
            if version == 0 and size >= 16:
                value_off = i + 8
                cur = _u32(out, value_off)
                struct.pack_into(">I", out, value_off, max(0, cur - delta))
            elif version == 1 and size >= 20:
                value_off = i + 8
                cur = _u64(out, value_off)
                struct.pack_into(">Q", out, value_off, max(0, cur - delta))
            search_from = i + 4
        else:
            search_from = i + 4
    return bytes(out)




def patch_tkhd_track_id(trak: bytes, new_id: int) -> bytes:
    """Rewrite ``trak/tkhd.track_ID`` (v0/v1 aware)."""
    out = bytearray(trak)
    try:
        _, tsize, theader, _ = _box_header(trak, 0, len(trak))
    except MP4ParseError:
        return trak
    mdia = None
    for child in _iter_children(trak, theader, tsize):
        if child.type == b"tkhd":
            vaf = _u32(trak, child.payload_start)
            version = vaf >> 24
            off = child.payload_start + 4 + (8 if version == 1 else 4) + 4 + (8 if version == 1 else 4) + 4
            # tkhd payload: verflags(4) creation(4/8) modification(4/8) track_ID(4)
            tkhd_off = child.start + 8 + 4 + (8 if version == 1 else 4) + (8 if version == 1 else 4)
            struct.pack_into(">I", out, tkhd_off, new_id)
            _ = off
        elif child.type == b"mdia":
            mdia = child
    if mdia is not None:
        # also patch tref (reference track ids) if present inside trak
        pass
    return bytes(out)


def patch_trex_track_id(mvex: bytes, old_id: int, new_id: int) -> bytes:
    """Rewrite ``mvex/trex.track_ID`` entries."""
    out = bytearray(mvex)
    try:
        _, msize, mheader, _ = _box_header(mvex, 0, len(mvex))
    except MP4ParseError:
        return mvex
    for trex, ts, _ in _find_child_boxes(mvex, mheader, msize, b"trex"):
        if len(trex) >= 16 and _u32(trex, 12) == old_id:
            struct.pack_into(">I", out, ts + 12, new_id)
    return bytes(out)


def patch_mvhd_duration(mvhd: bytes, duration: int) -> bytes:
    """Rewrite the mvhd duration field (v0/v1 aware)."""
    out = bytearray(mvhd)
    try:
        _, msize, mheader, _ = _box_header(mvhd, 0, len(mvhd))
    except MP4ParseError:
        return mvhd
    vaf = _u32(mvhd, mheader)
    version = vaf >> 24
    if version == 1:
        # creation(8) modification(8) timescale(4) duration(8)
        struct.pack_into(">Q", out, mheader + 4 + 8 + 8 + 4, duration)
    else:
        struct.pack_into(">I", out, mheader + 4 + 4 + 4 + 4, duration)
    return bytes(out)


def read_mvhd_timescale_duration(mvhd: bytes) -> tuple[int, int]:
    """Return (timescale, duration) of one mvhd box."""
    try:
        _, msize, mheader, _ = _box_header(mvhd, 0, len(mvhd))
    except MP4ParseError:
        return 0, 0
    vaf = _u32(mvhd, mheader)
    version = vaf >> 24
    if version == 1:
        timescale = _u32(mvhd, mheader + 4 + 8 + 8)
        duration = _u64(mvhd, mheader + 4 + 8 + 8 + 4)
    else:
        timescale = _u32(mvhd, mheader + 4 + 4 + 4)
        duration = _u32(mvhd, mheader + 4 + 4 + 4 + 4)
    return timescale, duration


def read_trak_timescale(trak: bytes) -> int | None:
    """Read the mdia/mdhd timescale of one trak box."""
    try:
        _, tsize, theader, _ = _box_header(trak, 0, len(trak))
    except MP4ParseError:
        return None
    for child in _iter_children(trak, theader, tsize):
        if child.type != b"mdia":
            continue
        mdia = child
        for gchild in _iter_children(trak, mdia.payload_start, mdia.payload_end):
            if gchild.type != b"mdhd":
                continue
            vaf = _u32(trak, gchild.payload_start)
            version = vaf >> 24
            # verflags(4) creation(4/8) modification(4/8) timescale(4)
            off = gchild.payload_start + 4 + (8 if version == 1 else 4) + (8 if version == 1 else 4)
            return _u32(trak, off)
    return None