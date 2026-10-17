"""Log sink: captures log output into a thread-safe buffer for the TUI.

Every log line produced by ``GlobalLogger`` / ``RipLogger`` (and by
``quality.py``, which prints tabular output directly) is appended to a bounded
deque as a pre-formatted ANSI string.  The log panel renders from that buffer.

Threading
---------
Lines can be produced from two places:

* the event-loop thread — ``GlobalLogger`` / ``RipLogger`` sinks called from
  the download/decrypt coroutines, ``quality.py``;
* a worker thread — ``src/utils.py`` runs blocking work through a
  ``ThreadPoolExecutor``.

Widgets may only be touched from the app's thread, so :func:`push` never calls
a widget.  It appends under the GIL and then *schedules* a wake-up on the event
loop with ``call_soon_threadsafe``, which is safe from any thread including the
loop's own.  Bursts are coalesced by a pending flag so a thousand lines in one
tick produce a single subscriber callback; the subscriber drains with
:func:`since`, which is index-based and therefore immune to ``deque`` eviction.

Public API
----------
install() / uninstall()  – swap ``src.logger._safe_print`` (call once at TUI
                           startup / teardown).
bind_loop(loop)          – event loop used for wake-ups (None ⇒ call inline).
subscribe(cb)            – register a ``Callable[[], None]`` wake-up callback.
unsubscribe(cb)          – remove it.
subscribe_alert(cb)      – register ``Callable[[str, str], None]``, called with
                           ``(level, line)`` for ERROR and above (toasts).
unsubscribe_alert(cb)    – remove it.
latest_seq()             – sequence number of the newest buffered line.
since(after)             – ``[(seq, line, level), ...]`` newer than ``after``.
get_lines()              – snapshot ``list[str]`` of recent lines (oldest
                           first); kept for tests and diagnostics.
push(msg)                – internal; also callable from tests.

Levels
------
loguru hands the sink a ``Message`` — a ``str`` subclass that still carries the
structured ``record`` — so the level name is available without parsing the
formatted text.  Plain strings (tests, ad-hoc callers) are recorded as INFO.
``get_lines()`` stays level-free for compatibility; :func:`since` is what the
panel uses, and it returns the level alongside each line so the viewer can
filter on it.
"""

from __future__ import annotations

import asyncio
import re
import threading
from collections import deque
from typing import Callable, Optional

# Maximum number of log lines retained in memory.
MAX_LINES = 2_000

# Levels that raise an alert (used for toasts) as well as being buffered.
ALERT_LEVELS = frozenset({"ERROR", "CRITICAL"})

# Severity order, so the viewer can filter on "this level and above".
LEVEL_ORDER: dict[str, int] = {
    "TRACE": 0,
    "DEBUG": 1,
    "INFO": 2,
    "SUCCESS": 3,
    "WARNING": 4,
    "ERROR": 5,
    "CRITICAL": 6,
}


def level_rank(level: str) -> int:
    """Numeric rank of a level name; unknown levels sort as INFO."""
    return LEVEL_ORDER.get(level.upper(), LEVEL_ORDER["INFO"])


# ---------------------------------------------------------------------------
# Line compaction
# ---------------------------------------------------------------------------
#
# The log pane is 60-70 columns wide once the sidebar has taken its third, and
# loguru's default prefix is a large part of that: the wall-clock timestamp is
# 23 columns and `RipLogger` repeats `| SONG | artist - title |` on every line
# of every track.  Measured on a real 14-track album, a plain
# `Selected codec: ...` line came to 113 columns and wrapped onto two rows.
#
# Both patterns are matched against loguru's colorized output, so an escape
# sequence sits between the parts.  Each substitution degrades to "leave the
# line alone" if the format ever changes — losing the compaction is fine,
# mangling the log is not.
_TS_ANSI = r"\x1b\[[0-9;]*m"
_TIMESTAMP_RE = re.compile(
    rf"({_TS_ANSI})(\d{{4}}-\d{{2}}-\d{{2}}) (\d{{2}}:\d{{2}}:\d{{2}})(?:\.\d+)?({_TS_ANSI})"
)
# `| SONG | <name> |` (or ALBUM / MV / …), each part wrapped in its own codes.
_SOURCE_RE = re.compile(
    rf"(\| ){_TS_ANSI}([A-Z]+){_TS_ANSI}( \| ){_TS_ANSI}([^|\x1b]*){_TS_ANSI}( \| )"
)


def _shorten_source(match: "re.Match[str]") -> str:
    """Drop the item type and the artist, keeping `| title |`.

    The type is already implied by the icon on the sidebar row and the artist
    is in the filename format, so repeating both on every line buys nothing
    and costs a third of the pane.
    """
    lead, _kind, mid, name, trail = match.groups()
    short = name.split(" - ", 1)[-1].strip() or name
    return f"{lead}{short}{trail}"


def compact(line: str) -> str:
    """Trim the parts of a loguru prefix the full-screen pane cannot afford.

    Only applied by the TUI: the legacy REPL and headless output keep the full
    timestamp and item name, where there is room for them and no live sidebar
    to duplicate them.
    """
    line = _TIMESTAMP_RE.sub(lambda m: f"{m.group(1)}{m.group(3)}{m.group(4)}", line)
    return _SOURCE_RE.sub(_shorten_source, line)


