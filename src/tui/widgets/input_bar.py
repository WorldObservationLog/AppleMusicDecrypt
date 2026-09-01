"""Command input bar widget.

A single-line TextArea with:
  • Tab-completion (NestedCompleter, reused from InteractiveShell.completer())
  • InMemoryHistory (↑/↓ to browse)
  • Dynamic prefix:  "> " in normal mode, "[BATCH] > " in batch mode
  • Submission fires a callback (async) on Enter
"""

from __future__ import annotations

from typing import Callable, Awaitable

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.containers import VSplit, Window
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.lexers import SimpleLexer
from prompt_toolkit.widgets import TextArea


class InputBar:
    """Single-line command input at the bottom of the main layout."""

    def __init__(
        self,
        completer,                                     # NestedCompleter
        on_submit: Callable[[str], Awaitable[None]],   # async command handler
        is_batch:  Callable[[], bool],
    ) -> None:
        self._on_submit = on_submit
        self._is_batch  = is_batch

        self._history = InMemoryHistory()

        self._textarea = TextArea(
            height=D.exact(1),
            multiline=False,
            wrap_lines=False,
            completer=completer,
            complete_while_typing=True,
            history=self._history,
            accept_handler=self._on_accept,
            lexer=SimpleLexer(style="class:tui.input.prompt"),
            style="class:tui.input.prompt",
        )

        # Prefix label (changes in batch mode).
        self._prefix_control = FormattedTextControl(
            text=self._prefix_text,
            focusable=False,
            show_cursor=False,
        )

        self.container = VSplit([
            Window(content=self._prefix_control,
                   width=D(preferred=8, max=8),   # widest prefix = "[BATCH] "
                   style="class:tui.input.prompt"),
            self._textarea,
        ])

        # Expose the underlying window so the app can set focus.
        self.window = self._textarea.window

    # ------------------------------------------------------------------ #

    def _prefix_text(self) -> StyleAndTextTuples:
        # Prefixes are left-aligned in an 8-column window; normal mode is
        # simply "> " (6 trailing columns stay blank while typing).
        if self._is_batch():
            return [("class:tui.input.batch", "[BATCH] ")]
        return [("class:tui.input.prompt", "> ")]

    def _on_accept(self, buf: Buffer) -> bool:
        text = buf.text.strip()
        if text:
            import asyncio
            asyncio.get_event_loop().create_task(self._on_submit(text))
        return True   # clears the buffer after accept

    def get_text(self) -> str:
        return self._textarea.text
