"""Task-tree data model for the TUI task panel.

Hierarchy
---------
  TreeNode (ALBUM / PLAYLIST / ARTIST)
    └─ TreeNode (SONG)        ← wraps a src.task.Task
  TreeNode (MV)               ← standalone, no children
  TreeNode (SONG)             ← standalone (direct dl <song-url>)

``TaskTree`` (creart singleton, registered in ``main.py``) is the single
source of truth for the task panel.  ``rip.py`` / ``mv.py`` call the
registration helpers (wrapped in try/except so the pipeline works without
the TUI); the widget reads ``TaskTree.snapshot()`` on every render tick
and leaf names resolve live through ``TreeNode.resolved_name`` so the
sidebar picks up real song titles once metadata arrives.

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

from rich.cells import cell_len

from src.tui import glyphs

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
        return glyphs.status_icon(self.name)

    def icon_padded(self) -> str:
        """Status icon padded to a fixed 2-cell display width so the
        sidebar columns line up regardless of glyph advance width."""
        return _pad_to_width(self.icon(), 2)

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

    # Whether a fully-finished group may be folded automatically.  Cleared
    # once the user expands the group by hand, so that a deliberate look at a
    # finished album is not undone on the next frame.
    auto_collapse: bool            = True

    # Set when node first reaches a terminal state (for ``cl`` pruning).
    finished_at:  Optional[float]  = None

    # Manually set for MV / standalone nodes; derived for groups.
    _status:      Optional[NodeStatus] = field(default=None, repr=False)

    # ------------------------------------------------------------------ #
    # Status (read)
    # ------------------------------------------------------------------ #

    @property
    def status(self) -> NodeStatus:
        # Pure read: this used to set ``expanded = False`` for finished
        # groups, which meant a manual expand was reverted by the very next
        # render — the group could never be opened.  The collapse policy now
        # lives in ``TaskTree.collapse_finished``, which runs once per tick.
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
    def total_bytes(self) -> int:
        """Expected size of the media stream (sum over children for a group)."""
        if self.task:
            return self.task.total_bytes
        return sum(c.total_bytes for c in self.children)

    @property
    def progress(self) -> Optional[float]:
        """Download progress as a fraction in ``[0, 1]``, or ``None``.

        ``None`` rather than 0.0 when something below this node has no known
        size: a group whose remaining tracks have not started downloading yet
        would otherwise report a misleadingly high percentage.
        """
        if not self.children:
            return self.task.progress if self.task else None
        if self.status in (NodeStatus.DONE, NodeStatus.EXIST):
            # Finished groups are complete by definition, even if the size of
            # some member never became known.
            return 1.0
        totals = [c.total_bytes for c in self.children]
        if not totals or any(t <= 0 for t in totals):
            return None
        return min(1.0, self.downloaded_bytes / sum(totals))

    def counts(self) -> "tuple[int, int]":
        """``(settled, total)`` leaf count below this node.

        "Settled" means terminal — done, already existed *or* failed.  This is
        the progress measure: a track that failed is still a track that will
        not run again, so a finished album reaches 100% even if some of it
        failed.
        """
        if not self.children:
            return (1 if self.status.is_terminal() else 0), 1
        done = total = 0
        for child in self.children:
            child_done, child_total = child.counts()
            done += child_done
            total += child_total
        return done, total

    def breakdown(self) -> "tuple[int, int, int]":
        """``(succeeded, failed, total)`` leaf counts below this node.

        Deliberately *not* what :meth:`counts` reports: a group row reads
        ``2/4 ⚠2`` — two tracks succeeded out of four, two of them failed —
        and ``4/4 ⚠2`` would say the same thing twice while looking like a
        contradiction.  The row's own status icon only says "some failed", and
        a finished group folds to one line the user has no reason to open.
        """
        if not self.children:
            status = self.status
            succeeded = 1 if status in (NodeStatus.DONE, NodeStatus.EXIST) else 0
            failed = 1 if status == NodeStatus.FAILED else 0
            return succeeded, failed, 1
        succeeded = failed = total = 0
        for child in self.children:
            child_ok, child_failed, child_total = child.breakdown()
            succeeded += child_ok
            failed += child_failed
            total += child_total
        return succeeded, failed, total

    def size_gap(self) -> "tuple[int, int]":
        """``(leaves with a known size, unsettled leaves still without one)``.

        The second number is the ETA's blind spot: at the start of an album
        only the first few tracks have been through the m3u8, so
        ``TreeStats.expected`` covers a fraction of the real total.  Measured
        on a 14-track album, the first sample knew about 102 MB of what turned
        out to be 388 MB.
        """
        if not self.children:
            if self.total_bytes > 0:
                return 1, 0
            # A settled track will not need a size any more.
            return (0, 0) if self.status.is_terminal() else (0, 1)
        known = gap = 0
        for child in self.children:
            child_known, child_gap = child.size_gap()
            known += child_known
            gap += child_gap
        return known, gap

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
        return glyphs.kind_icon(self.kind.name)

    def kind_style(self) -> str:
        return {
            NodeKind.ALBUM:    "class:task.kind.album",
            NodeKind.PLAYLIST: "class:task.kind.playlist",
            NodeKind.ARTIST:   "class:task.kind.artist",
            NodeKind.MV:       "class:task.kind.mv",
            NodeKind.SONG:     "class:task.kind.song",
        }[self.kind]


def _display_width(s: str) -> int:
    """Display width of *s* in terminal cells.

    Delegates to Rich, which is the same measurement the renderer itself uses.
    The previous hand-rolled version counted the whole ``0x2600-0x27BF`` block
    as double-width, but ``✔`` (U+2714, ALREADY_EXIST) and ``⚠`` (U+26A0,
    PARTIAL) occupy a single cell — so those two rows came out one column
    short and their labels did not line up with the rest of the list.
    """
    return cell_len(s)


def _pad_to_width(s: str, width: int) -> str:
    """Right-pad *s* with spaces so its display width == *width*."""
    return s + " " * max(0, width - cell_len(s))


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

@dataclass
class TreeStats:
    """Aggregate view of the whole tree, for the status bar.

    Two independent progress notions live here on purpose:

    * ``done``/``total`` count *tracks that have settled* — done, already
      existed or failed — which is what a user waiting on an album is actually
      counting, and which reaches 100% even when part of the batch failed;
    * ``downloaded``/``expected`` count *bytes*, which is what the ETA needs.

    ``expected`` only covers the tracks whose size is known so far, so
    ``known`` and ``unknown`` record how complete that picture is and
    :attr:`remaining_bytes` extrapolates from it.
    """

    done: int = 0
    total: int = 0
    downloaded: int = 0
    expected: int = 0
    # Leaves whose size arrived, and unsettled leaves still waiting for one.
    known: int = 0
    unknown: int = 0
    # Tracks that failed.
    failed: int = 0

    @property
    def track_percent(self) -> Optional[float]:
        """Fraction of tracks settled, or ``None`` when the tree is empty."""
        if not self.total:
            return None
        return self.done / self.total

    @property
    def average_track_bytes(self) -> int:
        """Mean size of the tracks that have reported one (0 when none has)."""
        if self.known <= 0:
            return 0
        return self.expected // self.known

    @property
    def remaining_bytes(self) -> int:
        """Bytes still to fetch, extrapolated for tracks that have no size yet.

        Without the extrapolation the ETA is optimistic exactly when it is most
        read — the first seconds of a large album.
        """
        outstanding = max(0, self.expected - self.downloaded)
        return outstanding + self.unknown * self.average_track_bytes


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
        self._roots.insert(0, node)   # newest group on top
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
            self._roots.insert(0, node)   # newest song on top
        return node

    def register_mv(
        self,
        mv_id:        str,
        display_name: str,
        parent_id:    str = "",
    ) -> TreeNode:
        """Register a music-video node (no children, progress tracked via _status),
        optionally under a parent group.  Re-registering updates the name."""
        if mv_id in self._by_id:
            node = self._by_id[mv_id]
            node.display_name = display_name
            return node
        node = TreeNode(
            node_id=mv_id,
            kind=NodeKind.MV,
            display_name=display_name,
            _status=NodeStatus.WAITING,
        )
        self._by_id[mv_id] = node
        if parent_id and parent_id in self._by_id:
            self._by_id[parent_id].children.append(node)
        else:
            self._roots.insert(0, node)   # newest MV on top
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

    def stats(self) -> TreeStats:
        """Aggregate track and byte counters over the whole tree."""
        stats = TreeStats()
        for root in self._roots:
            done, total = root.counts()
            stats.done += done
            stats.total += total
            stats.downloaded += root.downloaded_bytes
            stats.expected += root.total_bytes
            known, gap = root.size_gap()
            stats.known += known
            stats.unknown += gap
            stats.failed += root.breakdown()[1]
        return stats

    def clear_done(self) -> int:
        """Remove all terminal nodes (and their children).  Returns count removed."""
        before = len(self._roots)
        self._roots = [n for n in self._roots if not n.status.is_terminal()]
        # Rebuild lookup (simpler than surgical removal).
        self._reindex()
        return before - len(self._roots)

    def remove(self, node_id: str) -> bool:
        """Detach one node (and everything below it).  Returns whether it existed.

        Used by the sidebar's delete key.  Removal is purely cosmetic — it
        edits the view model, not the download — so callers must check that
        the node is finished before offering it.
        """
        node = self._by_id.get(node_id)
        if node is None:
            return False
        parent = self._parent_of(node_id)
        if parent is None:
            self._roots = [n for n in self._roots if n.node_id != node_id]
        else:
            parent.children = [c for c in parent.children if c.node_id != node_id]
        self._reindex()
        return True

    def _parent_of(self, node_id: str) -> Optional[TreeNode]:
        for node in self._by_id.values():
            if any(child.node_id == node_id for child in node.children):
                return node
        return None

    def _reindex(self) -> None:
        self._by_id = {}
        for root in self._roots:
            self._index(root)

    def set_all_expanded(self, expanded: bool) -> int:
        """Open or fold every group.  Returns how many groups were touched.

        Expanding by hand also clears ``auto_collapse``: "expand all" is an
        explicit instruction, and having finished groups snap shut again on
        the next tick would make the key look broken.
        """
        touched = 0
        for node in self._by_id.values():
            if not node.children:
                continue
            node.expanded = expanded
            if expanded:
                node.auto_collapse = False
            touched += 1
        return touched

    def _index(self, node: TreeNode) -> None:
        self._by_id[node.node_id] = node
        for child in node.children:
            self._index(child)

    def toggle_expand(self, node_id: str) -> None:
        node = self._by_id.get(node_id)
        if node and node.children:
            node.expanded = not node.expanded
            # Opening a group by hand is a deliberate act: opt it out of the
            # automatic fold so the next tick does not shut it again.
            if node.expanded:
                node.auto_collapse = False

    def collapse_finished(self) -> int:
        """Fold every fully-finished group the user has not opened.

        Returns how many groups were folded.  This is deliberately a method
        driven by the UI tick instead of a side effect inside
        :attr:`TreeNode.status`: state changes then happen at one explicit
        point per frame, and a group the user expanded stays open.
        """
        folded = 0
        for node in self._by_id.values():
            if not node.children or not node.expanded or not node.auto_collapse:
                continue
            if all(child.status.is_terminal() for child in node.children):
                node.expanded = False
                folded += 1
        return folded


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