# (sequence, line, level) triples.  The sequence keeps incremental consumption
# correct even after the deque starts evicting old entries.
_LOG_BUF: deque[tuple[int, str, str]] = deque(maxlen=MAX_LINES)

# Reference to the *original* _safe_print so we can restore it.
_original_safe_print: Optional[Callable[[str], None]] = None

# Flag: True while the TUI sink is active.
_installed: bool = False

# --- wake-up plumbing -------------------------------------------------------

_lock = threading.Lock()
_listeners: list[Callable[[], None]] = []
_alert_listeners: list[Callable[[str, str], None]] = []
_pending_alerts: list[tuple[str, str]] = []
_loop: Optional[asyncio.AbstractEventLoop] = None
_seq: int = 0
_pending: bool = False


# ---------------------------------------------------------------------------
# Buffer
# ---------------------------------------------------------------------------

def push(msg) -> None:
    """Append a log line (may contain ANSI escapes) to the buffer.

    Safe to call from any thread.  *msg* is a plain string or a loguru
    ``Message``; the latter also supplies the level, which drives the viewer's
    level filter and the ERROR toasts.
    """
    global _seq
    record = getattr(msg, "record", None)
    level = record["level"].name if record is not None else "INFO"
    # Normalise line endings; strip the trailing newline loguru always adds.
    line = compact(str(msg).rstrip("\n").rstrip("\r"))
    _seq += 1
    _LOG_BUF.append((_seq, line, level))
    if level.upper() in ALERT_LEVELS:
        with _lock:
            _pending_alerts.append((level, line))
    _notify()


def get_lines() -> list[str]:
    """Return a snapshot of all buffered lines (oldest → newest)."""
    return [line for _, line, _ in list(_LOG_BUF)]


def latest_seq() -> int:
    """Sequence number of the most recently pushed line (0 when empty)."""
    return _seq


def since(after: int) -> list[tuple[int, str, str]]:
    """Return ``[(seq, line, level), ...]`` for every line newer than *after*.

    Scans only the newly appended tail, so a steady-state tick is O(new lines).
    """
    out: list[tuple[int, str, str]] = []
    for item in reversed(_LOG_BUF):
        if item[0] <= after:
            break
        out.append(item)
    out.reverse()
    return out


def clear() -> None:
    """Drop every buffered line (used when the view is reset)."""
    _LOG_BUF.clear()


# ---------------------------------------------------------------------------
# Wake-up plumbing
# ---------------------------------------------------------------------------

def bind_loop(loop: Optional[asyncio.AbstractEventLoop]) -> None:
    """Set the event loop used to deliver wake-ups to subscribers.

    ``None`` (the default) disables delivery entirely — see :func:`_notify`.
    Pass the running loop before mounting a UI that subscribes.
    """
    global _loop
    _loop = loop


def subscribe(callback: Callable[[], None]) -> None:
    """Register *callback* to be woken when new lines arrive."""
    with _lock:
        if callback not in _listeners:
            _listeners.append(callback)


def unsubscribe(callback: Callable[[], None]) -> None:
    with _lock:
        if callback in _listeners:
            _listeners.remove(callback)


def subscribe_alert(callback: Callable[[str, str], None]) -> None:
    """Register *callback* for ERROR-and-above lines, called as ``(level, line)``.

    Separate from :func:`subscribe` because the handling differs: an alert is
    *shown* (a toast) and must not be lost when the log pane is filtered or
    scrolled away, while a wake-up only means "there is more to drain".
    """
    with _lock:
        if callback not in _alert_listeners:
            _alert_listeners.append(callback)


def unsubscribe_alert(callback: Callable[[str, str], None]) -> None:
    with _lock:
        if callback in _alert_listeners:
            _alert_listeners.remove(callback)


def _notify() -> None:
    """Schedule a subscriber wake-up, coalescing bursts into one callback.

    With no event loop bound (``bind_loop(None)``) nothing is dispatched: a
    subscriber must never run on the thread that produced the log line.  Lines
    are still buffered, so a panel that mounts later drains them via
    :func:`since` in its first read.
    """
    global _pending
    with _lock:
        if _pending or not (_listeners or _alert_listeners) or _loop is None:
            return
        _pending = True
        loop = _loop

    try:
        loop.call_soon_threadsafe(_dispatch)
    except RuntimeError:
        # The loop is closed (shutdown path) — dispatch inline so the pending
        # flag is cleared rather than latching.
        _dispatch()


def _dispatch() -> None:
    """Run subscriber callbacks on the app's thread."""
    global _pending
    with _lock:
        _pending = False
        callbacks = list(_listeners)
        alert_callbacks = list(_alert_listeners)
        alerts = list(_pending_alerts)
        _pending_alerts.clear()
    for callback in callbacks:
        try:
            callback()
        except Exception:
            # A broken subscriber must never kill the logging path.
            pass
    for callback in alert_callbacks:
        for level, line in alerts:
            try:
                callback(level, line)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------

def install() -> None:
    """Redirect GlobalLogger / RipLogger output into the TUI buffer."""
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
    """Restore the original _safe_print (stdout / rich console)."""
    global _original_safe_print, _installed
    if not _installed or _original_safe_print is None:
        return

    import src.logger as _logger_mod

    _logger_mod._safe_print = _original_safe_print  # type: ignore[attr-defined]
    _original_safe_print = None
    _installed = False
