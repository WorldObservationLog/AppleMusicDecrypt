"""Glyphs used by the TUI, with an ASCII fallback.

Every icon here is emoji or a symbol that needs a font with wide coverage.
Over a serial console, in a ``TERM=dumb`` session, or through an SSH client
with a limited font they come out as boxes or single-width mojibake — and
because the sidebar lines up its columns by *measured* width, a glyph that is
two cells wide in one font and one cell wide in another throws every row off.

Set ``APPLEMUSICDECRYPT_ASCII=1`` (or call :func:`set_ascii`) to swap the whole
set for plain ASCII.  Widths stay right either way: they come from
:func:`rich.cells.cell_len`, not from a table of assumptions.
"""

from __future__ import annotations

import os

# Status icons, keyed by the ``NodeStatus`` member name.
_STATUS_ICONS: dict[str, str] = {
    "WAITING": "⏳",
    "RUNNING": "🔄",
    "DONE": "✅",
    "EXIST": "✔",
    "FAILED": "❌",
    "PARTIAL": "⚠",
}
_STATUS_ASCII: dict[str, str] = {
    "WAITING": "·",
    "RUNNING": ">",
    "DONE": "+",
    "EXIST": "=",
    "FAILED": "x",
    "PARTIAL": "!",
}

# Kind icons, keyed by the ``NodeKind`` member name.
_KIND_ICONS: dict[str, str] = {
    "ALBUM": "💿",
    "PLAYLIST": "📋",
    "ARTIST": "🎤",
    "MV": "🎬",
    "SONG": "🎵",
}
_KIND_ASCII: dict[str, str] = {
    "ALBUM": "A",
    "PLAYLIST": "P",
    "ARTIST": "R",
    "MV": "V",
    "SONG": "s",
}

# Loose decorations used by the status bar and a few hints.
_SYMBOLS: dict[str, str] = {
    "down": "⬇",
    "decrypt": "🔓",
    "ellipsis": "…",
}
_SYMBOLS_ASCII: dict[str, str] = {
    "down": "v",
    "decrypt": "d",
    "ellipsis": "...",
}

_ASCII_ENV = "APPLEMUSICDECRYPT_ASCII"
_FALSEY = ("", "0", "false", "no", "off")

_ascii = os.environ.get(_ASCII_ENV, "").strip().lower() not in _FALSEY


def use_ascii() -> bool:
    """Whether the ASCII glyph set is active."""
    return _ascii


def set_ascii(value: bool) -> None:
    """Switch glyph sets.  Callers re-render afterwards."""
    global _ascii
    _ascii = bool(value)


def status_icon(name: str) -> str:
    """Icon for a ``NodeStatus`` member name."""
    return (_STATUS_ASCII if _ascii else _STATUS_ICONS).get(name, "?")


def kind_icon(name: str) -> str:
    """Icon for a ``NodeKind`` member name."""
    return (_KIND_ASCII if _ascii else _KIND_ICONS).get(name, "?")


def symbol(name: str) -> str:
    """A status-bar decoration (``down`` / ``decrypt`` / ``playing`` / …)."""
    return (_SYMBOLS_ASCII if _ascii else _SYMBOLS).get(name, "")
