"""TUI layout definition.

Single full-screen split layout (log pane | task sidebar), input bar and
status bar.  Narrow-terminal stacked mode was removed.

  ┌ LOG (focusable, scrollable) ──────┬ TASKS (sidebar) ─┐
  │                                   │                    │
  ├ INPUT BAR ────────────────────────┴────────────────────┤
  └ STATUS BAR ───────────────────────────────────────────-┘
"""

from __future__ import annotations

from prompt_toolkit.layout.containers import (
    FloatContainer,
    Float,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.layout import Layout
from prompt_toolkit.widgets import Frame, HorizontalLine, VerticalLine

from src.tui.widgets.log_view   import LogView
from src.tui.widgets.task_list  import TaskListWidget, SIDEBAR_WIDTH
from src.tui.widgets.input_bar  import InputBar
from src.tui.widgets.batch_panel import BatchPanel
from src.tui.widgets.status_bar import StatusBar


# Current sidebar width (user-adjustable via Ctrl+Left/Ctrl+Right).
_sidebar_width = SIDEBAR_WIDTH


def set_sidebar_width(delta: int) -> None:
    """Adjust the sidebar width (kept in sync with the layout render)."""
    global _sidebar_width
    _sidebar_width = max(24, min(90, _sidebar_width + delta))


def get_sidebar_width() -> int:
    return _sidebar_width


def build_layout(
    log_view:    LogView,
    task_list:   TaskListWidget,
    input_bar:   InputBar,
    batch_panel: BatchPanel,
    status_bar:  StatusBar,
) -> tuple[Layout, list[Float]]:
    """Build and return the prompt_toolkit Layout plus the list of Floats."""

    # ── body: log | sidebar ──────────────────────────────────────────────
    body = VSplit([
        Frame(
            body=log_view.window,
            title=" LOG ",
            width=D(weight=7),
            style="class:frame.border",
        ),
        Frame(
            body=task_list.pane,
            title=" TASKS ",
            # Grow with content: preferred 52+4, never wider than 45% of
            # the screen so the log pane keeps room on small terminals.
            width=D(preferred=_sidebar_width + 4,
                    max=_sidebar_width + 4,
                    weight=3),
            style="class:frame.border",
        ),
    ])

    # ── input row ────────────────────────────────────────────────────────
    input_row = Frame(
        body=input_bar.container,
        height=D.exact(3),
        style="class:frame.border",
    )

    # ── full-screen stack ────────────────────────────────────────────────
    root = HSplit([
        body,
        input_row,
        status_bar.window,
    ])

    # ── floats (batch panel; help float added in app.py) ─────────────────
    floats: list[Float] = [batch_panel.float_container]

    layout = Layout(
        FloatContainer(content=root, floats=floats),
        focused_element=input_bar.window,
    )

    return layout, floats
