"""Status bar widget — bottom-most line of the TUI.

Renders a single styled line with three zones:
  LEFT  : ↓ speed  🔓 dec-speed  tasks N
  CENTRE: wrapper region tags (JP  HK  TW …) – fetched from WrapperClient
  RIGHT : mode indicator + keyboard hints

The control is purely read-only (no focus, no cursor).
"""

from __future__ import annotations

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.dimension import Dimension as D

from creart import it
from src.measurer import Measurer


class StatusBar:
    """One-line status bar at the bottom of the screen."""

    def __init__(
        self,
        get_regions:    "Callable[[], list[str]]",
        is_tailing:     "Callable[[], bool]",
        is_batch:       "Callable[[], bool]",
    ) -> None:
        self._get_regions = get_regions
        self._is_tailing  = is_tailing
        self._is_batch    = is_batch

        self.control = FormattedTextControl(
            text=self._render,
            focusable=False,
            show_cursor=False,
        )
        self.window = Window(
            content=self.control,
            height=D.exact(1),
            style="class:tui.statusbar",
        )

    # ------------------------------------------------------------------ #

    def _render(self) -> StyleAndTextTuples:
        m = it(Measurer)

        # ── left ─────────────────────────────────────────────────────────
        out: StyleAndTextTuples = [
            ("class:tui.statusbar.key",   " ↓ "),
            ("class:tui.statusbar.value", m.download_speed()),
            ("class:tui.statusbar.sep",   "  "),
            ("class:tui.statusbar.key",   "🔓 "),
            ("class:tui.statusbar.value", m.decrypt_speed()),
            ("class:tui.statusbar.sep",   "  "),
            ("class:tui.statusbar.key",   "tasks "),
            ("class:tui.statusbar.value", str(m.tasks_count())),
            ("class:tui.statusbar.sep",   "   "),
        ]

        # ── centre: regions ──────────────────────────────────────────────
        regions = self._get_regions()
        if regions:
            for r in regions:
                out.append(("class:tui.statusbar.region", r.upper()))
                out.append(("class:tui.statusbar.sep",    "  "))
        else:
            out.append(("class:tui.statusbar.key", "(no regions)"))
            out.append(("class:tui.statusbar.sep",  "   "))

        # ── mode indicators ───────────────────────────────────────────────
        if self._is_batch():
            out.append(("class:tui.statusbar.batch", "[BATCH]  "))
        if not self._is_tailing():
            out.append(("class:tui.statusbar.scroll", "[SCROLL ↑↓ End=tail]  "))

        # ── right: key hints ─────────────────────────────────────────────
        out += [
            ("class:tui.statusbar.sep",   " "),
            ("class:tui.statusbar.key",   "[Tab]"),
            ("class:tui.statusbar",       "Focus  "),
            ("class:tui.statusbar.key",   "[F1]"),
            ("class:tui.statusbar",       "Help  "),
            ("class:tui.statusbar.key",   "[F10]"),
            ("class:tui.statusbar",       "Exit "),
        ]
        return out
