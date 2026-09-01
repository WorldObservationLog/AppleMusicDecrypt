"""Log-view widget.

Renders the contents of ``src.tui.log_sink`` as a scrollable,
colour-aware pane.  ANSI escape codes from loguru are converted to
prompt_toolkit ``StyleAndTextTuples`` via a simple regex parser so the
existing ``GlobalLogger`` / ``RipLogger`` colour scheme is preserved
without any changes to the logger internals.

Scroll behaviour
----------------
* Default: **auto-tail** – always shows the most-recent N lines that fit.
* After the user presses ↑ / PgUp (or scrolls up): **scroll mode** –
  the view is pinned at ``_offset`` lines from the bottom.
* Pressing End (or the view receiving a new log line while in scroll
  mode does *not* forcibly reset – the user must press End explicitly).
* ``scroll_to_bottom()`` resets to auto-tail; called by the app on End.
"""

from __future__ import annotations

import re
from typing import Callable

from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.key_binding import KeyBindings

from src.tui import log_sink

# ---------------------------------------------------------------------------
# ANSI → StyleAndTextTuples converter
# ---------------------------------------------------------------------------

# Mapping from ANSI 256-colour / basic codes to approximate Catppuccin tokens.
# We only need to handle what loguru + our RipLogger actually emit.
_ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")

# Map select SGR codes to style strings understood by prompt_toolkit.
_SGR_TO_STYLE: dict[str, str] = {
    "0":  "",          # reset
    "1":  "bold",
    "32": "#a6e3a1",   # green  → success / timestamp
    "33": "#f9e2af",   # yellow → warning
    "31": "#f38ba8",   # red    → error
    "34": "#89b4fa",   # blue   → info
    "35": "#cba6f7",   # magenta → tag
    "36": "#89dceb",   # cyan
    "37": "#cdd6f4",   # white
    "90": "#6c7086",   # dark grey → debug
}


def _ansi_to_tuples(raw: str) -> StyleAndTextTuples:
    """Convert a loguru-formatted ANSI string to prompt_toolkit tuples."""
    result: StyleAndTextTuples = []
    current_style = ""
    style_parts: list[str] = []
    pos = 0
    for m in _ANSI_RE.finditer(raw):
        # Text before this escape.
        if m.start() > pos:
            result.append((current_style, raw[pos:m.start()]))
        # Update style.  style_parts persists across consecutive SGR codes
        # (e.g. "[33m[1m" => yellow + bold), and is only cleared
        # by an explicit reset (0).
        codes = m.group(1).split(";")
        for code in codes:
            mapped = _SGR_TO_STYLE.get(code)
            if mapped is None:
                continue
            if mapped == "":
                style_parts = []    # reset
            else:
                style_parts.append(mapped)
        current_style = " ".join(style_parts)
        pos = m.end()
    # Trailing text.
    if pos < len(raw):
        result.append((current_style, raw[pos:]))
    # Ensure the line ends with a newline for the Window renderer.
    if result and not result[-1][1].endswith("\n"):
        result.append(("", "\n"))
    elif not result:
        result.append(("", "\n"))
    return result


# ---------------------------------------------------------------------------
# LogView
# ---------------------------------------------------------------------------

class LogView:
    """Scrollable log pane backed by ``log_sink``."""

    def __init__(self) -> None:
        self._offset: int  = 0      # lines from the bottom (0 = auto-tail)
        self._tail:   bool = True   # True → auto-tail mode
        self._cursor_pos = None     # (row, col) hint for Window auto-scroll

        self.control = FormattedTextControl(
            text=self._get_text,
            focusable=True,
            show_cursor=False,
            get_cursor_position=lambda: self._cursor_pos,
        )
        # Click-to-focus for the log pane (FormattedTextControl has no
        # built-in focus_on_click like BufferControl does).
        from prompt_toolkit.mouse_events import MouseEventType
        from prompt_toolkit.application.current import get_app as _get_app

        _orig_mouse = self.control.mouse_handler

        def _mouse_handler(mouse_event):
            if (mouse_event.event_type == MouseEventType.MOUSE_UP
                    and _get_app().layout.current_control != self.control):
                _get_app().layout.current_control = self.control
                return None
            return _orig_mouse(mouse_event)

        self.control.mouse_handler = _mouse_handler
        self.window = Window(
            content=self.control,
            wrap_lines=True,   # long lines wrap instead of being clipped
            allow_scroll_beyond_bottom=False,
        )

    # ------------------------------------------------------------------ #
    # Scroll API (called by keybindings in app.py)
    # ------------------------------------------------------------------ #

    def scroll_up(self, lines: int = 1) -> None:
        self._tail = False
        self._offset = min(self._offset + lines,
                           max(0, len(log_sink.get_lines()) - 1))

    def scroll_down(self, lines: int = 1) -> None:
        self._offset = max(0, self._offset - lines)
        if self._offset == 0:
            self._tail = True

    def scroll_to_bottom(self) -> None:
        self._offset = 0
        self._tail   = True

    @property
    def is_tailing(self) -> bool:
        return self._tail

    # ------------------------------------------------------------------ #
    # Renderer
    # ------------------------------------------------------------------ #

    def _get_text(self) -> StyleAndTextTuples:
        lines = log_sink.get_lines()
        if not lines:
            self._cursor_pos = None
            return [("class:log.text", "(no log output yet)\n")]

        # Determine the window of lines to show.
        # prompt_toolkit will scroll the Window content automatically;
        # we hand it *all* lines and let the Window clip.
        # When in scroll mode we want to anchor the view so that the
        # line at _offset from the bottom is at the *bottom* of the
        # visible area.  We achieve this by trimming the tail.
        if not self._tail and self._offset > 0:
            end = max(1, len(lines) - self._offset + 1)
            lines = lines[:end]

        result: StyleAndTextTuples = []
        for raw in lines:
            result.extend(_ansi_to_tuples(raw))

        # Cursor on the last rendered line: the Window keeps the view
        # scrolled to the bottom (auto-tail) as new lines arrive.
        # Point(x=column, y=row).
        self._cursor_pos = Point(x=0, y=len(lines) - 1)
        return result
