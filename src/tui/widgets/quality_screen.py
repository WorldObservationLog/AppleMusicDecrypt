"""Scrollable table view for ``qa`` output.

Why this exists: ``quality.py`` prints a ``tabulate`` grid, which is a fixed
width decided by its content (~90 columns for the seven quality fields) and
knows nothing about the terminal.  Piped into the log pane it was broken —
at 68 columns every border wrapped onto a second line and the columns stopped
lining up, so the output was unreadable exactly where it was most useful.

A :class:`DataTable` solves it properly: columns size themselves to the
content, the whole thing scrolls in both directions, and a row can be selected
to copy.

Several tables can be merged into one screen.  ``qa <album>`` asks the wrapper
once per track, so a 14-track album produces 14 results a fraction of a second
apart; the app collects them for a moment and passes them here as one table
with a leading ``Song`` column, rather than stacking 14 modal screens.
"""

from __future__ import annotations

from typing import Optional, Sequence

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

HINT = "↑↓ scroll · ←→ pan · c copy row · Esc close"


class QualityScreen(ModalScreen[None]):
    """One or more quality tables, shown as a scrollable grid."""

    BINDINGS = [
        # ``priority`` so the app-level bindings cannot swallow the close keys
        # while the table has focus.
        Binding("escape", "close", "Close", show=False, priority=True),
        Binding("f1", "close", "Close", show=False, priority=True),
        Binding("q", "close", "Close", show=False, priority=True),
        # Plain ``c``: nothing in this screen takes text input.
        Binding("c", "copy_row", "Copy row", show=False),
    ]

    def __init__(
        self,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        title: str = "QUALITY",
        note: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._headers = list(headers)
        self._rows = [list(row) for row in rows]
        self._title = title
        self._note = note

    def compose(self):
        with Vertical(id="quality-dialog"):
            yield Static(HINT, id="quality-hint")
            yield DataTable(id="quality-table", zebra_stripes=True)

    def on_mount(self) -> None:
        dialog = self.query_one("#quality-dialog")
        dialog.border_title = f" {self._title} "
        if self._note:
            dialog.border_subtitle = f" {self._note} "

        table = self.query_one("#quality-table", DataTable)
        table.cursor_type = "row"
        table.add_columns(*self._headers)
        for row in self._rows:
            # Cells must be str for DataTable's width maths; ints/floats/None
            # arrive straight from the pydantic model.
            table.add_row(*["" if cell is None else str(cell) for cell in row])
        table.focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def action_copy_row(self) -> None:
        """Copy the highlighted row as TSV, so it pastes into a spreadsheet."""
        table = self.query_one("#quality-table", DataTable)
        row_index = table.cursor_row
        if row_index is None or not (0 <= row_index < len(self._rows)):
            return
        row = self._rows[row_index]
        text = "\t".join("" if cell is None else str(cell) for cell in row)
        if self.app.copy_to_system_clipboard(text):
            self.notify("Row copied", timeout=2)
        else:
            self.notify("Clipboard unavailable", severity="warning", timeout=3)

    # ------------------------------------------------------------------ #
    # Merging
    # ------------------------------------------------------------------ #

    @staticmethod
    def merge(
        tables: Sequence[tuple[str, list[str], list[list[str]]]],
        label_column: str = "Song",
    ) -> Optional[tuple[list[str], list[list[str]], str, str]]:
        """Fold several results into one table with a leading label column.

        Returns ``(headers, rows, title, note)``, or ``None`` when *tables* is
        empty.  Results whose headers differ are kept as-is under a single
        header set — the fields come from the same ``qa`` invocation, so in
        practice they only differ if the caller asked for different fields.
        """
        if not tables:
            return None
        if len(tables) == 1:
            title, headers, rows = tables[0]
            return list(headers), [list(row) for row in rows], "QUALITY", title

        headers = list(tables[0][1])
        rows: list[list[str]] = []
        for title, _headers, body in tables:
            label = title.split(":", 1)[-1].strip() or title
            for row in body:
                rows.append([label, *row])
        return ([label_column, *headers], rows, "QUALITY",
                f"{len(tables)} results")
