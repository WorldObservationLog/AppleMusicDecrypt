"""TUI Application — entry point for the interactive interface.

``run_tui(shell)`` is the replacement for ``InteractiveShell.start()``.  It:

  1. Installs the log sink (redirects GlobalLogger / RipLogger into the deque).
  2. Binds the sink to the running event loop for thread-safe wake-ups.
  3. Builds the Textual app and runs it on the existing asyncio event loop.
  4. Tears down on exit (uninstalls the log sink, stops QEMU if needed).

Compared with the prompt_toolkit version, everything that used to be assembled
by hand is now declarative:

======================================  =====================================
before (prompt_toolkit)                 now (Textual)
======================================  =====================================
``Frame`` rebuilt every frame to show   ``:focus-within`` CSS rule
  the focus highlight
hand-written ``mouse_handler`` per      built-in click focus + ``FOCUS_ON_CLICK``
  widget for click-to-focus
manual Tab cycle across three windows   DOM order + ``action_focus_next``
``refresh_interval=0.5`` full redraw    event-driven log writes + a 0.5s tick
                                        that only touches the tree/status bar
``Float`` + ``ConditionalContainer``    ``ModalScreen`` pushed on demand
  permanently mounted for batch input
manual DummyInput/``/dev/tty`` probing  Textual's own drivers (failures surface
                                        as a clear error — see ``run_tui``)
======================================  =====================================

Command dispatch is still handled by ``InteractiveShell.command_parser()``;
this module only owns the UI shell.

Keybindings
-----------
Tab / Shift+Tab  cycle focus (log ⇄ task sidebar ⇄ input)
↑ / ↓            scroll log (log focused) / task list / command history (input)
End              resume log auto-follow after scrolling
Enter / click    fold or unfold a group in the task sidebar
F1               help panel (F1 again, Esc or q closes it)
F2               cycle the log level floor (everything → WARNING+ → ERROR+)
F3 or /          filter the log by substring; Esc clears it
F4               cycle the colour theme
F5 / F6          expand / fold every group in the sidebar
F7               copy the highlighted task's name
Delete           remove the highlighted task, once it has finished
Ctrl+P           command palette (every action, searchable)
F10 / Ctrl+C     exit (two-step confirm while tasks run)
Ctrl+D / Esc     submit / cancel the batch panel
Ctrl+← / Ctrl+→  resize the task sidebar

The sidebar actions use function keys rather than Ctrl+letters because the
focused widget already claims the useful ones (``ctrl+e`` is the input's
end-of-line, ``ctrl+y`` is the text area's redo); a binding that stops working
depending on focus is worse than a key that takes a moment to learn.

Log filtering
-------------
Two independent filters, applied to the backlog as well as to new lines, so
raising the floor replays history instead of losing it:

``F2``  a level floor — ``WARNING`` hides the routine chatter of a long batch;
``F3``  a substring, edited in a row above the command line.

Errors are also raised as notifications, so a failure that scrolls out of the
pane (or is filtered away) still surfaces.

Layout
------
The sidebar takes roughly a third of the terminal (bounded by
``SIDEBAR_MIN``/``SIDEBAR_WIDTH``) and the log pane gets the rest, but never
less than ``LOG_MIN_WIDTH`` columns — a narrow terminal shrinks the sidebar
first.  Both panes wrap their text instead of scrolling sideways; sizing the
sidebar by hand pins it, and later resizes only clamp it.

The status bar is assembled against the terminal width rather than handed to
the renderer as one long string: hints, progress and the speed sparkline drop
off in that order as the terminal narrows, and the hint block stays flush with
the right edge instead of being clipped.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from creart import it
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import Screen
from textual.widgets import Static

from src.config import Config
from src.measurer import Measurer
from src import quality
from src.tui import history, log_sink, settings, style
from src.tui.style import TUI_CSS
from src.tui.task_tree import TaskTree
from src.tui.win_console import disable_quick_edit, set_clipboard_text
from src.tui.widgets.batch_panel import BatchPanel
from src.tui.widgets.help_screen import HelpScreen
from src.tui.widgets.input_bar import CommandInput, CommandSuggester
from src.tui.widgets.log_view import LogPanel
from src.tui.widgets.prompt_screen import PromptScreen
from src.tui.widgets.quality_screen import QualityScreen
from src.tui.widgets.search_bar import SearchBar
from src.tui.widgets.status_bar import StatusBar
from src.tui.widgets.task_list import (
    SIDEBAR_MAX,
    SIDEBAR_MIN,
    SIDEBAR_WIDTH,
    TaskPanel,
)
from src.wrapper import WrapperClient

if TYPE_CHECKING:
    from src.cmd import InteractiveShell


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# The log pane never shrinks below this; the sidebar yields its columns first.
LOG_MIN_WIDTH = 24

# Below this terminal width there is no room for two panes at all: a 20-column
# sidebar beside a 24-column log is unusable, and the log is the pane that still
# says what is happening.  The sidebar yields completely instead.
SINGLE_PANE_WIDTH = 52

# SGR escape sequences; loguru colourises its output and a toast is plain text.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Leading "2026-01-01T00:00:00.000000+0000 | " timestamp of a formatted line.
_TIMESTAMP_RE = re.compile(r"^\S+\s*\|\s*")

# Longest toast body kept; a traceback-length line is useless in a popup.
_TOAST_MAX_CHARS = 160

# Idle time after the last keystroke before the log filter is replayed.
_FILTER_DEBOUNCE = 0.15

# Alert throttling.  A batch download that loses the network fails every track
# at once, and one toast per failure buries the interface — measured with eight
# errors: eight toasts stacked from the bottom edge to mid-screen, covering the
# log pane, the sidebar, the input row and the status bar.  Only the first few
# in each window are shown; the rest are counted and reported in the status
# bar, which covers nothing.
_ALERT_WINDOW = 5.0
_ALERT_MAX_PER_WINDOW = 3
# How long the "n suppressed" note lingers after a burst stops.
_ALERT_NOTE_LINGER = 10.0

# ``qa <album>`` asks the wrapper once per track, so a 14-track album produces
# 14 results within a few hundred milliseconds.  Collect them briefly and show
# one table instead of stacking 14 modal screens.
_QUALITY_COLLECT = 0.4

# Shown in the task sidebar until the first task is registered.
_EMPTY_TASKS = (
    "\n No tasks yet.\n\n"
    "  dl <url>     download a song,\n"
    "               album or playlist\n"
    "  dl -b        paste many URLs\n"
    "  qa <url>     inspect quality\n\n"
    " Press F1 for the full list.\n"
)

# Themes offered by the cycle key.  Both dark and light are included: the
# chrome follows Textual's theme variables, and ``style.set_palette`` switches
# the Rich-side colours (task rows, status bar, help text) to match, so a light
# theme is readable rather than dark-on-light.
THEMES: tuple[str, ...] = (
    "catppuccin-mocha",
    "textual-dark",
    "nord",
    "tokyo-night",
    "gruvbox",
    "catppuccin-latte",
    "textual-light",
    "rose-pine-dawn",
)


def _get_regions() -> list[str]:
    """Return the cached region list from WrapperClient (never blocks)."""
    try:
        return list(getattr(it(WrapperClient), "_last_regions", None) or [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class DecryptTUI(App):
    """Full-screen TUI for AppleMusicDecrypt."""

    CSS = TUI_CSS
    TITLE = "AppleMusicDecrypt"
    # The command palette is on by default and is genuinely useful here: it
    # makes every action searchable (the application's own commands are
    # appended in get_system_commands below) without another key to remember.
    # It answers Ctrl+P, which nothing else claims.
    ENABLE_COMMAND_PALETTE = True

    BINDINGS = [
        Binding("tab", "focus_next", "Focus", show=False),
        Binding("shift+tab", "focus_previous", "Focus", show=False),
        Binding("f1", "help", "Help"),
        Binding("f2", "cycle_log_level", "Log level"),
        Binding("f3", "search_log", "Filter log"),
        # ``/`` is the familiar "search" key, but it only reaches the app when
        # the command line does not have focus — an Input consumes the
        # character.  F3 is the version that always works.
        Binding("slash", "search_log", "Filter log", show=False),
        Binding("f4", "cycle_theme", "Theme", show=False),
        Binding("f10", "confirm_exit", "Exit"),
        Binding("ctrl+c", "confirm_exit", "Exit", show=False),
        Binding("ctrl+q", "confirm_exit", "Exit", show=False),
        Binding("ctrl+left", "narrow_sidebar", "Narrow", show=False),
        Binding("ctrl+right", "widen_sidebar", "Wider", show=False),
        # The command line already binds Ctrl+Left/Right to word-wise cursor
        # movement, so those only reach the app when focus is elsewhere.  Alt
        # works from anywhere.
        Binding("alt+left", "narrow_sidebar", "Narrow", show=False),
        Binding("alt+right", "widen_sidebar", "Wider", show=False),
        # Sidebar actions use function keys: every Ctrl+letter worth having is
        # already claimed by the focused widget (``ctrl+e`` is Input's
        # end-of-line, ``ctrl+y`` is TextArea's redo), and a binding that
        # silently stops working depending on focus is worse than a key that
        # needs a moment's remembering.  ``delete`` is the exception — it does
        # exactly what the user means in both contexts.
        Binding("f5", "expand_all_tasks", "Expand all tasks", show=False),
        Binding("f6", "collapse_all_tasks", "Fold all tasks", show=False),
        Binding("f7", "copy_task_name", "Copy task name", show=False),
        Binding("f8", "retry_task", "Retry task", show=False),
        Binding("f9", "cancel_task", "Cancel task", show=False),
        Binding("ctrl+s", "save_log", "Save log", show=False),
        Binding("delete", "remove_task", "Remove finished task", show=False),
    ]

    def __init__(self, shell: "InteractiveShell", task_tree: TaskTree) -> None:
        super().__init__()
        self._shell = shell
        self._tree = task_tree
        self._sidebar_width = SIDEBAR_WIDTH
        # Set once the user sizes the sidebar by hand; from then on the width
        # is theirs and a terminal resize only clamps it.
        self._sidebar_manual = False
        # Last observed terminal width, used to notice resizes (see _sync_split).
        self._terminal_width = 0
        # Debounce handle for the log filter (see _queue_filter_replay).
        self._filter_timer = None
        # Alert throttling state (see _on_log_alert).
        self._alert_window_end = 0.0
        self._alert_shown = 0
        self._alert_suppressed = 0
        self._alert_note_until = 0.0
        # Collected `qa` results, waiting for the batch to finish arriving.
        self._quality_buffer: list = []
        self._quality_timer = None
        # Whether the tree had unfinished work on the previous tick, so the
        # "finished" notification fires on the transition only.
        self._was_busy = False

        self._log = LogPanel(id="log")
        self._tasks = TaskPanel(task_tree, id="task-tree")
        # markup=False: the text contains ``<url>``, which Static would
        # otherwise try to parse as a Rich tag and swallow.
        self._empty = Static(_EMPTY_TASKS, id="task-empty", markup=False)
        self._search = SearchBar(id="search-bar")
        self._input = CommandInput(
            suggester=CommandSuggester(shell.completion_tree()),
            id="input-frame",
        )
        self._status = StatusBar(
            get_regions=_get_regions,
            is_tailing=lambda: self._log.is_tailing,
            is_batch=lambda: bool(getattr(shell, "batch_mode", False)),
            get_stats=task_tree.stats,
            log_filter=self._log.filter_label,
            suppressed_alerts=self.suppressed_alert_count,
            pending_lines=lambda: self._log.pending_lines,
            id="status-bar",
        )

    # ------------------------------------------------------------------ #
    # Composition
    # ------------------------------------------------------------------ #

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Container(id="log-frame"):
                yield self._log
            with Container(id="task-frame"):
                yield self._tasks
                yield self._empty
        yield self._search
        yield self._input
        yield self._status

    def on_mount(self) -> None:
        self._log_frame = self.query_one("#log-frame")
        self._task_frame = self.query_one("#task-frame")
        self._log_frame.border_title = " LOG "
        self._task_frame.border_title = " TASKS "
        self._search.display = False
        self._empty.display = True

        # Restore what the user chose last time.  The sidebar width is only
        # pinned when it was set by hand — persisting the automatic width would
        # stop a later, wider terminal from ever getting its columns back.
        stored = settings.load()
        theme = stored.get(settings.THEME_KEY, "")
        self.theme = theme if theme in THEMES else THEMES[0]
        self._sync_palette()

        saved_width = stored.get(settings.SIDEBAR_WIDTH_KEY, "")
        if saved_width.isdigit():
            self._sidebar_manual = True
            self._sidebar_width = int(saved_width)
        self._terminal_width = self.size.width
        # Fit the split now, not on the first tick: the tick only re-fits when
        # the width *changes*, so a terminal that starts out narrow would keep
        # a sidebar it has no room for.
        self._sync_split()

        # ``log_sink`` can be pushed to from worker threads, so the toast is
        # raised from its (loop-thread) alert dispatch rather than from the
        # logging call site.
        log_sink.subscribe_alert(self._on_log_alert)

        # The command line keeps its own history: the terminal's line editor is
        # not in the loop here, so nothing else would remember it.
        self._input.set_history(history.load())

        # `qa` output goes to a scrollable table instead of the log pane; a
        # `tabulate` grid is a fixed ~90 columns and shattered in a narrow pane.
        quality.set_quality_view(self.show_quality)

        # The tree mirrors a data model mutated by the ripper's coroutines, so
        # it is polled; the log pane is event-driven and needs no tick.
        self.set_interval(0.5, self._tick)

        self._input.focus_input()

    def on_unmount(self) -> None:
        log_sink.unsubscribe_alert(self._on_log_alert)
        quality.set_quality_view(None)
        history.save(self._input.history)
        self._save_settings()

    def _save_settings(self) -> None:
        """Persist the choices the user made this session."""
        values: dict[str, object] = {settings.THEME_KEY: self.theme}
        if self._sidebar_manual:
            values[settings.SIDEBAR_WIDTH_KEY] = self._sidebar_width
        settings.save(values)

    def _sync_split(self) -> None:
        """Re-fit the log/sidebar split for the current terminal width.

        A narrow terminal shrinks the sidebar first, so the log pane keeps a
        workable number of columns instead of being squeezed to a few cells; a
        very narrow one drops the sidebar entirely.  This runs from the tick
        rather than ``on_resize``: Textual dispatches ``Resize`` to the active
        screen and stops it on the way past, so an ``App.on_resize`` handler is
        never called.
        """
        if self.size.width and self.size.width < SINGLE_PANE_WIDTH:
            self._task_frame.display = False
            self._log_frame.styles.width = "1fr"
            return

        self._task_frame.display = True
        self._log_frame.styles.width = "7fr"
        if self._sidebar_manual:
            # The user picked this width; only re-clamp it.
            self._apply_sidebar_width(self._sidebar_width)
        else:
            self._apply_sidebar_width(self._auto_sidebar_width())

    def _tick(self) -> None:
        width = self.size.width
        if width != self._terminal_width:
            self._terminal_width = width
            self._sync_split()
        # Sample the transfer rate on the same cadence as the repaint so the
        # status-bar sparkline has an evenly spaced series to draw.
        it(Measurer).sample_speed()
        # Fold finished groups before mirroring the tree: the fold is a model
        # change and must happen at a single point per frame (see
        # ``TaskTree.collapse_finished``).
        self._tree.collapse_finished()
        self._tasks.sync()
        # The sidebar doubles as the empty state: a blank bordered box reads as
        # "broken", a short usage hint reads as "ready".
        self._empty.display = not self._tasks.children
        self._status.refresh_content()
        self._announce_if_finished()

    def _announce_if_finished(self) -> None:
        """Say so when a batch stops having work left.

        A download is something you start and walk away from, so "it is done"
        has to be said out loud rather than implied by a sidebar you are not
        looking at.  Keyed on the transition, not on the totals, so starting a
        second batch announces again.
        """
        stats = self._tree.stats()
        busy = bool(stats.total) and stats.done < stats.total
        if self._was_busy and not busy:
            if stats.failed:
                self.notify(
                    f"{stats.done - stats.failed} of {stats.total} tracks done, "
                    f"{stats.failed} failed",
                    title="Finished", severity="warning", timeout=10,
                )
            else:
                self.notify(f"All {stats.total} tracks done", title="Finished",
                            timeout=10)
        self._was_busy = busy

    # ------------------------------------------------------------------ #
    # Command input
    # ------------------------------------------------------------------ #

    async def on_command_input_submitted(self, event: CommandInput.Submitted) -> None:
        text = event.value
        await self._shell.command_parser(text)

        # Sync UI state back from the shell (batch mode may have been toggled
        # by `dl -b`, or cancelled by `exit`).
        batch_mode = bool(getattr(self._shell, "batch_mode", False))
        self._input.set_batch(batch_mode)
        self._status.refresh_content()

        if batch_mode:
            prefix = text.split()[0] if text.split() else "dl"
            self._open_batch(prefix)

    # ------------------------------------------------------------------ #
    # Batch panel
    # ------------------------------------------------------------------ #

    def _open_batch(self, cmd_prefix: str) -> None:
        self.push_screen(
            BatchPanel(cmd_prefix),
            lambda urls, prefix=cmd_prefix: self._on_batch_closed(urls, prefix),
        )

    def _on_batch_closed(self, urls: list[str] | None, cmd_prefix: str) -> None:
        self._shell.batch_mode = False
        self._input.set_batch(False)
        self._status.refresh_content()
        if urls:
            self.run_worker(self._run_batch(urls, cmd_prefix), exclusive=False)

    async def _run_batch(self, urls: list[str], cmd_prefix: str) -> None:
        for url in urls:
            await self._shell.command_parser(f"{cmd_prefix} {url}")

    # ------------------------------------------------------------------ #
    # Modal text prompt
    # ------------------------------------------------------------------ #

    async def ask_text(self, prompt: str, password: bool = False) -> Optional[str]:
        """Show a modal single-line prompt and wait for the answer.

        Returns the entered string, or ``None`` when the user cancelled.  The
        caller keeps ownership of the "cancelled" semantics: ``_ask()`` in
        ``cmd.py`` turns ``None`` into ``EOFError`` so the existing
        ``except (EOFError, KeyboardInterrupt)`` guards around the login /
        logout flows keep working unchanged.

        A plain future is used instead of Textual's ``push_screen_wait``,
        because that helper requires an active worker context and the login
        flow is awaited straight from a message handler.
        """
        future: asyncio.Future[Optional[str]] = (
            asyncio.get_running_loop().create_future()
        )

        def _done(value: Optional[str]) -> None:
            if not future.done():
                future.set_result(value)

        self.push_screen(PromptScreen(prompt, password=password), _done)
        return await future

    # ------------------------------------------------------------------ #
    # Log filtering
    # ------------------------------------------------------------------ #

    def action_search_log(self) -> None:
        """Show the filter row and put the cursor in it."""
        self._search.display = True
        self._search.focus_input()

    def action_cycle_log_level(self) -> None:
        """Step the level floor: INFO → WARNING → ERROR → INFO."""
        level = self._log.cycle_min_level()
        self.notify(
            f"Log level: {level}+" if level != "INFO" else "Log level: all",
            timeout=2,
        )
        self._status.refresh_content()

    def _queue_filter_replay(self) -> None:
        """Replay the log backlog once typing pauses.

        Filtering is a full replay of the buffered lines, which is linear in
        the backlog; doing that on every keystroke makes the field feel stuck
        on a long session, so it is debounced.
        """
        if self._filter_timer is not None:
            self._filter_timer.stop()
        self._filter_timer = self.set_timer(_FILTER_DEBOUNCE, self._log.replay)

    def on_search_bar_changed(self, event: SearchBar.Changed) -> None:
        self._log.set_filter_text(event.value, replay=False)
        self._queue_filter_replay()
        self._status.refresh_content()

    def on_search_bar_closed(self, event: SearchBar.Closed) -> None:
        if event.cancelled:
            self._log.set_filter_text("", replay=True)
        self._search.display = False
        self._input.focus_input()
        self._status.refresh_content()

    # ------------------------------------------------------------------ #
    # Quality tables
    # ------------------------------------------------------------------ #

    def show_quality(
        self, title: str, headers: list[str], rows: list[list[str]]
    ) -> None:
        """Presentation hook for ``qa`` (installed on ``src.quality``).

        Collects results briefly so that one `qa <album>` produces one table
        rather than one modal screen per track.
        """
        self._quality_buffer.append((title, headers, rows))
        if self._quality_timer is not None:
            self._quality_timer.stop()
        self._quality_timer = self.set_timer(
            _QUALITY_COLLECT, self._flush_quality
        )

    def _flush_quality(self) -> None:
        self._quality_timer = None
        batch = self._quality_buffer
        self._quality_buffer = []
        merged = QualityScreen.merge(batch)
        if merged is None:
            return
        headers, rows, title, note = merged
        self.push_screen(QualityScreen(headers, rows, title=title, note=note))

    # ------------------------------------------------------------------ #
    # Alerts
    # ------------------------------------------------------------------ #

    def _on_log_alert(self, level: str, line: str) -> None:
        """Raise an ERROR-and-above log line as a toast, at a bounded rate.

        The log pane is scrolled and can be filtered, so a failure that only
        wrote a line there could pass unnoticed — the toast is the part that
        does not depend on the user watching the right pane at the right time.

        But a batch download failing at the network layer fails *every* track
        at once, and an unbounded stream of toasts covers the whole interface.
        So each window shows at most ``_ALERT_MAX_PER_WINDOW``; the remainder
        are counted and surfaced in the status bar instead, where they cost no
        screen space.
        """
        now = time.monotonic()
        if now >= self._alert_window_end:
            if self._alert_suppressed:
                # Keep the previous window's count visible for a while, so a
                # burst that has already stopped is still reported.
                self._alert_note_until = now + _ALERT_NOTE_LINGER
            self._alert_window_end = now + _ALERT_WINDOW
            self._alert_shown = 0
            self._alert_suppressed = 0

        if self._alert_shown >= _ALERT_MAX_PER_WINDOW:
            self._alert_suppressed += 1
            self._alert_note_until = now + _ALERT_NOTE_LINGER
            self._status.refresh_content()
            return

        self._alert_shown += 1
        text = _TIMESTAMP_RE.sub("", _ANSI_RE.sub("", line)).strip()
        if len(text) > _TOAST_MAX_CHARS:
            text = text[: _TOAST_MAX_CHARS - 1] + "…"
        severity = "warning" if level.upper() == "WARNING" else "error"
        try:
            self.notify(text, title=level.title(), severity=severity,
                        timeout=8, markup=False)
        except Exception:
            # Notifications are best-effort; never break the logging path.
            pass

    def suppressed_alert_count(self) -> int:
        """How many alerts were merged away recently (for the status bar)."""
        if time.monotonic() >= self._alert_note_until:
            return 0
        return self._alert_suppressed

    # ------------------------------------------------------------------ #
    # Task sidebar actions
    # ------------------------------------------------------------------ #

    def _selected_task(self):
        """The node under the sidebar cursor, or None."""
        try:
            return self._tasks.highlighted_node()
        except Exception:
            return None

    def action_expand_all_tasks(self) -> None:
        count = self._tree.set_all_expanded(True)
        self._tasks.sync()
        self.notify(f"Expanded {count} group{'s' if count != 1 else ''}",
                    timeout=2)

    def action_collapse_all_tasks(self) -> None:
        count = self._tree.set_all_expanded(False)
        self._tasks.sync()
        self.notify(f"Folded {count} group{'s' if count != 1 else ''}",
                    timeout=2)

    def copy_to_system_clipboard(self, text: str) -> bool:
        """Copy *text*, reaching the OS clipboard and Textual's own buffer.

        ``copy_to_clipboard`` emits OSC 52, which terminals are free to
        ignore; the ctypes write is what makes a paste into another window
        work.  Either one succeeding is enough, so this reports success if
        either path worked.
        """
        copied = set_clipboard_text(text)
        try:
            self.copy_to_clipboard(text)
            copied = True
        except Exception:  # noqa: BLE001 - a terminal that cannot copy
            pass
        return copied

    def action_copy_task_name(self) -> None:
        """Copy the highlighted row's title to the clipboard."""
        node = self._selected_task()
        if node is None:
            self.notify("Select a task in the sidebar first",
                        severity="warning", timeout=2)
            return
        name = node.resolved_name
        if self.copy_to_system_clipboard(name):
            self.notify(f"Copied: {name}", timeout=2, markup=False)
        else:
            self.notify("Clipboard unavailable", severity="warning", timeout=3)

    def action_remove_task(self) -> None:
        """Drop the highlighted row from the sidebar.

        Only finished rows are removable: the sidebar is a view of the ripper's
        state, and hiding a task that is still writing files would leave the
        user with no way to see what happened to it.
        """
        node = self._selected_task()
        if node is None:
            self.notify("Select a task in the sidebar first",
                        severity="warning", timeout=2)
            return
        if not node.status.is_terminal():
            self.notify(f"'{node.resolved_name}' is still running",
                        severity="warning", timeout=2, markup=False)
            return
        self._tree.remove(node.node_id)
        self._tasks.sync()
        self._empty.display = not self._tasks.children

    # ------------------------------------------------------------------ #
    # Retry / cancel
    # ------------------------------------------------------------------ #

    def _task_nodes(self, node) -> list:
        """Every leaf task at or below *node*."""
        if node.task is not None:
            return [node]
        found: list = []
        for child in node.children:
            found.extend(self._task_nodes(child))
        return found

    def action_retry_task(self) -> None:
        """Run the highlighted task — or every failed track in a group — again.

        Works on a failure, but also on a success: re-ripping a track with a
        different codec is the same operation as retrying one that failed.
        """
        from src.task import Status

        node = self._selected_task()
        if node is None:
            self.notify("Select a task in the sidebar first",
                        severity="warning", timeout=2)
            return

        if node.task is not None:
            targets = [node]
        else:
            # A group: retry what actually broke, not the whole album.
            targets = [n for n in self._task_nodes(node)
                       if n.task.status == Status.FAILED]
            if not targets:
                self.notify(f"'{node.resolved_name}' has no failed tracks",
                            severity="warning", timeout=2, markup=False)
                return

        runnable = [n for n in targets if getattr(n.task, "request", None) is not None]
        if not runnable:
            self.notify("No recorded request for that task", severity="warning",
                        timeout=3)
            return
        self.run_worker(self._retry_nodes(runnable), exclusive=False)

    async def _retry_nodes(self, nodes: list) -> None:
        retried = 0
        for node in nodes:
            try:
                if await self._shell.retry_task(node.task):
                    retried += 1
            except Exception as exc:
                self.notify(f"Retry failed: {exc}", severity="error", timeout=4)
        if retried:
            self.notify(f"Retrying {retried} task{'s' if retried != 1 else ''}…",
                        timeout=3)
            self._tick()

    def action_cancel_task(self) -> None:
        """Cancel the coroutine behind the highlighted task."""
        node = self._selected_task()
        if node is None or node.task is None:
            self.notify("Select a running task in the sidebar first",
                        severity="warning", timeout=2)
            return
        task = node.task
        if task.status.is_terminal():
            self.notify("That task has already finished", severity="warning",
                        timeout=2)
            return
        runner = getattr(task, "runner", None)
        if runner is None or runner.done():
            self.notify("No running coroutine for that task", severity="warning",
                        timeout=3)
            return
        # Safe from here: this runs on the event loop the task belongs to.
        runner.cancel()
        self.notify(f"Cancelling '{task.display_name}'…", timeout=2, markup=False)

    # ------------------------------------------------------------------ #
    # Theme
    # ------------------------------------------------------------------ #

    def action_cycle_theme(self) -> None:
        try:
            index = THEMES.index(self.theme)
        except ValueError:
            index = -1
        self.theme = THEMES[(index + 1) % len(THEMES)]
        self._sync_palette()
        self.notify(f"Theme: {self.theme}", timeout=2)

    def _sync_palette(self) -> None:
        """Point the Rich-side palette at the current theme's brightness."""
        try:
            dark = self.get_theme(self.theme).dark
        except Exception:
            dark = True
        style.set_palette(bool(dark))
        # The rows and the status bar are already built; ask for a repaint.
        self._tasks.sync()
        self._status.refresh_content()

    # ------------------------------------------------------------------ #
    # Command palette
    # ------------------------------------------------------------------ #

    def get_system_commands(self, screen: Screen):
        """Add the application's own actions to the Ctrl+P palette."""
        yield from super().get_system_commands(screen)

        def download() -> None:
            self._input.prefill("dl ")

        def quality() -> None:
            self._input.prefill("qa ")

        async def batch() -> None:
            await self._shell.command_parser("dl -b")

        async def status() -> None:
            await self._shell.command_parser("status")

        async def clear() -> None:
            await self._shell.command_parser("cl")

        yield SystemCommand("Download…", "Focus the command line, ready for a URL",
                            download)
        yield SystemCommand("Inspect quality…", "Focus the command line with 'qa'",
                            quality)
        yield SystemCommand("Batch input", "Paste many URLs at once", batch)
        yield SystemCommand("Wrapper status", "Show the wrapper status and regions",
                            status)
        yield SystemCommand("Clear finished tasks", "Prune the sidebar", clear)

    # ------------------------------------------------------------------ #
    # Log export
    # ------------------------------------------------------------------ #

    def action_save_log(self) -> None:
        """Write the log buffer to a timestamped file beside ``config.toml``.

        The pane holds two thousand lines and shows twenty-two of them, so a bug
        report with the actual log in it is worth much more than a screenshot.
        Colour codes are stripped: the file is meant to be read and pasted.
        """
        lines = log_sink.get_lines()
        if not lines:
            self.notify("Nothing in the log yet", severity="warning", timeout=2)
            return

        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = Path.cwd() / f"applemusicdecrypt-{stamp}.log"
        body = "\n".join(_ANSI_RE.sub("", line) for line in lines) + "\n"
        try:
            path.write_text(body, encoding="utf-8")
        except OSError as exc:
            self.notify(f"Could not write {path.name}: {exc}",
                        severity="error", timeout=6, markup=False)
            return
        self.notify(f"Saved {len(lines)} lines to {path.name}", timeout=6,
                    markup=False)

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    async def action_help(self) -> None:
        """Open the key/command reference panel.

        The ``help`` *command* still prints the verbose version into the log;
        F1 is the panel, which does not scroll away.
        """
        self.push_screen(
            HelpScreen(bindings=self.BINDINGS,
                       commands=self._shell.completion_tree())
        )

    async def action_confirm_exit(self) -> None:
        await self._shell.confirm_and_exit()

    def action_narrow_sidebar(self) -> None:
        self._sidebar_manual = True
        self._apply_sidebar_width(self._sidebar_width - 4)

    def action_widen_sidebar(self) -> None:
        self._sidebar_manual = True
        self._apply_sidebar_width(self._sidebar_width + 4)

    def _auto_sidebar_width(self) -> int:
        """Sidebar width that suits the current terminal size (~a third)."""
        width = self.size.width
        if width <= 0:
            return SIDEBAR_WIDTH
        return max(SIDEBAR_MIN, min(SIDEBAR_WIDTH, int(width * 0.35)))

    def _clamp_sidebar(self, width: int) -> int:
        """Keep the sidebar inside its bounds and off the log pane's columns."""
        limit = max(SIDEBAR_MIN, self.size.width - LOG_MIN_WIDTH)
        return max(SIDEBAR_MIN, min(SIDEBAR_MAX, width, limit))

    def _apply_sidebar_width(self, width: int) -> None:
        self._sidebar_width = self._clamp_sidebar(width)
        frame = self.query_one("#task-frame")
        frame.styles.width = self._sidebar_width
        frame.refresh(layout=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run_tui(shell: "InteractiveShell") -> None:
    """Build and run the full-screen TUI, returning when the user exits."""
    log_sink.install()
    # Deliver sink wake-ups on this loop; log lines can come from worker
    # threads (src/utils.py uses a ThreadPoolExecutor).
    log_sink.bind_loop(asyncio.get_running_loop())

    # conhost blocks every console write while a QuickEdit selection is active,
    # and the TUI redraws from the asyncio thread — one stray click-drag in a
    # Windows console would freeze the whole app until Esc.
    restore_console = disable_quick_edit()

    tree = it(TaskTree)
    app = DecryptTUI(shell, tree)
    # Command handlers call shell.handle_exit(), which needs the app to ask
    # Textual to exit cleanly (restoring the terminal).
    shell._tui_app = app  # type: ignore[attr-defined]

    try:
        await app.run_async()
    except Exception as exc:  # noqa: BLE001 - surfaced to the user below
        # Textual could not drive this terminal (no pty, unsupported TERM,
        # redirected stdio, …).  Point at the escape hatch instead of dying
        # with a traceback — this is the failure that used to be silent with
        # prompt_toolkit's DummyInput fallback.
        raise SystemExit(
            f"Unable to start the full-screen TUI in this terminal: {exc}\n"
            f"Try running with --legacy-ui for a plain REPL."
        ) from exc
    finally:
        restore_console()
        log_sink.bind_loop(None)
        log_sink.uninstall()
        if it(Config).localInstance.enable:
            await shell.localInstance.terminate()
