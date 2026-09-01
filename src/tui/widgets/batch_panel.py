"""Batch-mode floating input panel.

Shown as a centred Float overlay when the user activates batch mode
(``dl -b``).  The user types one URL per line, then presses Ctrl+D or
clicks the [Submit] hint to commit; Esc cancels.

The panel owns its own multi-line TextArea.  On submit the text is split
on newlines and each non-empty line is passed to ``on_submit(urls)``.
"""

from __future__ import annotations

from typing import Callable, Awaitable

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.containers import (
    ConditionalContainer, Float, HSplit, Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.widgets import Frame, TextArea


class BatchPanel:
    """Floating multi-line URL input panel for batch mode."""

    def __init__(
        self,
        is_active:  Callable[[], bool],
        on_submit:  Callable[[list[str], str], Awaitable[None]],
        on_cancel:  Callable[[], None],
        get_cmd_prefix: Callable[[], str],   # e.g. "dl -c alac"
    ) -> None:
        self._is_active     = is_active
        self._on_submit     = on_submit
        self._on_cancel     = on_cancel
        self._get_cmd_prefix = get_cmd_prefix

        self._textarea = TextArea(
            multiline=True,
            wrap_lines=False,
            scrollbar=True,
            focus_on_click=True,
            style="class:tui.input.prompt",
            lexer=None,
        )

        hint_ctrl = FormattedTextControl(
            text=self._hint_text,
            focusable=False,
        )

        inner = HSplit([
            Window(content=hint_ctrl, height=D.exact(1)),
            self._textarea,
        ])

        self._frame = Frame(
            body=inner,
            title=" BATCH INPUT — one URL per line ",
            style="class:frame.border",
        )

        # ConditionalContainer: only rendered while batch mode is active.
        self.float_container = Float(
            content=ConditionalContainer(
                content=self._frame,
                filter=Condition(self._is_active),
            ),
            # Position: centred, leaving margin on all sides.
            left=6, right=6, top=4, bottom=6,
            xcursor=False, ycursor=False,
        )

        # Expose the TextArea window for focus management.
        self.window = self._textarea.window

    # ------------------------------------------------------------------ #

    def _hint_text(self) -> StyleAndTextTuples:
        return [
            ("class:tui.statusbar.key",  " Ctrl+D"),
            ("class:tui.statusbar",      " Submit    "),
            ("class:tui.statusbar.key",  "Esc"),
            ("class:tui.statusbar",      " Cancel "),
        ]

    async def submit(self) -> None:
        """Called by the app keybinding (Ctrl+D) when panel is active."""
        raw  = self._textarea.text
        urls = [u.strip() for u in raw.splitlines() if u.strip()]
        # Clear the textarea for the next batch.
        self._textarea.text = ""
        cmd_prefix = self._get_cmd_prefix()
        await self._on_submit(urls, cmd_prefix)

    def cancel(self) -> None:
        """Called by the app keybinding (Esc) when panel is active."""
        self._textarea.text = ""
        self._on_cancel()
