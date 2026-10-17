"""Command input row.

A single-line ``Input`` with a mode-dependent prefix label, command history on
↑/↓ and context-aware completion.

Replaces the prompt_toolkit ``TextArea`` + ``Buffer`` + ``InMemoryHistory``
combination.  Two things the old implementation did by hand are now built in:
the input widget itself, and the cursor/selection handling (so the Home/End and
history bindings that previously set ``buffer.cursor_position`` manually are
gone).

Completion note
---------------
prompt_toolkit offered a nested *popup menu*; Textual's ``Suggester`` surfaces a
single inline suggestion, and the key that accepts it is ``→`` — **not** Tab,
which the app binds to focus cycling.  :class:`CommandSuggester` keeps the same
nested grammar from ``InteractiveShell.completion_tree()`` and resolves the
active branch from the tokens already typed, so ``dl --codec al`` still
completes to ``dl --codec alac``.

Because a single inline suggestion only shows the *first* match, the row also
renders a hint line listing every candidate for the token being typed
(``--codec  --include-participate-songs  --language``).  That line appears only
while there is something to complete, and it never takes focus — choosing from
it is still a matter of typing one more character.
"""

from __future__ import annotations

from typing import Optional

from rich.cells import cell_len
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.message import Message
from textual.suggester import Suggester
from textual.widgets import Input, Static

from src.tui.style import rich_style
from src.tui.win_console import get_clipboard_text

# Maximum number of commands kept in the in-memory history.
HISTORY_LIMIT = 500

# Prefix label per mode (the CSS sets the matching column width).
PREFIX_NORMAL = ">"
PREFIX_BATCH = "[BATCH]"

# Longest candidate list drawn before the remainder is summarised as "+N".
MAX_HINTS = 12


class CommandSuggester(Suggester):
    """Context-aware completion driven by a nested command grammar.

    *tree* is the same nested dict the old ``NestedCompleter`` consumed: keys
    are completable words and ``None`` marks a leaf.  Only the word currently
    being typed is completed, so completing never fires while the user is
    starting a fresh token.
    """

    def __init__(self, tree: dict, case_sensitive: bool = True) -> None:
        super().__init__(case_sensitive=case_sensitive)
        self._tree = tree

    # ------------------------------------------------------------------ #

    def _resolve(self, value: str) -> "tuple[str, list[str]]":
        """``(prefix already typed, matching words)`` for the current token."""
        if not value:
            return "", []
        tokens = value.split(" ")
        last = tokens[-1]
        if not last:
            # A trailing space starts a new token — stay quiet rather than
            # forcing the first keyword of the level on the user.
            return "", []

        # Walk down the grammar along the completed tokens.
        level: object = self._tree
        for token in tokens[:-1]:
            if not isinstance(level, dict):
                return "", []
            level = level.get(token)
            if level is None:
                return "", []
        if not isinstance(level, dict):
            return "", []

        if self.case_sensitive:
            matches = [key for key in level if key.startswith(last)]
        else:
            matches = [key for key in level if key.lower().startswith(last.lower())]
        # Declaration order, deliberately not sorted: this is the order the
        # user sees in the hint row, and ``get_suggestion`` accepts the first
        # entry, so the two must agree on what "the obvious completion" is.
        return value[: len(value) - len(last)], matches

    def candidates(self, value: str) -> list[str]:
        """Every completion for the token being typed, in stable order."""
        prefix, matches = self._resolve(value)
        return [prefix + match for match in matches]

    async def get_suggestion(self, value: str) -> Optional[str]:
        prefix, matches = self._resolve(value)
        if not matches:
            return None
        completion = prefix + matches[0]
        if completion == value:
            return None
        return completion


class SystemClipboardInput(Input):
    """An ``Input`` whose Ctrl+V reads the *system* clipboard.

    Textual binds Ctrl+V to ``paste``, which reads ``App.clipboard`` — an
    in-memory buffer holding only what this process copied.  Text copied in
    another window is invisible to it, and the escape sequence terminals use
    for copying (OSC 52) has no read side at all.  So the OS clipboard is
    read directly, with the in-memory buffer as the fallback.
    """

    BINDINGS = [Binding("ctrl+v", "paste_system", "Paste", show=False)]

    def action_paste_system(self) -> None:
        text = get_clipboard_text()
        if not text:
            self.action_paste()
            return
        # A command is one line: fold a multi-line paste instead of inserting
        # newlines that would each look like a submission.
        self.insert_text_at_cursor(" ".join(text.split()))


