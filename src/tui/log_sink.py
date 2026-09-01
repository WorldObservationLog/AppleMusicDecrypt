"""Log sink: captures loguru output into a thread-safe deque for the TUI.

Drop-in replacement for the prompt_toolkit ``print_formatted_text`` sink
used by ``GlobalLogger`` / ``RipLogger``.  When the TUI is active every log
line is appended to ``_LOG_BUF`` (a bounded deque) as a pre-formatted ANSI
string.  The log-view widget reads from that deque via ``get_lines()``.

Public API
----------
install()   – replace the existing ``_safe_print`` target; call once at TUI
              startup before ``Application.run_async()``.
uninstall() – restore the original ``_safe_print`` behaviour (for teardown).
get_lines() – return a snapshot list[str] of recent log lines (newest last).
push(msg)   – internal; also callable from tests.
"""

from __future__ import annotations

import sys
from collections import deque
from typing import Callable

# Maximum number of log lines retained in memory.
_MAX_LINES = 2_000

_LOG_BUF: deque[str] = deque(maxlen=_MAX_LINES)

# Reference to the *original* _safe_print so we can restore it.
_original_safe_print: Callable[[str], None] | None = None

# Flag: True while the TUI sink is active.
_installed: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def push(msg: str) -> None:
    """Append a raw log string (may contain ANSI escapes) to the buffer."""
    # Normalise line endings; strip the trailing newline loguru always adds.
    _LOG_BUF.append(msg.rstrip("\n").rstrip("\r"))


def get_lines() -> list[str]:
    """Return a snapshot of all buffered lines (oldest → newest)."""
    return list(_LOG_BUF)


def install() -> None:
    """Redirect GlobalLogger / RipLogger output into the TUI deque."""
    global _original_safe_print, _installed
    if _installed:
        return

    import src.logger as _logger_mod  # import here to avoid circular deps

    _original_safe_print = _logger_mod._safe_print

    def _tui_sink(msg: str) -> None:
        push(msg)

    _logger_mod._safe_print = _tui_sink  # type: ignore[attr-defined]
    _installed = True


def uninstall() -> None:
    """Restore the original _safe_print (stdout / prompt_toolkit)."""
    global _original_safe_print, _installed
    if not _installed or _original_safe_print is None:
        return

    import src.logger as _logger_mod

    _logger_mod._safe_print = _original_safe_print  # type: ignore[attr-defined]
    _original_safe_print = None
    _installed = False
