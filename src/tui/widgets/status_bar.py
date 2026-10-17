"""Status bar — bottom-most line of the TUI.

One line, three zones:

  LEFT   ⬇ dl-speed  🔓 dec-speed  tasks N  [BATCH] [SCROLL End=tail]
  CENTRE wrapper region tags (JP HK TW), from the cached
         ``WrapperClient._last_regions`` (populated by the ``status`` command)
  RIGHT  keyboard hints

The line is assembled against the terminal width instead of being handed to
the renderer as one long string.  Appending everything and letting the pane
clip it meant the right-hand hints sat far from the right edge on a wide
terminal, and on a narrow one the whole group was cut off — measured at 62
columns, ``[F1]Help [F10]Exit`` disappeared entirely.  Here:

* the hint set is graded by available width, richest first, and drops to
  nothing on a very narrow terminal;
* the region tags take whatever is left in the middle and are dropped before
  either end is sacrificed;
* the row is padded so the hints end flush with the right edge.

Widths are measured with :func:`rich.cells.cell_len` so the emoji glyphs count
for what they actually occupy.

Read-only: the widget never takes focus.  Content is refreshed by the app on a
timer (speeds and task counts are read from ``Measurer``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from creart import it
from rich.cells import cell_len
from textual.content import Content
from textual.widgets import Static

from src.measurer import Measurer
from src.tui import glyphs
from src.tui.style import rich_style

if TYPE_CHECKING:
    from src.tui.task_tree import TreeStats

# Keyboard hints, richest first.  The first entry whose minimum terminal width
# fits is used, then trimmed to the space actually available.
_HINT_LEVELS: tuple[tuple[int, tuple[tuple[str, str], ...]], ...] = (
    (88, (("[Tab]", "Focus  "), ("[F1]", "Help  "), ("[F10]", "Exit "))),
    (54, (("[F1]", "Help  "), ("[F10]", "Exit "))),
    (26, (("[F10]", "Exit "),)),
    (0, ()),
)

# Left zone, richest first: the metric group itself has to shrink on a narrow
# terminal, otherwise the bar overflows before the hints even matter.  Order
# reflects what a waiting user needs — progress and transfer rate outrank the
# decrypt rate and the speed history, which is why the sparkline is the first
# thing to go.
_LEFT_LEVELS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (104, ("dl", "spark", "progress", "eta", "dec")),
    (78, ("dl", "spark", "progress", "eta")),
    (52, ("dl", "progress", "eta")),
    (34, ("dl", "progress")),
    (18, ("dl",)),
    (0, ()),
)

# Columns the middle zone needs before it is worth showing region tags at all.
_MID_MIN = 6

# Width assumed when the bar is rendered without a terminal behind it: an
# unmounted widget (or one built in a unit test) reports a zero size, and
# ``self.app`` raises once there is no active app.  The value is wide enough
# that such a render still exercises all three zones.
_DEFAULT_WIDTH = 120

# Eight-step block ramp for the speed sparkline.
_SPARK_GLYPHS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float], width: int) -> str:
    """Render the last *width* samples as a block-character trend line.

    Scaled against the window's own peak rather than an absolute rate, so a
    slow link still shows its shape instead of a flat line of lowest blocks.
    """
    if not values:
        return ""
    window = values[-width:]
    peak = max(window)
    if peak <= 0:
        return _SPARK_GLYPHS[0] * len(window)
    # Map the range onto the ramp's endpoints, so the peak is a full block and
    # half the peak sits in the middle of the ramp.  Scaling by len() instead
    # would put the midpoint one step high.
    top = len(_SPARK_GLYPHS) - 1
    return "".join(
        _SPARK_GLYPHS[min(top, int(value / peak * top))]
        for value in window
    )


def _fmt_duration(seconds: float) -> str:
    """Compact ``1:05`` / ``2h10m`` style duration."""
    if seconds != seconds or seconds < 0:  # NaN or nonsense
        return "--"
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}:{total % 60:02d}"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


def _progress_label(stats: "TreeStats") -> str:
    """``8/12 · 67%`` — track-level progress over the whole tree."""
    if not stats.total:
        return ""
    percent = int((stats.track_percent or 0.0) * 100)
    return f"{stats.done}/{stats.total} · {percent}%"


def _eta_label(stats: "TreeStats") -> str:
    """``ETA 2:15`` from the remaining bytes and the current download rate.

    Prefixed with ``~`` while some tracks have no known size yet and the
    figure is an extrapolation rather than a measurement.
    """
    remaining = stats.remaining_bytes
    if remaining <= 0:
        return ""
    speed = it(Measurer).download_speed_bps()
    if speed <= 0:
        return ""
    prefix = "ETA ~" if stats.unknown else "ETA "
    return prefix + _fmt_duration(remaining / speed)


class StatusBar(Static):
    """One-line status bar at the bottom of the screen."""

    def __init__(
        self,
        get_regions: Callable[[], list[str]],
        is_tailing: Callable[[], bool],
        is_batch: Callable[[], bool],
        get_stats: Callable[[], "TreeStats"],
        log_filter: Callable[[], str] = lambda: "",
        suppressed_alerts: Callable[[], int] = lambda: 0,
        pending_lines: Callable[[], int] = lambda: 0,
        **kwargs,
    ) -> None:
        super().__init__("", **kwargs)
        self._get_regions = get_regions
        self._is_tailing = is_tailing
        self._is_batch = is_batch
        self._get_stats = get_stats
        self._log_filter = log_filter
        self._suppressed_alerts = suppressed_alerts
        self._pending_lines = pending_lines

    def on_mount(self) -> None:
        self.refresh_content()

    # ------------------------------------------------------------------ #

    def refresh_content(self) -> None:
        """Re-read the metrics and repaint the line."""
        try:
            self.update(self._render())
        except Exception:
            # A metrics hiccup must not take the UI down.
            pass

    # ------------------------------------------------------------------ #
    # Width helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _width_of(segments: list[tuple[str, str]]) -> int:
        return sum(cell_len(text) for text, _ in segments)

    def _available_width(self) -> int:
        try:
            width = self.size.width or self.app.size.width
        except Exception:
            # No active app: the widget is not mounted (unit tests build one
            # directly and call _render).
            width = 0
        return max(1, width or _DEFAULT_WIDTH)

    # ------------------------------------------------------------------ #
    # Zones
    # ------------------------------------------------------------------ #

    def _left_segments(self, total: int) -> list[tuple[str, str]]:
        """Speeds, progress and mode flags, graded by width."""
        for min_width, parts in _LEFT_LEVELS:
            if total >= min_width:
                break

        m = it(Measurer)
        stats = (self._get_stats()
                 if any(part in parts for part in ("progress", "eta"))
                 else None)
        segments: list[tuple[str, str]] = []

        if "dl" in parts:
            segments += [
                (f" {glyphs.symbol('down')} ", rich_style("tui.statusbar.key")),
                (m.download_speed(), rich_style("tui.statusbar.value")),
            ]
        if "spark" in parts:
            spark = _sparkline(m.speed_history(), 10)
            if spark:
                segments.append((" " + spark, rich_style("tui.statusbar.spark")))
        if "progress" in parts and stats is not None:
            label = _progress_label(stats)
            if label:
                segments += [
                    ("  ", rich_style("tui.statusbar.sep")),
                    (label, rich_style("tui.statusbar.progress")),
                ]
        if "eta" in parts and stats is not None:
            eta = _eta_label(stats)
            if eta:
                segments += [
                    ("  ", rich_style("tui.statusbar.sep")),
                    (eta, rich_style("tui.statusbar.eta")),
                ]
        if "dec" in parts:
            segments += [
                ("  ", rich_style("tui.statusbar.sep")),
                (f"{glyphs.symbol('decrypt')} ", rich_style("tui.statusbar.key")),
                (m.decrypt_speed(), rich_style("tui.statusbar.value")),
            ]

        # The mode flags matter more than any metric — they explain why the
        # input behaves the way it does — so they survive at every width, but
        # the scroll hint is spelled out only when there is room.
        if self._is_batch():
            segments.append(("  [BATCH]", rich_style("tui.statusbar.batch")))
        # An active log filter hides lines, so it has to be visible: otherwise
        # a quiet log looks like a stalled download.
        active_filter = self._log_filter()
        if active_filter:
            segments.append(("  [LOG " + active_filter + "]",
                             rich_style("tui.statusbar.filter")))
        # Alerts that were merged away instead of shown as a toast.  Reported
        # here because a count costs no screen space, while a toast covers the
        # interface.
        merged = self._suppressed_alerts()
        if merged:
            segments.append((f"  [+{merged} more error"
                             f"{'s' if merged != 1 else ''}]",
                             rich_style("tui.statusbar.alert")))
        if not self._is_tailing():
            scroll = "[SCROLL End=tail]" if total >= 72 else "[SCROLL]"
            segments.append(("  " + scroll, rich_style("tui.statusbar.scroll")))
            # While the view is paused, say how much has piled up below it.
            pending = self._pending_lines()
            if pending:
                segments.append((f"  ↓{pending}", rich_style("tui.statusbar.pending")))
        return segments

    def _hint_segments(self, total: int, budget: int) -> list[tuple[str, str]]:
        """Right-hand hints: the richest set that fits *budget* columns."""
        for min_width, items in _HINT_LEVELS:
            if total < min_width:
                continue
            segments: list[tuple[str, str]] = []
            for key, label in items:
                segments.append((key, rich_style("tui.statusbar.key")))
                segments.append((label, rich_style("tui.statusbar")))
            if self._width_of(segments) <= budget:
                return segments
        return []

    def _region_segments(self, budget: int) -> list[tuple[str, str]]:
        """Wrapper region tags, truncated to *budget* columns."""
        if budget <= 0:
            return []
        regions = self._get_regions()
        if not regions:
            placeholder: list[tuple[str, str]] = [
                ("(no regions)", rich_style("tui.statusbar.key")),
            ]
            return placeholder if self._width_of(placeholder) <= budget else []

        out: list[tuple[str, str]] = []
        used = 0
        for index, region in enumerate(regions):
            piece = region.upper()
            cost = cell_len(piece) + (2 if index else 0)
            if used + cost > budget:
                break
            if index:
                out.append(("  ", rich_style("tui.statusbar.sep")))
            out.append((piece, rich_style("tui.statusbar.region")))
            used += cost
        return out

    # ------------------------------------------------------------------ #

    def _render(self) -> Content:
        total = self._available_width()

        left = self._left_segments(total)
        left_w = self._width_of(left)

        # Reserve the middle zone before choosing hints, so a narrow terminal
        # gives up the region tags rather than the exit hint.
        hints = self._hint_segments(total, max(0, total - left_w - _MID_MIN))
        right_w = self._width_of(hints)

        middle_budget = total - left_w - right_w - 4
        # Below ~22 columns the left zone has dropped every metric to keep the
        # line inside the terminal; showing region tags there would be the
        # only thing on the bar, so skip them too.
        regions = (
            self._region_segments(middle_budget)
            if middle_budget > 0 and left
            else []
        )
        regions_w = self._width_of(regions)

        # Pad so the hints finish flush with the right edge.  A tiny pad just
        # becomes the gutter (the region tags already reserve four columns).
        pad = max(0, total - left_w - regions_w - right_w)
        left_pad = pad // 2
        right_pad = pad - left_pad

        text = Content("")
        for chunk, style in left:
            text = text.append_text(chunk, style=style)
        if left_pad:
            text = text.append_text(" " * left_pad,
                                    style=rich_style("tui.statusbar"))
        for chunk, style in regions:
            text = text.append_text(chunk, style=style)
        if right_pad:
            text = text.append_text(" " * right_pad,
                                    style=rich_style("tui.statusbar"))
        for chunk, style in hints:
            text = text.append_text(chunk, style=style)
        return text
