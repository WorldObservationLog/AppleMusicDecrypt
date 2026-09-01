"""Task-list widget — tree-structured sidebar.

Reads from ``TaskTree.snapshot()`` on every render tick (driven by
``Application(refresh_interval=0.5)`` plus explicit ``app.invalidate()``
calls from status changes) and emits a ``StyleAndTextTuples`` list that
prompt_toolkit renders inside a ``ScrollablePane``.

Tree chrome
-----------
  ▼ 💿  Album Name                   ← collapsed with ▶
    ├─ ▶  Artist - Song A  4.2MB ↓
    ├─ ✓  Artist - Song B
    └─ ✗  Artist - Song C  ERR: …

Scroll
------
Alt+↑ / Alt+↓ scroll the sidebar independently of the log pane.
The sidebar never receives keyboard focus (no Tab stop).
"""

from __future__ import annotations

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout import ScrollablePane
from prompt_toolkit.layout.dimension import Dimension as D

from src.tui.task_tree import TaskTree, TreeNode, NodeKind, NodeStatus

# Column width reserved for the sidebar (set in layout.py via preferred).
SIDEBAR_WIDTH = 32


def _fmt_bytes(n: int) -> str:
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f}MB"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.0f}kB"
    return f"{n}B"


class TaskListWidget:
    """Scrollable tree-view of all registered TaskTree nodes."""

    def __init__(self, tree: TaskTree) -> None:
        self._tree       = tree
        self._scroll_pos = 0   # handled by ScrollablePane

        self._inner_control = FormattedTextControl(
            text=self._render,
            focusable=False,
            show_cursor=False,
        )
        # Inner Window: height = len(rendered lines); ScrollablePane clips it.
        self._inner_window = Window(
            content=self._inner_control,
            wrap_lines=False,
            dont_extend_width=False,
        )
        # ScrollablePane lets Alt+↑/↓ scroll without focus.
        self.pane = ScrollablePane(
            content=self._inner_window,
            show_scrollbar=True,
        )

    # ------------------------------------------------------------------ #
    # Renderer
    # ------------------------------------------------------------------ #

    def _render(self) -> StyleAndTextTuples:
        roots = self._tree.snapshot()
        if not roots:
            return [("log.text", "  (no tasks)\n")]

        out: StyleAndTextTuples = []
        for node in roots:
            self._render_node(node, out, depth=0, last=True)
        return out

    def _render_node(
        self,
        node: TreeNode,
        out:  StyleAndTextTuples,
        depth: int,
        last: bool,
    ) -> None:
        status = node.status

        # ── indentation & tree branch ────────────────────────────────────
        if depth == 0:
            indent = ""
        else:
            connector = "└─ " if last else "├─ "
            indent = "  " * (depth - 1) + connector

        # ── expand / collapse chevron (parent nodes only) ────────────────
        if node.children:
            chevron = "▼ " if node.expanded else "▶ "
        else:
            chevron = ""

        # ── status icon ──────────────────────────────────────────────────
        icon       = status.icon()
        icon_style = status.style_class()

        # ── name (truncate to fit sidebar) ───────────────────────────────
        prefix_len   = len(indent) + len(chevron) + 2   # icon + space
        max_name     = max(8, SIDEBAR_WIDTH - prefix_len - 1)
        name         = node.display_name
        if len(name) > max_name:
            name = name[: max_name - 1] + "…"

        name_style = node.kind_style() if depth == 0 else "task.kind.song"

        # ── progress / error suffix ───────────────────────────────────────
        suffix_parts: list[str] = []
        if status == NodeStatus.RUNNING:
            dl  = node.downloaded_bytes
            dec = node.decrypted_bytes
            if dl:
                suffix_parts.append(f"{_fmt_bytes(dl)}↓")
            if dec:
                suffix_parts.append(f"{_fmt_bytes(dec)}🔓")
        elif status == NodeStatus.FAILED and node.error:
            err = str(node.error)[:18]
            suffix_parts.append(f"ERR:{err}")

        # ── assemble line ─────────────────────────────────────────────────
        if indent:
            out.append(("task.tree", indent))
        if chevron:
            out.append(("task.heading", chevron))
        out.append((icon_style, icon + " "))
        out.append((name_style, name))

        if suffix_parts:
            suffix = "  " + " ".join(suffix_parts)
            style  = "task.error" if status == NodeStatus.FAILED else "task.progress"
            out.append((style, suffix))

        out.append(("", "\n"))

        # ── children ─────────────────────────────────────────────────────
        if node.children and node.expanded:
            for i, child in enumerate(node.children):
                self._render_node(
                    child, out,
                    depth=depth + 1,
                    last=(i == len(node.children) - 1),
                )
