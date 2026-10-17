"""Incremental log filter row.

Shown on demand (``/``) as a one-line bar above the command input, hidden again
with ``Esc``.  It is a sibling of the command line rather than a modal: the
point of a log filter is to watch the log change while you type, and a modal
would cover it.

The widget owns nothing but the text field.  It posts :class:`SearchBar.Changed`
on every keystroke and :class:`SearchBar.Closed` when the user backs out; the
app decides what those mean (it debounces the replay and clears the filter).
"""

from __future__ import annotations

from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Input, Static

PREFIX = "/"


class SearchBar(Horizontal):
    """One-line filter field for the log pane."""

    BINDINGS = [
        # ``priority`` so the app-level bindings (focus cycling, exit) cannot
        # swallow Escape while the user is inside the field.
        Binding("escape", "dismiss", "Close", show=False, priority=True),
    ]

    class Changed(Message):
        """Posted on every keystroke with the text currently entered."""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    class Closed(Message):
        """Posted when the user finishes (Enter) or backs out (Esc)."""

        def __init__(self, value: str, cancelled: bool) -> None:
            self.value = value
            self.cancelled = cancelled
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._prefix = Static(PREFIX, id="search-prefix")
        self._input = Input(
            placeholder="filter log lines (Enter keeps, Esc clears)",
            id="search-input",
        )

    def compose(self):
        yield self._prefix
        yield self._input

    # ------------------------------------------------------------------ #

    @property
    def input(self) -> Input:
        return self._input

    @property
    def value(self) -> str:
        return self._input.value

    def focus_input(self) -> None:
        self._input.focus()

    def reset(self) -> None:
        """Clear the field without emitting anything."""
        self._input.value = ""

    # ------------------------------------------------------------------ #

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self.post_message(self.Changed(event.value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.post_message(self.Closed(event.value, cancelled=False))

    def action_dismiss(self) -> None:
        self.post_message(self.Closed(self._input.value, cancelled=True))
