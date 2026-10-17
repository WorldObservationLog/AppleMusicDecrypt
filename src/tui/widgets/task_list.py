"""Task sidebar — a wrapped, multi-line list mirroring :class:`TaskTree`.

Why not a ``Tree``
------------------
The sidebar used to be a Textual ``Tree``.  Two things made that the wrong
widget for this panel:

* **Rows are one line each and the tree never wraps.**  A long
  ``artist - title`` widened the virtual canvas, so narrow terminals got a
  horizontal scrollbar instead of readable text.  ``Tree`` renders one
  ``Strip`` per node, so wrapping cannot be enabled from the outside.
* **``TreeNode.add()`` defaults to ``allow_expand=True``.**  Every node —
  including a song leaf with no children — therefore drew an expand chevron,
  and the labels drawn by this module added a second one on top of it.

``ListView`` fixes both: a ``ListItem`` is ``height: auto`` and each of its
children wraps to the width it is given, so a long title flows onto as many
lines as it needs and the list never scrolls sideways.

Layout
------
One ``ListItem`` per *visible* model node (children of a collapsed group are
flattened away).  Each item holds a fixed-width arrow cell and a flexible
label cell, so a wrapped title stays aligned under the text rather than
running back under the indent::

    ▼ 🔄 💿 Artist - Album Title
      ▶ ⏳ 🎵 Another Artist - A Very Long Song
              Title That Wraps

Only nodes with children get an arrow; leaves render two spaces so the
columns stay aligned.

Sync model
----------
``TaskTree`` is the source of truth and is mutated from the ripper's
coroutines (``rip.py`` / ``mv.py``), so the panel mirrors it on a timer.
Each tick:

* new model nodes are inserted at the model's position (groups go to the top,
  matching ``TaskTree``'s "newest first" ordering);
* changed labels are updated in place;
* nodes pruned from the model (``cl``) are removed;
* children of a collapsed node are not mounted at all.

Label diffing keeps the highlight and scroll position stable — only
genuinely changed nodes are touched, and the highlighted node is restored by
id across reordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from rich.text import Text
from textual.widgets import ListItem, ListView, Static

from src.tui.style import rich_style
from src.tui.task_tree import NodeStatus

if TYPE_CHECKING:
    from src.tui.task_tree import TaskTree, TreeNode as MTreeNode

# Sidebar width bounds (the initial width lives in TUI_CSS).
SIDEBAR_WIDTH = 52
SIDEBAR_MIN = 20
SIDEBAR_MAX = 90


def _fmt_bytes(n: int) -> str:
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f}MB"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.0f}kB"
    return f"{n}B"


@dataclass
class _Row:
    """A mounted list row plus the state used to diff it against the model."""

    item: ListItem
    arrow: Static
    label: Static
    mnode: "MTreeNode"
    depth: int
    fingerprint: tuple


class TaskPanel(ListView):
    """Wrapping task list: one row per visible ``TaskTree`` node."""

    def __init__(self, task_tree: "TaskTree", **kwargs) -> None:
        super().__init__(**kwargs)
        self._model = task_tree
        # model node_id → row
        self._rows: dict[str, _Row] = {}
        # node_ids in display order (mirrors the mounted children)
        self._order: list[str] = []

    def on_mount(self) -> None:
        # Show anything registered before the panel mounted; the app's tick
        # keeps it in sync from here on.
        self.sync()

    # ------------------------------------------------------------------ #
    # Mirroring TaskTree
    # ------------------------------------------------------------------ #

    def sync(self) -> None:
        """Bring the list in line with the current ``TaskTree`` snapshot."""
        flat: list[tuple[MTreeNode, int]] = []
        _flatten(self._model.snapshot(), 0, flat)

        # Remember which node the cursor sits on: rows may be inserted above
        # it (a new group lands on top) or removed underneath it.
        cursor_node = self._highlighted_node_id()

        seen: set[str] = set()
        self._order = []
        for index, (mnode, depth) in enumerate(flat):
            node_id = mnode.node_id
            seen.add(node_id)
            self._order.append(node_id)

            row = self._rows.get(node_id)
            if row is None:
                row = self._create_row(mnode, depth)
                self._rows[node_id] = row
                self._mount_row(index, row.item)
                continue

            row.mnode = mnode
            if row.depth != depth:
                row.depth = depth
                self._indent(row)
            fingerprint = self._fingerprint(mnode)
            if row.fingerprint != fingerprint:
                row.fingerprint = fingerprint
                self._render_row(row)

        # Drop rows the model no longer knows about (e.g. after `cl`), and any
        # node that just fell out of view because its parent collapsed.
        for node_id in [n for n in self._rows if n not in seen]:
            self._rows.pop(node_id).item.remove()

        self._restore_cursor(cursor_node)

    def _create_row(self, mnode: "MTreeNode", depth: int) -> _Row:
        arrow = Static(self._arrow(mnode), classes="arrow", expand=True)
        label = Static(self._label(mnode, depth), classes="label", expand=True)
        item = ListItem(arrow, label)
        row = _Row(item, arrow, label, mnode, depth, self._fingerprint(mnode))
        self._indent(row)
        return row

    def _mount_row(self, index: int, item: ListItem) -> None:
        """Insert *item* at *index*, appending when it belongs at the end."""
        if index < len(self.children):
            self.insert(index, [item])
        else:
            self.append(item)

    def _indent(self, row: _Row) -> None:
        row.item.styles.padding = (0, 0, 0, row.depth * 2)

    def _render_row(self, row: _Row) -> None:
        row.arrow.update(self._arrow(row.mnode))
        row.label.update(self._label(row.mnode, row.depth))

    # ------------------------------------------------------------------ #
    # Cursor
    # ------------------------------------------------------------------ #

    def _highlighted_node_id(self) -> Optional[str]:
        index = self.index
        if index is None or not (0 <= index < len(self.children)):
            return None
        item = self.children[index]
        for node_id, row in self._rows.items():
            if row.item is item:
                return node_id
        return None

    def highlighted_node(self) -> "Optional[MTreeNode]":
        """The model node under the cursor, if any."""
        node_id = self._highlighted_node_id()
        if node_id is None:
            return None
        row = self._rows.get(node_id)
        return row.mnode if row is not None else None

    def _restore_cursor(self, node_id: Optional[str]) -> None:
        if node_id is not None and node_id in self._order:
            self.index = self._order.index(node_id)
        elif self.index is not None and self.index >= len(self.children):
            self.index = len(self.children) - 1 if self.children else None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter / click on a group folds it; on a leaf it does nothing."""
        event.stop()
        index = event.index
        if index is None or not (0 <= index < len(self._order)):
            return
        mnode = self._rows[self._order[index]].mnode
        if mnode.children:
            self._model.toggle_expand(mnode.node_id)
            self.sync()

    # ------------------------------------------------------------------ #
    # Labels
    # ------------------------------------------------------------------ #

    def _fingerprint(self, mnode: "MTreeNode") -> tuple:
        """Cheap key reflecting everything that is rendered into the row.

        ``counts()`` is included because a group row prints its own finished /
        total track count: without it a group whose children changed state
        would keep showing a stale ``8/12``, since nothing about the group
        node itself moved.
        """
        status = mnode.status
        done, failed, total = mnode.breakdown()
        return (
            mnode.resolved_name,
            status,
            mnode.downloaded_bytes,
            mnode.decrypted_bytes,
            mnode.total_bytes,
            done,
            failed,
            total,
            str(mnode.error)[:12] if status == NodeStatus.FAILED else "",
            len(mnode.children),
            mnode.expanded,
        )

    def _arrow(self, mnode: "MTreeNode") -> Text:
        """Expand indicator, or blank padding for a node that cannot expand."""
        if not mnode.children:
            return Text("  ")
        glyph = "▼ " if mnode.expanded else "▶ "
        return Text(glyph, style=rich_style("task.heading"))

    def _label(self, mnode: "MTreeNode", depth: int) -> Text:
        status = mnode.status
        text = Text()

        text.append(status.icon_padded() + " ", style=rich_style(status.style_class()))
        text.append(mnode.kind_icon() + " ", style=rich_style(mnode.kind_style()))

        name_style = mnode.kind_style() if depth == 0 else "class:task.kind.song"
        text.append(mnode.resolved_name, style=rich_style(name_style))

        suffix = self._progress_suffix(mnode, status)
        if suffix is not None:
            body, token = suffix
            text.append("  " + body, style=rich_style(token))

        return text

    def _progress_suffix(
        self, mnode: "MTreeNode", status: NodeStatus
    ) -> Optional[tuple[str, str]]:
        """Progress text for a row, or ``None`` when there is nothing to add.

        Two measures, because they answer different questions:

        * a **group** counts tracks (``8/12``) — that is what someone waiting
          on an album is actually counting, and it stays meaningful even when
          a track's size never becomes known;
        * a **track** counts percent (``45%``), falling back to a plain byte
          count while the stream size is still unknown.

        Both are deliberately terse.  The sidebar is 35-50 columns wide and a
        long title already wraps; spelling out ``2.8MB/6.2MB``, or appending a
        percentage to the track count, pushed the suffix onto a line of its
        own and made every running row twice as tall.  The absolute figures
        live in the status bar and in the ``status`` command instead.
        """
        if status == NodeStatus.FAILED:
            if mnode.error:
                # Short on purpose: the full text (and the traceback context)
                # is in the log pane and in the error toast.
                return f"ERR:{str(mnode.error)[:12]}", "class:task.error"
            return None

        if mnode.children:
            done, failed, total = mnode.breakdown()
            if not total:
                return None
            if status == NodeStatus.RUNNING:
                return f"{done}/{total}", "class:task.progress"
            # A finished group folds to one line, and the row's own status
            # icon only says "some failed".  Spell out how many, or the count
            # is the same whether everything worked or half the album broke.
            if failed:
                # Any failure is worth colouring, whether it took the whole
                # album down or one track.
                return f"{done}/{total} ⚠{failed}", "class:task.error"
            return None

        if status != NodeStatus.RUNNING:
            return None

        parts: list[str] = []
        progress = mnode.progress
        if progress is not None:
            parts.append(f"{int(progress * 100)}%")
        elif mnode.downloaded_bytes:
            parts.append(f"{_fmt_bytes(mnode.downloaded_bytes)} ⬇")
        if mnode.decrypted_bytes:
            parts.append(f"🔓 {_fmt_bytes(mnode.decrypted_bytes)}")

        return (" ".join(parts), "class:task.progress") if parts else None


def _flatten(
    nodes: list["MTreeNode"],
    depth: int,
    out: list[tuple["MTreeNode", int]],
) -> None:
    """Depth-first walk of the *visible* nodes (collapsed groups are skipped)."""
    for mnode in nodes:
        out.append((mnode, depth))
        if mnode.children and mnode.expanded:
            _flatten(mnode.children, depth + 1, out)
