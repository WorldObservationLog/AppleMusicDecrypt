"""Full-screen help panel.

Opened with ``F1``.  Replaces the old behaviour of dumping the key table into
the log, where it scrolled away and could not be searched.

Two of the three sections generate themselves from the running application
rather than from a hand-maintained copy:

* **Keys** come from ``App.BINDINGS``, so a new binding shows up here for free
  and a removed one cannot linger;
* **Commands** come from ``InteractiveShell.completion_tree()``, the same
  grammar that drives the input's completion, so the syntax shown is the
  syntax that is accepted.

Only the short command notes and the mouse/pane paragraph are written by hand;
``cmd.py``'s ``print_help`` remains the verbose version that goes to the log.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional

from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Static

from src.tui.style import rich_style

# One-line description per top-level command, keyed by command name.
_COMMAND_NOTES: dict[str, str] = {
    "dl": "download a song, album, playlist, artist or music video",
    "qa": "list the available audio qualities without downloading",
    "status": "wrapper status and the available storefront regions",
    "login": "sign in on the wrapper side",
    "logout": "sign out",
    "cl": "remove finished tasks from the sidebar",
    "help": "print the long form of this help into the log",
    "exit": "quit (asks again within 5s while tasks are running)",
}

# Bindings whose description is empty or duplicated are still worth showing,
# but a hand-written label reads better than an enum name.
_KEY_LABELS: dict[str, str] = {
    "focus_next": "Focus",
    "focus_previous": "Focus",
    "search_log": "Filter the log",
    "cycle_log_level": "Cycle the log level floor",
    "narrow_sidebar": "Narrow / widen the task sidebar",
    "widen_sidebar": "Narrow / widen the task sidebar",
    "confirm_exit": "Quit",
    "help": "This panel",
    "cycle_theme": "Cycle the colour theme",
    "expand_all_tasks": "Expand every group in the sidebar",
    "collapse_all_tasks": "Fold every group in the sidebar",
    "copy_task_name": "Copy the highlighted task's name",
    "remove_task": "Remove the highlighted finished task",
    "retry_task": "Retry it (a group retries only its failures)",
    "cancel_task": "Cancel the running task under the cursor",
}

# Not expressible as a Binding: handled by a focused widget or by the mouse.
_PANE_HELP: tuple[tuple[str, str], ...] = (
    ("Tab", "complete the word, or move focus when there is nothing to complete"),
    ("Enter / click", "fold or unfold a group in the task sidebar"),
    ("Up / Down", "scroll the log, or walk the command history"),
    ("PgUp / PgDn / Home", "scroll the log"),
    ("End", "resume following the log after scrolling back"),
    ("Wheel", "scroll the pane under the pointer"),
    ("Click", "focus a pane"),
    ("Ctrl+D / Esc", "submit / cancel the batch panel or a prompt"),
)


def _pretty_key(key: str) -> str:
    """Turn a binding key into something a human reads at a glance."""
    special = {
        "slash": "/",
        "tab": "Tab",
        "escape": "Esc",
        "return": "Enter",
        "space": "Space",
    }
    parts = []
    for piece in key.split("+"):
        lowered = piece.lower()
        if lowered in special:
            parts.append(special[lowered])
        elif lowered.startswith("f") and lowered[1:].isdigit():
            parts.append(lowered.upper())
        elif len(lowered) == 1:
            parts.append(lowered.upper())
        else:
            parts.append(lowered.capitalize())
    return "+".join(parts)


def describe_keys(bindings: Iterable[Binding]) -> list[tuple[str, str]]:
    """``[(keys, what it does)]``, one row per distinct action."""
    grouped: dict[str, list[str]] = {}
    for binding in bindings:
        action = getattr(binding, "action", "") or ""
        label = _KEY_LABELS.get(action) or getattr(binding, "description", "") or action
        grouped.setdefault(label, []).append(_pretty_key(binding.key))
    # ``·`` rather than ``/`` as the separator: one of the keys *is* a slash
    # ("F3 · /" reads; "F3 / /" does not).
    return [(" · ".join(keys), label) for label, keys in grouped.items()]


def describe_command(name: str, spec: object) -> str:
    """Render one ``completion_tree`` entry as a usage line."""
    if not isinstance(spec, Mapping):
        return name
    options: list[str] = []
    for option, value in spec.items():
        if isinstance(value, Mapping) and value:
            choices = "|".join(str(choice) for choice in list(value)[:4])
            options.append(f"[{option} <{choices}>]")
        else:
            options.append(f"[{option}]")
    return " ".join([name, *options])


class HelpScreen(ModalScreen[None]):
    """Scrollable key and command reference."""

    BINDINGS = [
        # ``priority`` so the app-level bindings cannot swallow the close keys.
        Binding("escape", "close", "Close", show=False, priority=True),
        Binding("f1", "close", "Close", show=False, priority=True),
        Binding("q", "close", "Close", show=False, priority=True),
    ]

    def __init__(
        self,
        bindings: Iterable[Binding],
        commands: Optional[Mapping[str, object]] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        # NOT ``self._bindings``: that is Textual's own binding registry, and
        # shadowing it breaks key handling for the whole screen.
        self._binding_list = list(bindings)
        self._commands = dict(commands or {})

    # ------------------------------------------------------------------ #

    def compose(self):
        with Vertical(id="help-dialog"):
            with VerticalScroll(id="help-scroll"):
                yield Static(self._render(), id="help-body")

    def on_mount(self) -> None:
        self.query_one("#help-dialog").border_title = " HELP "
        self.query_one("#help-scroll").focus()

    def action_close(self) -> None:
        self.dismiss(None)

    # ------------------------------------------------------------------ #

    def _render(self) -> Content:
        text = Content("")

        def add(chunk: str, token: str) -> None:
            nonlocal text
            text = text.append_text(chunk, style=rich_style(token))

        def heading(title: str) -> None:
            add(title + "\n", "tui.help.heading")

        def row(left: str, right: str, left_width: int) -> None:
            # A long left column wraps its description onto the next line
            # instead of running into it (``dl [--codec …]`` is wider than any
            # sensible fixed column).
            if len(left) >= left_width:
                add("  " + left + "\n", "tui.help.key")
                add(" " * (left_width + 2) + right + "\n", "tui.help.text")
                return
            add("  " + left.ljust(left_width), "tui.help.key")
            add(right + "\n", "tui.help.text")

        def column_width(rows: list[tuple[str, str]], cap: int, floor: int = 18) -> int:
            longest = max((len(left) for left, _ in rows), default=floor)
            return min(cap, longest + 2)

        heading("Keys")
        key_rows = describe_keys(self._binding_list) + list(_PANE_HELP)
        key_width = column_width(key_rows, cap=24)
        for keys, label in key_rows:
            row(keys, label, key_width)

        if self._commands:
            add("\n", "tui.help.text")
            heading("Commands")
            command_rows = [
                (describe_command(name, spec), _COMMAND_NOTES.get(name, ""))
                for name, spec in self._commands.items()
            ]
            command_width = column_width(command_rows, cap=46)
            for left, right in command_rows:
                row(left, right, command_width)

        add("\n", "tui.help.text")
        heading("Examples")
        example_rows = [
            ("dl https://music.apple.com/…/album/1234567890", "whole album, ALAC"),
            ("dl <url> -c aac -l ja", "AAC with Japanese metadata"),
            ("dl -b", "paste many URLs at once"),
            ("qa <url> --bit-depth", "just the bit depth"),
            ("cl", "tidy the sidebar"),
        ]
        example_width = column_width(example_rows, cap=48)
        for left, right in example_rows:
            row(left, right, example_width)

        add("\n", "tui.help.text")
        add("  Esc / F1 / q to close\n", "tui.help.hint")
        return text
