"""Command input bar widget.

A single-line TextArea with:
  • Tab-completion (NestedCompleter, reused from InteractiveShell.completer())
  • InMemoryHistory — ↑/↓ walk history (scoped to this buffer in app.py)
  • Home/End jump to line start/end
  • Mode-dependent prefix, rendered in a mode-matched window:
      normal  "> "        (2-column window)
      batch   "[BATCH] "  (8-column window)
  • Enter: appends to history, fires the async ``on_submit`` callback
    and clears the line.
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
            # Termux narrow terminals: live-completion floats can fight the
            # on-screen keyboard and swallow input; Tab still completes.
            complete_while_typing=False,
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

        # Prefix window width adapts to mode: 2 cols ("> ") in normal mode,
        # 8 cols ("[BATCH] ") in batch mode.  ConditionalContainer swaps them.
        from prompt_toolkit.filters import Condition
        from prompt_toolkit.layout.containers import ConditionalContainer
        self.container = VSplit([
            ConditionalContainer(
                Window(content=self._prefix_control,
                       width=D.exact(2),
                       style="class:tui.input.prompt"),
                filter=Condition(lambda: not self._is_batch()),
            ),
            ConditionalContainer(
                Window(content=self._prefix_control,
                       width=D.exact(8),
                       style="class:tui.input.prompt"),
                filter=Condition(self._is_batch),
            ),
            self._textarea,
        ])

        # Expose the underlying window so the app can set focus.
        self.window = self._textarea.window

    # ------------------------------------------------------------------ #

    def _prefix_text(self) -> StyleAndTextTuples:
        # Each mode's prefix fills its (mode-matched) window exactly.
        if self._is_batch():
            return [("class:tui.input.batch", "[BATCH] ")]
        return [("class:tui.input.prompt", "> ")]

    def _on_accept(self, buf: Buffer) -> bool:
        text = buf.text.strip()
        if text:
            # Append to history so ↑/↓ can recall it later.
            self._history.append_string(text)
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(self._on_submit(text))
        buf.reset()          # clear the input line after Enter
        return False

    def get_text(self) -> str:
        return self._textarea.text

    # ------------------------------------------------------------------ #
    # History / cursor keybindings (merged into the app's keybindings)
    # ------------------------------------------------------------------ #

    def key_bindings(self):
        """↑/↓ history navigation + Home/End line jumps, scoped to the
        input buffer so they don't clash with log-pane scrolling."""
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.filters import has_focus

        kb = KeyBindings()
        cond = has_focus(self._textarea.window)

        @kb.add("up", filter=cond)
        def _history_prev(event):
            self._history_previous()

        @kb.add("down", filter=cond)
        def _history_next(event):
            self._history_next()

        @kb.add("home", filter=cond)
        def _home(event):
            self._textarea.buffer.cursor_position = 0

        @kb.add("end", filter=cond)
        def _end(event):
            self._textarea.buffer.cursor_position = len(self._textarea.buffer.text)

        return kb

    def _history_previous(self) -> None:
        """Recall the previous command (↑)."""
        strings = self._history.get_strings()
        if not strings:
            return
        self._h_index = getattr(self, "_h_index", len(strings)) - 1
        self._h_index = max(0, self._h_index)
        self._textarea.buffer.set_document(
            type(self._textarea.buffer.document)(
                strings[self._h_index], cursor_position=len(strings[self._h_index])
            ),
            bypass_readonly=True,
        )

    def _history_next(self) -> None:
        """Recall the next command (↓); past the newest one clears the line."""
        strings = self._history.get_strings()
        if not strings:
            return
        self._h_index = getattr(self, "_h_index", len(strings)) + 1
        if self._h_index >= len(strings):
            self._h_index = len(strings)
            self._textarea.buffer.reset()
            return
        self._h_index = max(0, self._h_index)
        text = strings[self._h_index]
        self._textarea.buffer.set_document(
            type(self._textarea.buffer.document)(
                text, cursor_position=len(text)
            ),
            bypass_readonly=True,
        )
