"""Batch-mode input panel.

A modal screen shown when the user activates batch mode (``dl -b``).  The user
types one URL per line; ``Ctrl+D`` submits and ``Esc`` cancels.

Replaces the prompt_toolkit ``Float`` overlay + ``ConditionalContainer``: the
old version had to be built and kept mounted at all times, with visibility
driven by a filter, and its keys were bound at the app level behind an
``is_batch`` condition.  A ``ModalScreen`` is created on demand, owns its own
focus and keys, and dismisses with a result the app can await.

Pasting fifty URLs is the point of this panel, so it says what it received:

* lines **wrap** (``soft_wrap=True``) — a long Apple Music URL used to run off
  the right edge with a horizontal scrollbar, hiding the tail that usually
  carries the track id;
* a live counter reports entries, unique URLs, duplicates and lines that do
  not look like URLs at all, so a broken paste is caught before submitting
  rather than as fifty failures in the log.

Duplicates are collapsed on submit; nothing else is dropped.  A line that does
not look like a URL is *flagged*, not discarded — the command layer is the
authority on what is a valid URL, and silently dropping input would be worse
than letting it report its own error.
"""

from __future__ import annotations

from typing import Optional

from textual.binding import Binding
from textual.containers import Vertical
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea

from src.tui.style import rich_style

HINT = "One URL per line — Ctrl+D submit · Esc cancel"


def _looks_like_url(line: str) -> bool:
    return line.lower().startswith(("http://", "https://"))


class BatchPanel(ModalScreen[Optional[list[str]]]):
    """Modal multi-line URL input.

    Dismisses with the list of unique non-empty URLs, or ``None`` when
    cancelled.
    """

    BINDINGS = [
        # ``priority=True`` is required: TextArea binds ``ctrl+d`` itself
        # (delete-right), and a non-priority screen binding only runs after the
        # focused widget declined the key.
        Binding("ctrl+d", "submit", "Submit", show=False, priority=True),
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        # ModalScreen otherwise binds ctrl+c to quitting the whole app, which
        # would bypass the shell's two-step exit confirmation.  Inside the
        # panel it cancels instead.
        Binding("ctrl+c", "cancel", "Cancel", show=False, priority=True),
    ]

    def __init__(self, cmd_prefix: str = "dl", **kwargs) -> None:
        super().__init__(**kwargs)
        self._cmd_prefix = cmd_prefix

    @property
    def cmd_prefix(self) -> str:
        return self._cmd_prefix

    def compose(self):
        with Vertical(id="batch-dialog"):
            yield Static(HINT, id="batch-hint")
            # soft_wrap: Apple Music URLs are long, and the interesting part
            # (the id) is at the end.
            yield TextArea(id="batch-text", soft_wrap=True)
            yield Static("", id="batch-stats")

    def on_mount(self) -> None:
        dialog = self.query_one("#batch-dialog")
        dialog.border_title = f" BATCH INPUT ({self._cmd_prefix}) "
        self.query_one("#batch-text", TextArea).focus()
        self._refresh_stats()

    # ------------------------------------------------------------------ #
    # Contents
    # ------------------------------------------------------------------ #

    def lines(self) -> list[str]:
        """Every non-empty line as typed, duplicates included."""
        raw = self.query_one("#batch-text", TextArea).text
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _analyse(self) -> tuple[list[str], int, int]:
        """``(unique URLs, duplicate count, suspicious count)``."""
        seen: set[str] = set()
        unique: list[str] = []
        duplicates = 0
        suspicious = 0
        for line in self.lines():
            if not _looks_like_url(line):
                suspicious += 1
            if line in seen:
                duplicates += 1
                continue
            seen.add(line)
            unique.append(line)
        return unique, duplicates, suspicious

    def urls(self) -> list[str]:
        """Unique entries, in the order they were pasted."""
        return self._analyse()[0]

    # ------------------------------------------------------------------ #

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        event.stop()
        self._refresh_stats()

    def _refresh_stats(self) -> None:
        """Repaint the counter line under the editor."""
        entries, duplicates, suspicious = self._analyse()
        total = len(self.lines())
        widget = self.query_one("#batch-stats", Static)

        text = Content("")
        if not total:
            text = text.append_text(" paste URLs — each line becomes one command",
                                    style=rich_style("tui.batch.dim"))
            widget.update(text)
            return

        text = text.append_text(f" {total} line{'s' if total != 1 else ''}",
                                style=rich_style("tui.batch.text"))
        text = text.append_text(f"  ·  {len(entries)} unique",
                                style=rich_style("tui.batch.ok"))
        if duplicates:
            text = text.append_text(f"  ·  {duplicates} duplicate"
                                    f"{'s' if duplicates != 1 else ''} (skipped)",
                                    style=rich_style("tui.batch.warn"))
        if suspicious:
            text = text.append_text(f"  ·  {suspicious} not a URL",
                                    style=rich_style("tui.batch.warn"))
        widget.update(text)

    # ------------------------------------------------------------------ #

    def action_submit(self) -> None:
        self.dismiss(self.urls())

    def action_cancel(self) -> None:
        self.dismiss(None)
