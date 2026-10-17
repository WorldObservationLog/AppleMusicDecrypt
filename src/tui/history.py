"""Persistent command history for the TUI command line.

The full-screen UI keeps its own history (the terminal's line editor is not in
the loop, unlike ``--legacy-ui``), so without this it would vanish on exit.
The file is written next to ``config.toml`` — that is, in the working directory
the program was started from — so a project checkout stays self-contained.

Every failure mode here is non-fatal: history is a convenience, and a read-only
directory or a corrupt file must never stop the application from starting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

HISTORY_FILENAME = ".applemusicdecrypt_history"

# Entries kept on disk; the widget trims to its own limit on load.
DEFAULT_LIMIT = 500


def history_path(base: Optional[Path] = None) -> Path:
    """Location of the history file (``config.toml``'s directory by default)."""
    directory = base if base is not None else Path.cwd()
    return directory / HISTORY_FILENAME


def load(path: Optional[Path] = None, limit: int = DEFAULT_LIMIT) -> list[str]:
    """Read the newest *limit* entries, oldest first.

    Returns an empty list when the file is missing or unreadable.
    """
    target = path if path is not None else history_path()
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.strip()][-limit:]


def save(
    items: Sequence[str],
    path: Optional[Path] = None,
    limit: int = DEFAULT_LIMIT,
) -> bool:
    """Write *items* (oldest first), keeping only the newest *limit*.

    Returns whether the file was written.  A blank entry is never stored, so a
    stray Enter does not push a useless line into the history.
    """
    target = path if path is not None else history_path()
    entries = [item for item in items if item.strip()][-limit:]
    try:
        target.write_text("\n".join(entries) + ("\n" if entries else ""),
                          encoding="utf-8")
        return True
    except OSError:
        return False
