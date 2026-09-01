"""Task-tree data model for the TUI task panel.

Hierarchy
---------
  TreeNode (ALBUM / PLAYLIST / ARTIST)
    └─ TreeNode (SONG)        ← wraps a src.task.Task
  TreeNode (MV)               ← standalone, no children
  TreeNode (SONG)             ← standalone (direct dl <song-url>)

The ``TaskTree`` singleton (registered with creart) is the single source of
truth for the task panel.  ``rip.py`` / ``mv.py`` call the small registration
helpers; the TUI widget reads ``TaskTree.snapshot()`` on every render tick.

Node lifecycle
--------------
  register_group()  – called by rip_album/rip_playlist/rip_artist at start
  register_song()   – called by rip_song after Task is created
  finish_node()     – called when a node reaches a terminal status
  clear_done()      – called by the ``cl`` command to prune terminal nodes
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.task import Task


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class NodeKind(Enum):
    SONG     = auto()
    ALBUM    = auto()
    PLAYLIST = auto()
    ARTIST   = auto()
    MV       = auto()


class NodeStatus(Enum):
    WAITING     = auto()
    RUNNING     = auto()
    DONE        = auto()
    EXIST       = auto()   # ALREADY_EXIST
    FAILED      = auto()
    PARTIAL     = auto()   # some children failed

    def is_terminal(self) -> bool:
        return self in (NodeStatus.DONE, NodeStatus.EXIST, NodeStatus.FAILED,
                        NodeStatus.PARTIAL)

    def icon(self) -> str:
        return {
            NodeStatus.WAITING:  "..",
            NodeStatus.RUNNING:  ">>",
            NodeStatus.DONE:     "ok",
            NodeStatus.EXIST:    "ok",
            NodeStatus.FAILED:   "XX",
            NodeStatus.PARTIAL:  "!!",
        }[self]

    def style_class(self) -> str:
        return {
            NodeStatus.WAITING:  "class:task.icon.wait",
            NodeStatus.RUNNING:  "class:task.icon.run",
            NodeStatus.DONE:     "class:task.icon.done",
            NodeStatus.EXIST:    "class:task.icon.exist",
            NodeStatus.FAILED:   "class:task.icon.fail",
            NodeStatus.PARTIAL:  "class:task.icon.fail",
        }[self]


# ---------------------------------------------------------------------------
# TreeNode
# ---------------------------------------------------------------------------

@dataclass
class TreeNode:
    node_id:      str
    kind:         NodeKind
    display_name: str

    # Leaf nodes bind to a Task; parent nodes derive status from children.
    task:         Optional["Task"] = field(default=None, repr=False)

    children:     list["TreeNode"] = field(default_factory=list, repr=False)
    expanded:     bool             = True

    # Set when node first reaches a terminal state (for ``cl`` pruning).
    finished_at:  Optional[float]  = None

    # Manually set for MV / standalone nodes; derived for groups.
    _status:      Optional[NodeStatus] = field(default=None, repr=False)

    # ------------------------------------------------------------------ #
    # Status (read)
    # ------------------------------------------------------------------ #

    @property
    def status(self) -> NodeStatus:
        # Leaf or MV: read from Task or _status.
        if not self.children:
            if self._status is not None:
                return self._status
            return _task_to_node_status(self.task)

        # Parent: aggregate from children.
        statuses = [c.status for c in self.children]
        if any(s == NodeStatus.RUNNING  for s in statuses):
            return NodeStatus.RUNNING
        if all(s in (NodeStatus.DONE, NodeStatus.EXIST) for s in statuses):
            return NodeStatus.DONE
        if all(s.is_terminal() for s in statuses):
            return NodeStatus.PARTIAL if any(
                s == NodeStatus.FAILED for s in statuses) else NodeStatus.DONE
        return NodeStatus.WAITING

    @status.setter
    def status(self, value: NodeStatus) -> None:
        self._status = value

    # ------------------------------------------------------------------ #
    # Progress (leaves only)
    # ------------------------------------------------------------------ #

    @property
    def downloaded_bytes(self) -> int:
        if self.task:
            return self.task.downloaded_bytes
        return sum(c.downloaded_bytes for c in self.children)

    @property
    def decrypted_bytes(self) -> int:
        if self.task:
            return self.task.decrypted_bytes
        return sum(c.decrypted_bytes for c in self.children)

    @property
    def error(self):
        if self.task:
            return self.task.error
        return None

    @property
    def resolved_name(self) -> str:
        """Live display name.  Leaf nodes re-read Task.display_name so the
        sidebar picks up the real song title once metadata arrives (the
        initial register happens before metadata is fetched, when the name
        would still be the adam ID)."""
        if self.task is not None:
            return self.task.display_name
        return self.display_name

    # ------------------------------------------------------------------ #
    # Kind helpers
    # ------------------------------------------------------------------ #

    def kind_icon(self) -> str:
        return {
            NodeKind.ALBUM:    "[ALB]",
            NodeKind.PLAYLIST: "[PLS]",
            NodeKind.ARTIST:   "[ART]",
            NodeKind.MV:       "[MV] ",
            NodeKind.SONG:     "     ",
        }[self.kind]

    def kind_style(self) -> str:
        return {
            NodeKind.ALBUM:    "class:task.kind.album",
            NodeKind.PLAYLIST: "class:task.kind.playlist",
            NodeKind.ARTIST:   "class:task.kind.artist",
            NodeKind.MV:       "class:task.kind.mv",
            NodeKind.SONG:     "class:task.kind.song",
        }[self.kind]


def _task_to_node_status(task: Optional["Task"]) -> NodeStatus:
    if task is None:
        return NodeStatus.WAITING
    from src.task import Status
    return {
        Status.WAITING:       NodeStatus.WAITING,
        Status.PARSING:       NodeStatus.RUNNING,
        Status.DOWNLOADING:   NodeStatus.RUNNING,
        Status.DECRYPTING:    NodeStatus.RUNNING,
        Status.SAVING:        NodeStatus.RUNNING,
        Status.DONE:          NodeStatus.DONE,
        Status.ALREADY_EXIST: NodeStatus.EXIST,
        Status.FAILED:        NodeStatus.FAILED,
    }.get(task.status, NodeStatus.WAITING)


# ---------------------------------------------------------------------------
# TaskTree singleton
# ---------------------------------------------------------------------------

class TaskTree:
    """Registry and snapshot source for the TUI task panel."""

    def __init__(self) -> None:
        # Insertion-ordered list of root-level nodes.
        self._roots:    list[TreeNode]        = []
        # Fast lookup: node_id → TreeNode (all levels).
        self._by_id:    dict[str, TreeNode]   = {}

    # ------------------------------------------------------------------ #
    # Registration (called from rip.py / mv.py)
    # ------------------------------------------------------------------ #

    def register_group(
        self,
        node_id:      str,
        kind:         NodeKind,
        display_name: str,
    ) -> TreeNode:
        """Register an album / playlist / artist parent node."""
        if node_id in self._by_id:
            return self._by_id[node_id]
        node = TreeNode(node_id=node_id, kind=kind, display_name=display_name)
        self._roots.append(node)
        self._by_id[node_id] = node
        return node

    def register_song(
        self,
        song_id:       str,
        display_name:  str,
        task:          "Task",
        parent_id:     str = "",
    ) -> TreeNode:
        """Register a song leaf node, optionally under a parent group."""
        if song_id in self._by_id:
            node = self._by_id[song_id]
            node.task = task
            return node
        node = TreeNode(
            node_id=song_id,
            kind=NodeKind.SONG,
            display_name=display_name,
            task=task,
        )
        self._by_id[song_id] = node
        if parent_id and parent_id in self._by_id:
            self._by_id[parent_id].children.append(node)
        else:
            self._roots.append(node)
        return node

    def register_mv(
        self,
        mv_id:        str,
        display_name: str,
    ) -> TreeNode:
        """Register a music-video node (no children, progress tracked via _status)."""
        if mv_id in self._by_id:
            return self._by_id[mv_id]
        node = TreeNode(
            node_id=mv_id,
            kind=NodeKind.MV,
            display_name=display_name,
            _status=NodeStatus.WAITING,
        )
        self._roots.append(node)
        self._by_id[mv_id] = node
        return node

    def update_mv_status(self, mv_id: str, status: NodeStatus,
                         downloaded_bytes: int = 0) -> None:
        node = self._by_id.get(mv_id)
        if node is None:
            return
        node.status = status
        if status.is_terminal():
            node.finished_at = time.monotonic()

    # ------------------------------------------------------------------ #
    # Query / maintenance
    # ------------------------------------------------------------------ #

    def snapshot(self) -> list[TreeNode]:
        """Return a shallow copy of the root list for the widget to render."""
        return list(self._roots)

    def clear_done(self) -> int:
        """Remove all terminal nodes (and their children).  Returns count removed."""
        before = len(self._roots)
        self._roots = [n for n in self._roots if not n.status.is_terminal()]
        # Rebuild lookup (simpler than surgical removal).
        self._by_id = {}
        for root in self._roots:
            self._index(root)
        return before - len(self._roots)

    def _index(self, node: TreeNode) -> None:
        self._by_id[node.node_id] = node
        for child in node.children:
            self._index(child)

    def toggle_expand(self, node_id: str) -> None:
        node = self._by_id.get(node_id)
        if node and node.children:
            node.expanded = not node.expanded


# ---------------------------------------------------------------------------
# creart integration
# ---------------------------------------------------------------------------

from typing import Type
from creart import AbstractCreator, CreateTargetInfo, exists_module


class TaskTreeCreator(AbstractCreator):
    targets = (CreateTargetInfo("src.tui.task_tree", "TaskTree"),)

    @staticmethod
    def available() -> bool:
        return exists_module("src.tui.task_tree")

    @staticmethod
    def create(create_type: Type[TaskTree]) -> TaskTree:
        return create_type()