class CommandInput(Vertical):
    """Command input bar: prefix label + single-line input + candidate hints."""

    BINDINGS = [
        Binding("up", "history_previous", "History back", show=False),
        Binding("down", "history_next", "History forward", show=False),
        # Tab completes when there is something to complete and otherwise falls
        # through to focus cycling, the way a shell does it.  Textual's Input
        # does not bind Tab, so an ordinary (non-priority) binding here sees it
        # first and can hand it back.
        Binding("tab", "complete_or_focus", "Complete", show=False),
    ]

    class Submitted(Message):
        """Posted when the user submits a non-empty command."""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def __init__(
        self,
        suggester: Optional[Suggester] = None,
        placeholder: str = "dl <url> | qa <url> | help",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._history: list[str] = []
        self._h_index: int = 0
        self._batch: bool = False
        self._prefix = Static(PREFIX_NORMAL, id="input-prefix")
        self._input = SystemClipboardInput(
            placeholder=placeholder,
            id="command-input",
            suggester=suggester,
        )
        self._hints = Static("", id="input-hints")

    def compose(self):
        with Horizontal(id="input-row"):
            yield self._prefix
            yield self._input
        yield self._hints

    # ------------------------------------------------------------------ #
    # Accessors
    # ------------------------------------------------------------------ #

    @property
    def input(self) -> Input:
        """The inner ``Input`` widget (used for focus and value access)."""
        return self._input

    @property
    def history(self) -> list[str]:
        return list(self._history)

    def set_history(self, items: list[str]) -> None:
        """Replace the history (used to restore a previous session).

        Entries are appended to whatever is already there and the list is
        trimmed to ``HISTORY_LIMIT``, so a caller may seed defaults first.
        """
        if not items:
            return
        self._history.extend(items)
        if len(self._history) > HISTORY_LIMIT:
            del self._history[:-HISTORY_LIMIT]
        self._h_index = len(self._history)

    def focus_input(self) -> None:
        self._input.focus()

    def prefill(self, text: str) -> None:
        """Put *text* in the field with the cursor at the end, and focus it.

        Used by the command palette, so choosing "Download…" lands the user on
        a ready command line rather than an empty one.
        """
        self._set_value(text)
        self._refresh_hints(text)
        self.focus_input()

    # ------------------------------------------------------------------ #
    # Batch mode
    # ------------------------------------------------------------------ #

    def set_batch(self, active: bool) -> None:
        """Switch the prefix between ``>`` and ``[BATCH]``."""
        if self._batch == active:
            return
        self._batch = active
        self._prefix.update(PREFIX_BATCH if active else PREFIX_NORMAL)
        self._prefix.set_class(active, "batch")

    # ------------------------------------------------------------------ #
    # Candidate hints
    # ------------------------------------------------------------------ #

    def _candidates(self, value: str) -> list[str]:
        suggester = self._input.suggester
        if suggester is None or not hasattr(suggester, "candidates"):
            return []
        try:
            return list(suggester.candidates(value))  # type: ignore[attr-defined]
        except Exception:
            return []

    def _refresh_hints(self, value: str) -> None:
        """Draw the candidate list for the token being typed.

        Only the trailing token of each candidate is drawn — the input field
        already shows the rest — and the line disappears entirely when there
        is nothing to complete, so it costs no vertical space at rest.
        """
        candidates = self._candidates(value)
        if not candidates:
            self._hints.display = False
            return

        typed = value.split(" ")[-1]
        prefix_len = len(value) - len(typed)
        labels = [candidate[prefix_len:] for candidate in candidates]

        try:
            width = max(1, self.size.width - 4)
        except Exception:
            width = 76

        text = Content("")
        used = 0
        shown = 0
        for index, label in enumerate(labels[:MAX_HINTS]):
            piece = (" " if index == 0 else "  ") + label
            cost = cell_len(piece)
            if used + cost > width and shown:
                break
            token = ("tui.input.hint.first" if index == 0
                     else "tui.input.hint")
            text = text.append_text(piece, style=rich_style(token))
            used += cost
            shown += 1

        remaining = len(labels) - shown
        if remaining > 0:
            text = text.append_text(f"  +{remaining}",
                                    style=rich_style("tui.input.hint.more"))

        self._hints.update(text)
        self._hints.display = True

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._refresh_hints(event.value)

    def action_complete_or_focus(self) -> None:
        """Accept the first candidate, or move focus when there is none.

        Tab does both jobs in this UI and the ambiguity resolves itself by
        context: mid-word there is always something to complete, at rest there
        never is.
        """
        candidates = self._candidates(self._input.value)
        if not candidates:
            self.app.action_focus_next()
            return
        accepted = candidates[0]
        self._set_value(accepted)
        self._refresh_hints(accepted)

    # ------------------------------------------------------------------ #
    # History
    # ------------------------------------------------------------------ #

    def _set_value(self, value: str) -> None:
        self._input.value = value
        self._input.cursor_position = len(value)

    def action_history_previous(self) -> None:
        if not self._history:
            return
        self._h_index = max(0, self._h_index - 1)
        self._set_value(self._history[self._h_index])

    def action_history_next(self) -> None:
        if not self._history or self._h_index >= len(self._history):
            return
        self._h_index += 1
        if self._h_index >= len(self._history):
            self._set_value("")
        else:
            self._set_value(self._history[self._h_index])

    # ------------------------------------------------------------------ #
    # Submit
    # ------------------------------------------------------------------ #

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        self._hints.display = False
        if not text:
            return
        self._history.append(text)
        if len(self._history) > HISTORY_LIMIT:
            del self._history[:-HISTORY_LIMIT]
        self._h_index = len(self._history)
        event.stop()
        self.post_message(self.Submitted(text))
