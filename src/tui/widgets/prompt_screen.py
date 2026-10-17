"""Single-line modal prompt.

Used by ``login`` / ``logout`` when they need a value from the user *while the
full-screen TUI is running*.  The old implementation built a throwaway
``prompt_toolkit.PromptSession`` inside the running application, which meant two
frameworks fighting over the same terminal; a ``ModalScreen`` is just another
screen in the same app, so the terminal is never handed over.

Input is masked when ``password=True`` (Textual's ``Input`` renders the
placeholder instead of the value, and its value is still readable through the
API).
"""

from __future__ import annotations

from typing import Optional

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class PromptScreen(ModalScreen[Optional[str]]):
    """Ask for one line of text.

    Dismisses with the entered string, or ``None`` when cancelled
    (Esc / Ctrl+C).  An empty submission is returned as an empty string — the
    caller decides whether that is valid.
    """

    BINDINGS = [
        # ``priority=True`` keeps the app-level Ctrl+C binding (two-step exit)
        # from firing while the user is answering a prompt.
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("ctrl+c", "cancel", "Cancel", show=False, priority=True),
    ]

    def __init__(self, prompt: str, password: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._prompt = prompt
        self._password = password

    @property
    def prompt(self) -> str:
        return self._prompt

    @property
    def password(self) -> bool:
        return self._password

    def compose(self):
        with Vertical(id="prompt-dialog"):
            # markup=False: the question can contain ``<`` (a URL, an error
            # message) and must be shown verbatim.
            yield Static(self._prompt, id="prompt-label", markup=False)
            yield Input(
                password=self._password,
                placeholder="(hidden)" if self._password else "",
                id="prompt-input",
            )

    def on_mount(self) -> None:
        self.query_one("#prompt-dialog").border_title = (
            " PASSWORD " if self._password else " INPUT "
        )
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)
