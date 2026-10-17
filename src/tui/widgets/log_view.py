"""Log panel — RichLog-backed, ANSI-aware log pane.

Replaces the old ``FormattedTextControl`` + ``Window`` pair.  ``RichLog``
supplies what the previous implementation had to fake:

* real ANSI colour through Rich (``Text.from_ansi``) instead of a hand-written
  10-entry SGR table, so 256-colour and background codes survive;
* native scrolling bindings (up / down / pageup / pagedown / home / end) and a
  scrollbar, instead of emulating scrolling by slicing the line list;
* ``max_lines`` retention instead of a separate bounded buffer.

Wrapping
--------
Lines wrap to the pane width and the pane never scrolls sideways.  That takes
two things beyond ``wrap=True``: ``min_width`` has to come down from
RichLog's default of 78 (a floor that keeps a narrow pane rendering 78-cell
lines), and the backlog has to be re-wrapped on resize, because RichLog only
re-renders its deferred writes on the *first* resize.  ``_raw`` keeps the
original ANSI lines so :meth:`LogPanel._reflow` can replay them at the new
width.

Follow behaviour
----------------
* The pane follows the tail by default.
* Scrolling back into history pauses the follow so incoming lines do not yank
  the view away while you are reading.
* Scrolling back down to the bottom, or pressing ``End``, resumes it.

``RichLog.auto_scroll`` is a plain on/off switch — it is *not* cleared when the
user scrolls — so the follow state is tracked here and re-applied on every
write.  Writes happen in :meth:`_drain`, driven by ``log_sink`` wake-ups rather
than a refresh timer.

Filtering
---------
Two independent filters, both applied when lines are written *and* when the
backlog is replayed, so switching one is instantaneous and does not lose
anything:

* a **level floor** (``set_min_level``) — ``WARNING`` hides the routine
  progress chatter of a long batch;
* a **substring** (``set_filter_text``) — the search box.

Because the backlog keeps every line with its level, and a rich-log write is
what "showing" means, filtering is a replay rather than a per-line visibility
flag.  Replaying two thousand lines on every keystroke would be wasteful, so
the search box filters on submit and the level floor (a key press) replays
immediately.
"""

from __future__ import annotations

from collections import deque

from rich.text import Text
from textual.widgets import RichLog

from src.tui import log_sink
from src.tui.log_sink import level_rank

# Keys that scroll away from the tail / back towards it.
_BACK_KEYS = frozenset({"up", "pageup", "home", "ctrl+pageup"})
_FORWARD_KEYS = frozenset({"down", "pagedown", "end", "ctrl+pagedown"})

# Level floor cycled by the log-filter key, in order.
LEVEL_CYCLE: tuple[str, ...] = ("INFO", "WARNING", "ERROR")

# Idle time before a resize replays the backlog.  A drag-resize fires this many
# times a second and every replay re-renders the whole buffer, which is linear
# in ``max_lines``.
_REFLOW_DELAY = 0.08


