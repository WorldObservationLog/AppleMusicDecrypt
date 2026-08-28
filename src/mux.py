"""Pure-Python MP4 muxer: merge an MV's video and audio fragmented streams into
one MP4 file (replaces MP4Box/ffmpeg for MV remuxing).

Both input streams are already-decrypted fragmented MP4s (init + moof/mdat
fragments). The merged output keeps every fragment's moof/tfdt/trun intact
(only the traf ``tfhd.track_ID`` is renumbered) and interleaves fragments by
decode time, so no re-encoding is needed.
"""

import struct

from src.mp4 import (parse_init, _box_header, _iter_children, patch_moof_track_id,
                     parse_fragment_timing, patch_tkhd_track_id, patch_trex_track_id,
                     patch_mvhd_duration, read_mvhd_timescale_duration, read_trak_timescale)


def _top_boxes(data: bytes) -> list[tuple[bytes, int, int]]:
    out = []
    pos = 0
    while pos + 8 <= len(data):
        btype, size, header_size, _ = _box_header(data, pos, len(data))
        if size < 8 or pos + size > len(data):
            break
        out.append((btype, pos, size))
        pos += size
    return out


def _read_tkhd_track_id(trak: bytes) -> int:
    try:
        _, tsize, theader, _ = _box_header(trak, 0, len(trak))
    except Exception:
        return 0
    for child in _iter_children(trak, theader, tsize):
        if child.type == b"tkhd":
            vaf = struct.unpack(">I", trak[child.payload_start:child.payload_start + 4])[0]
            version = vaf >> 24
            off = child.start + 8 + 4 + (8 if version == 1 else 4) * 2
            return struct.unpack(">I", trak[off:off + 4])[0]
    return 0


def _split_moov(moov: bytes):
    """Split one moov into (mvhd, traks, mvex, rest)."""
    mvhd = None
    traks = []
    mvex = None
    rest = []
    try:
        _, msize, mheader, _ = _box_header(moov, 0, len(moov))
    except Exception:
        return None, [], None, []
    for child in _iter_children(moov, mheader, msize):
        raw = bytes(moov[child.start:child.end])
        if child.type == b"mvhd":
            mvhd = raw
        elif child.type == b"trak":
            traks.append(raw)
        elif child.type == b"mvex":
            mvex = raw
        else:
            rest.append(raw)
    return mvhd, traks, mvex, rest


def _box(btype: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), btype) + payload


def mux_mv(video_init, audio_init,
           video_frags: list[bytes], audio_frags: list[bytes],
           video_track_id: int = 1, audio_track_id: int = 2) -> bytes:
    """Merge decrypted MV video/audio init segments and fragments into one MP4.

    ``video_init`` / ``audio_init`` are ``InitInfo`` from ``parse_init`` (their
    ``output_init`` must already be transformed/decrypted). ``video_frags`` /
    ``audio_frags`` are rebuilt fragment byte strings (moof + mdat).
    """
    vtop = _top_boxes(video_init.output_init)
    atop = _top_boxes(audio_init.output_init)
    ftyp = bytes(video_init.output_init[0:vtop[0][2]]) if vtop else b""
    # locate moov within output_init
    vmoov = next((bytes(video_init.output_init[s:s + sz]) for b, s, sz in vtop if b == b"moov"), b"")
    amoov = next((bytes(audio_init.output_init[s:s + sz]) for b, s, sz in atop if b == b"moov"), b"")

    v_mvhd, v_traks, v_mvex, v_rest = _split_moov(vmoov)
    a_mvhd, a_traks, a_mvex, _a_rest = _split_moov(amoov)
    if not v_traks or not a_traks or v_mvhd is None:
        raise ValueError("mux_mv: need one trak per source and a video mvhd")

    v_old_id = _read_tkhd_track_id(v_traks[0])
    a_old_id = _read_tkhd_track_id(a_traks[0])

    # renumber tracks
    trak_v = patch_tkhd_track_id(v_traks[0], video_track_id)
    trak_a = patch_tkhd_track_id(a_traks[0], audio_track_id)

    # mvex: keep video's (renumbered) and append audio's (renumbered)
    mvex = None
    if v_mvex is not None:
        mvex = patch_trex_track_id(v_mvex, v_old_id, video_track_id)
        if a_mvex is not None:
            am = patch_trex_track_id(a_mvex, a_old_id, audio_track_id)
            # append the audio trex boxes (and any other mvex children) into video mvex
            try:
                _, msize, mheader, _ = _box_header(am, 0, len(am))
                children = [bytes(am[c.start:c.end]) for c in _iter_children(am, mheader, msize)]
                mvex = _box(b"mvex", mvex[mheader:] + b"".join(children))
            except Exception:
                pass
    elif a_mvex is not None:
        mvex = patch_trex_track_id(a_mvex, a_old_id, audio_track_id)

    # mvhd duration: max of both sources, in the video mvhd timescale
    v_ts, v_dur = read_mvhd_timescale_duration(v_mvhd)
    a_ts, a_dur = read_mvhd_timescale_duration(a_mvhd)
    new_dur = v_dur
    if v_ts and a_ts:
        new_dur = max(v_dur, int(a_dur * v_ts / a_ts))
    mvhd = patch_mvhd_duration(v_mvhd, new_dur)

    moov_payload = mvhd + trak_v + trak_a
    if mvex is not None:
        moov_payload += mvex
    for r in v_rest:
        moov_payload += r
    moov = _box(b"moov", moov_payload)

    # fragments: renumber + interleave by decode time
    entries = []  # (time_sec, kind, fragment)
    v_ts_sec = read_trak_timescale(trak_v) or 90000
    a_ts_sec = read_trak_timescale(trak_a) or 48000
    for frag in video_frags:
        _, tfdt = parse_fragment_timing(frag)
        frag = patch_moof_track_id(frag, v_old_id, video_track_id)
        t = (tfdt or 0) / v_ts_sec
        entries.append((t, 0, frag))
    for frag in audio_frags:
        _, tfdt = parse_fragment_timing(frag)
        frag = patch_moof_track_id(frag, a_old_id, audio_track_id)
        t = (tfdt or 0) / a_ts_sec
        entries.append((t, 1, frag))
    entries.sort(key=lambda e: (e[0], e[1]))

    out = bytearray(ftyp)
    out.extend(moov)
    for _, _, frag in entries:
        out.extend(frag)
    return bytes(out)