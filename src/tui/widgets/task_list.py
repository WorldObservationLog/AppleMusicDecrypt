"""Task-list widget — tree-structured sidebar.

Reads from ``TaskTree.snapshot()`` on every render tick (driven by
``Application(refresh_interval=0.5)`` plus explicit ``app.invalidate()``
calls from status changes) and emits a ``StyleAndTextTuples`` list that
prompt_toolkit renders inside a ``ScrollablePane``.

Tree chrome
-----------
  ▼ 💿 Album Name
      ├─ 🔄 Artist - Song A  4.2MB ⬇
      ├─ ✅ Artist - Song B
      └─ ❌ Artist - Song C  ERR:...

Scroll
------
The pane scrolls independently of the log pane (mouse wheel) and never
receives keyboard focus (no Tab stop).  Names are never truncated:
long lines soft-wrap.
"""

from __future__ import annotations

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout import ScrollablePane
from prompt_toolkit.layout.dimension import Dimension as D

from src.tui.task_tree import TaskTree, TreeNode, NodeKind, NodeStatus

# Column width reserved for the sidebar (set in layout.py via preferred).
# Preferred sidebar width. The layout gives it up to 45% of the screen
# width (preferred 52), so long artist - title lines are not clipped.
SIDEBAR_WIDTH = 52


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
            focusable=True,
            show_cursor=False,
        )
        # Click-to-focus for the sidebar (FormattedTextControl has no
        # built-in focus_on_click like BufferControl does).
        from prompt_toolkit.mouse_events import MouseEventType
        from prompt_toolkit.application.current import get_app as _get_app

        _orig_mouse = self._inner_control.mouse_handler

        def _mouse_handler(mouse_event):
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                if _get_app().layout.current_control != self._inner_control:
                    _get_app().layout.current_control = self._inner_control
                    return None
            elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                self.scroll(3)
                return None
            elif mouse_event.event_type == MouseEventType.SCROLL_UP:
                self.scroll(-3)
                return None
            return _orig_mouse(mouse_event)

        self._inner_control.mouse_handler = _mouse_handler
        # Inner Window: height = len(rendered lines); ScrollablePane clips it.
        self._inner_window = Window(
            content=self._inner_control,
            wrap_lines=True,   # long names wrap instead of being clipped
            dont_extend_width=False,
        )
        # ScrollablePane supports keyboard scrolling when its content is
        # focused; we also add explicit scroll() for mouse/keys below.
        self.pane = ScrollablePane(
            content=self._inner_window,
            show_scrollbar=True,
        )
        self._scroll_offset = 0

    def focusable_window(self):
        return self.pane

    def scroll(self, lines: int) -> None:
        """Scroll the sidebar by *lines* (negative = up)."""
        # Use the FULL (un-clipped) rendered lines as the scroll bound;
        # _render() applies _scroll_offset, so calling it here would shrink
        # the bound as we scroll and make up-scrolling impossible.
        total = max(0, len(self._full_render_lines()) - 1)
        self._scroll_offset = max(0, min(self._scroll_offset + lines, total))
        self._apply_scroll()

    def scroll_to_top(self) -> None:
        self._scroll_offset = 0
        self._apply_scroll()

    def _full_render_lines(self) -> list[str]:
        """Render without applying the scroll offset (for bounds)."""
        roots = self._tree.snapshot()
        if not roots:
            return []
        out: StyleAndTextTuples = []
        for node in roots:
            self._render_node(node, out, depth=0, last=True)
        return self._lines_from_fragments(out)

    def _render_lines(self) -> list[str]:
        frag = self._render()
        lines, buf = [], ""
        for _, txt in frag:
            buf += txt
            if txt.endswith("\n"):
                lines.append(buf.rstrip("\n"))
                buf = ""
        if buf:
            lines.append(buf)
        return lines

    def _apply_scroll(self) -> None:
        # ScrollablePane has no direct setter; emulate by adjusting the
        # inner window's height with a leading spacer is complex.  Instead
        # rely on prompt_toolkit's focus+up/down keys (bound in app.py via
        # event.app.layout focused window's built-in scroll).
        pass

    # ------------------------------------------------------------------ #
    # Renderer
    # ------------------------------------------------------------------ #

    def _render(self) -> StyleAndTextTuples:
        roots = self._tree.snapshot()
        if not roots:
            return [("class:log.text", "  (no tasks)\n")]

        out: StyleAndTextTuples = []
        for node in roots:
            self._render_node(node, out, depth=0, last=True)

        if self._scroll_offset:
            rendered = self._lines_from_fragments(out)
            out = self._fragments_from_lines(rendered[self._scroll_offset:])
        return out

    def _lines_from_fragments(self, frag: StyleAndTextTuples) -> list[str]:
        lines, buf = [], ""
        for _, txt in frag:
            buf += txt
            if txt.endswith("\n"):
                lines.append(buf.rstrip("\n"))
                buf = ""
        if buf:
            lines.append(buf)
        return lines

    def _fragments_from_lines(self, lines: list[str]) -> StyleAndTextTuples:
        result: StyleAndTextTuples = []
        for line in lines:
            result.append(("", line + "\n"))
        return result

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

        # ── status icon (padded so columns align) ────────────────────────
        icon       = status.icon_padded()
        icon_style = status.style_class()

        # ── name (full text; the Window wraps long lines) ────────────────
        name         = node.resolved_name

        name_style = node.kind_style() if depth == 0 else "class:task.kind.song"

        # ── progress / error suffix ───────────────────────────────────────
        suffix_parts: list[str] = []
        if status == NodeStatus.RUNNING:
            dl  = node.downloaded_bytes
            dec = node.decrypted_bytes
            if dl:
                suffix_parts.append(f"{_fmt_bytes(dl)} ⬇")
            if dec:
                suffix_parts.append(f"{_fmt_bytes(dec)} 🔓")
        elif status == NodeStatus.FAILED and node.error:
            err = str(node.error)[:18]
            suffix_parts.append(f"ERR:{err}")

        # ── assemble line ─────────────────────────────────────────────────
        if indent:
            out.append(("class:task.tree", indent))
        if chevron:
            out.append(("class:task.heading", chevron))
        out.append((icon_style, icon + " "))
        # Kind icon on every node (album/playlist/artist/MV/song).
        out.append((node.kind_style(), node.kind_icon() + " "))
        out.append((name_style, name))

        if suffix_parts:
            suffix = "  " + " ".join(suffix_parts)
            style  = "class:task.error" if status == NodeStatus.FAILED else "class:task.progress"
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