class LogPanel(RichLog):
    """Scrollable log pane fed by ``src.tui.log_sink``."""

    # Class-level default so the flag exists before __init__ completes.
    _follow: bool = True

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("max_lines", log_sink.MAX_LINES)
        kwargs.setdefault("auto_scroll", True)
        kwargs.setdefault("wrap", True)
        # Render at the pane's own width.  RichLog's default ``min_width`` is
        # 78, which keeps a narrow pane's lines 78 cells wide — that is what
        # produced a horizontal scrollbar instead of wrapped text.
        kwargs.setdefault("min_width", 1)
        # loguru emits ANSI escape codes, not Rich console markup.
        kwargs.setdefault("markup", False)
        kwargs.setdefault("highlight", False)
        super().__init__(**kwargs)
        self._seq = log_sink.latest_seq()
        # Raw (ANSI-bearing) lines with their level, kept so a resize or a
        # filter change can replay the backlog.
        self._raw: deque[tuple[str, str]] = deque(
            maxlen=self.max_lines or log_sink.MAX_LINES
        )
        self._last_width: int = -1
        # Filter state: level floor + substring.
        self._min_rank: int = level_rank("INFO")
        self._filter_text: str = ""
        # Lines that arrived while the view was scrolled back.  Without this
        # the status bar can only say "not following" — not how far behind the
        # view is, which is the part that decides whether to press End.
        self._pending_lines: int = 0
        # Debounce handle for the resize replay.
        self._reflow_timer = None

    # ------------------------------------------------------------------ #
    # Filters
    # ------------------------------------------------------------------ #

    @property
    def min_level(self) -> str:
        """Current level floor as a name."""
        for name in LEVEL_CYCLE:
            if level_rank(name) == self._min_rank:
                return name
        return "INFO"

    @property
    def filter_text(self) -> str:
        return self._filter_text

    @property
    def is_filtered(self) -> bool:
        return self._min_rank > level_rank("INFO") or bool(self._filter_text)

    def filter_label(self) -> str:
        """Compact description of the active filters ("" when unfiltered)."""
        parts: list[str] = []
        if self._min_rank > level_rank("INFO"):
            parts.append(self.min_level + "+")
        if self._filter_text:
            parts.append(f'"{self._filter_text}"')
        return " ".join(parts)

    def _passes(self, line: str, level: str) -> bool:
        if level_rank(level) < self._min_rank:
            return False
        if self._filter_text and self._filter_text.lower() not in line.lower():
            return False
        return True

    def set_min_level(self, level: str) -> None:
        """Raise or lower the level floor and replay the backlog."""
        rank = level_rank(level)
        if rank == self._min_rank:
            return
        self._min_rank = rank
        self._reflow()

    def cycle_min_level(self) -> str:
        """Advance the level floor; returns the level now in effect."""
        try:
            index = LEVEL_CYCLE.index(self.min_level)
        except ValueError:
            index = 0
        nxt = LEVEL_CYCLE[(index + 1) % len(LEVEL_CYCLE)]
        self.set_min_level(nxt)
        return nxt

    def set_filter_text(self, text: str, replay: bool = True) -> None:
        """Set the substring filter.

        ``replay=False`` records the filter without repainting, so a caller
        can debounce the (linear) backlog replay while the user is still
        typing and then call :meth:`replay` once.
        """
        cleaned = text.strip()
        if cleaned == self._filter_text:
            return
        self._filter_text = cleaned
        if replay:
            self._reflow()

    def replay(self) -> None:
        """Re-apply the current filters to the buffered backlog."""
        self._reflow()

    # ------------------------------------------------------------------ #
    # Follow state
    # ------------------------------------------------------------------ #

    @property
    def is_tailing(self) -> bool:
        return self._follow

    @property
    def pending_lines(self) -> int:
        """How many lines arrived below the viewport while it was paused."""
        return 0 if self._follow else self._pending_lines

    def _set_follow(self, follow: bool) -> None:
        self._follow = follow
        self.auto_scroll = follow
        if follow:
            self._pending_lines = 0

    def pause_follow(self) -> None:
        """Stop following the tail (user is reading history)."""
        self._set_follow(False)

    def resume_follow(self) -> None:
        """Re-enable tailing and jump to the bottom."""
        self._set_follow(True)
        self.scroll_end(animate=False)

    def _sync_follow_to_position(self) -> None:
        """Follow only while the viewport sits at the end of the buffer."""
        self._set_follow(self.is_vertical_scroll_end)

    # ------------------------------------------------------------------ #
    # Scrolling: let the built-in bindings scroll, then update the flag
    # ------------------------------------------------------------------ #

    def on_key(self, event) -> None:
        if event.key in _BACK_KEYS:
            self.pause_follow()
        elif event.key in _FORWARD_KEYS:
            # The binding scrolls on this same event; re-evaluate afterwards.
            self.call_after_refresh(self._sync_follow_to_position)

    def _on_mouse_scroll_up(self, event) -> None:
        self.pause_follow()

    def _on_mouse_scroll_down(self, event) -> None:
        self.call_after_refresh(self._sync_follow_to_position)

    # ------------------------------------------------------------------ #
    # Feeding from the sink
    # ------------------------------------------------------------------ #

    def on_mount(self) -> None:
        log_sink.subscribe(self._drain)
        self._drain()          # flush anything logged before mount

    def on_unmount(self) -> None:
        log_sink.unsubscribe(self._drain)

    # ------------------------------------------------------------------ #
    # Re-wrapping on resize
    # ------------------------------------------------------------------ #

    def on_resize(self, event) -> None:
        super().on_resize(event)
        # RichLog re-renders its deferred writes on the *first* resize only.
        # After that the stored lines keep the width they were rendered at, so
        # narrowing the pane leaves stale, over-wide lines behind.  Re-wrap
        # them from the raw backlog whenever the width actually changes — after
        # the next layout pass, because that is when the new width reaches
        # ``scrollable_content_region``, which we measure against.
        if event.size.width == self._last_width:
            return
        self._last_width = event.size.width
        if self._reflow_timer is not None:
            self._reflow_timer.stop()
        self._reflow_timer = self.set_timer(_REFLOW_DELAY, self._reflow)

    def _write(self, line: str) -> None:
        """Write one raw line, wrapped to the pane's current width.

        The width has to be passed explicitly.  Without it, ``RichLog.write``
        measures the content against ``self.app.console.options.max_width`` —
        the console is 80 cells wide, and ``expand`` defaults to ``False`` — so
        a pane wider than 80 cells would stop wrapping at 80 and a pane that
        grows would keep the shorter lines it already had.
        """
        width = self.scrollable_content_region.width
        self.write(Text.from_ansi(line), width=max(1, width))

    def _reflow(self) -> None:
        """Re-render every buffered line that passes the filters."""
        if not self.is_mounted:
            return
        raw = list(self._raw)
        self.clear()
        for line, level in raw:
            if self._passes(line, level):
                self._write(line)
        self.auto_scroll = self._follow
        if self._follow:
            self.scroll_end(animate=False)

    def _drain(self) -> None:
        """Append every line buffered since the last drain."""
        if not self.is_mounted:
            return
        pending = log_sink.since(self._seq)
        if not pending:
            return
        for seq, line, level in pending:
            self._raw.append((line, level))
            # Filtered-out lines are still buffered: raising the floor is a
            # replay, not a re-download of history.
            if self._passes(line, level):
                self._write(line)
                if not self._follow:
                    self._pending_lines += 1
            self._seq = seq
        # write() honours self.auto_scroll, which tracks the follow flag.
        self.auto_scroll = self._follow
