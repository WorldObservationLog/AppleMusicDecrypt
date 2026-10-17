"""Persisted UI preferences for the full-screen TUI.

A tiny ``key = value`` store kept next to ``config.toml``.  Deliberately
separate from :mod:`src.tui.history` — that file is one command per line and is
meant to be greppable, whereas this is a handful of settings that are rewritten
whole.

Only choices the user actually made are stored.  The sidebar width is written
only once it has been set by hand: persisting the *automatic* width would pin
the sidebar forever, so a later, larger terminal would never get its extra
columns back.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

SETTINGS_FILENAME = ".applemusicdecrypt_tui"

# Recognised keys (unknown ones are preserved on load but not surfaced).
THEME_KEY = "theme"
SIDEBAR_WIDTH_KEY = "sidebar_width"


def settings_path(base: Optional[Path] = None) -> Path:
    """Location of the settings file (``config.toml``'s directory by default)."""
    directory = base if base is not None else Path.cwd()
    return directory / SETTINGS_FILENAME


def load(path: Optional[Path] = None) -> dict[str, str]:
    """Read the stored settings.  Returns ``{}`` when unreadable."""
    target = path if path is not None else settings_path()
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"')
        if key:
            values[key] = value
    return values


def save(values: Mapping[str, object], path: Optional[Path] = None) -> bool:
    """Write *values* atomically enough for a settings file.

    Every failure mode is non-fatal: a read-only directory must not stop the
    application from starting or exiting cleanly.
    """
    target = path if path is not None else settings_path()
    body = "".join(
        f"{key} = {value}\n"
        for key, value in sorted(values.items())
        if value is not None and str(value) != ""
    )
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_text(
            "# AppleMusicDecrypt full-screen UI preferences\n" + body,
            encoding="utf-8",
        )
        temporary.replace(target)
        return True
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False
