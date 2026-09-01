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
    DynamicContainer,
    FloatContainer,
    Float,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.filters import has_focus
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
    def _log_frame():
        return Frame(
            body=log_view.window,
            title=" LOG ",
            width=D(weight=7),
            style="class:frame.border.focused" if has_focus(log_view.window)()
                  else "class:frame.border",
        )

    def _task_frame():
        return Frame(
            body=task_list.pane,
            title=" TASKS ",
            width=D(preferred=get_sidebar_width() + 4,
                    max=get_sidebar_width() + 4,
                    weight=3),
            style="class:frame.border.focused" if has_focus(task_list._inner_window)()
                  else "class:frame.border",
        )

    body = VSplit([
        DynamicContainer(_log_frame),
        DynamicContainer(_task_frame),
    ])

    # ── input row ────────────────────────────────────────────────────────
    def _input_frame():
        return Frame(
            body=input_bar.container,
            height=D.exact(3),
            style="class:frame.border.focused" if has_focus(input_bar.window)()
                  else "class:frame.border",
        )

    input_row = DynamicContainer(_input_frame)

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
