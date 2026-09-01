"""TUI Application — entry point for the interactive interface.

``run_tui(shell)`` is the replacement for ``InteractiveShell.start()``.
It:
  1. Installs the log sink (redirects GlobalLogger / RipLogger into the deque).
  2. Instantiates all widgets and wires them together.
  3. Builds the prompt_toolkit Application.
  4. Registers all keybindings.
  5. Runs ``app.run_async()`` on the existing asyncio event loop.
  6. Tears down on exit (uninstalls log sink, stops QEMU if needed).

Keybindings
-----------
Tab        focus log pane <-> input bar
Up/Down    scroll log (log focused) / command history (input focused)
End        log auto-follow after scrolling
F1         help;  F2  narrow-terminal LOG<->TASKS toggle
F10/Ctrl+C exit (two-step confirm while tasks run)
Ctrl+D/Esc submit / cancel the batch panel

Command dispatch is still handled by ``InteractiveShell.command_parser()``;
this module only owns the UI shell.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import TYPE_CHECKING

from creart import it
from prompt_toolkit import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.bindings.focus import focus_next

from src.config import Config
from src.measurer import Measurer
from src.wrapper import WrapperClient
from src.tui import log_sink
from src.tui.style import TUI_STYLE
from src.tui.task_tree import TaskTree
from src.tui.layout import build_layout
from src.tui.widgets.log_view   import LogView
from src.tui.widgets.task_list  import TaskListWidget
from src.tui.widgets.input_bar  import InputBar
from src.tui.widgets.batch_panel import BatchPanel
from src.tui.widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from src.cmd import InteractiveShell


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_regions() -> list[str]:
    """Return the cached region list from WrapperClient (non-blocking)."""
    try:
        cached = WrapperClient.status.cache_info()  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        client = it(WrapperClient)
        # Access the cached result without triggering a new network call.
        # The underlying alru_cache stores the coroutine result; we can
        # peek at it via __wrapped__ or just call synchronously if cached.
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Fire-and-forget: the status was fetched at startup; use the
            # last known value stored on the client object if available.
            return getattr(client, "_last_regions", [])
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Main TUI runner
# ---------------------------------------------------------------------------

async def run_tui(shell: "InteractiveShell") -> None:
    """Build and run the full-screen TUI, returning when the user exits."""

    # ── 1. log sink ───────────────────────────────────────────────────────
    log_sink.install()

    # ── 2. widget state ───────────────────────────────────────────────────
    tree   = it(TaskTree)
    app_ref: list[Application] = []   # filled after app construction

    # Shared mutable state (plain lists used as mutable cells).
    _batch_active = [False]
    _batch_args_cmd = ["dl"]         # stores the command prefix for batch
    # Narrow-terminal mode: on phones / split screens the sidebar and log
    # cannot fit side-by-side.  Narrow terminals start in LOG view; F2
    # toggles to the full-width TASKS pane and back.
    from src.tui.layout import NARROW_THRESHOLD
    import shutil as _shutil
    _narrow_tasks = [(_shutil.get_terminal_size().columns or 80) < NARROW_THRESHOLD]

    def is_narrow_tasks() -> bool:
        return _narrow_tasks[0]

    def is_batch() -> bool:
        return _batch_active[0]

    def is_tailing() -> bool:
        return log_view.is_tailing

    # ── 3. widgets ────────────────────────────────────────────────────────
    log_view  = LogView()
    task_list = TaskListWidget(tree)
    status_bar = StatusBar(
        get_regions = _get_regions,
        is_tailing  = is_tailing,
        is_batch    = is_batch,
        is_narrow   = is_narrow_tasks,
    )

    async def _on_command(text: str) -> None:
        """Dispatch a command string through InteractiveShell."""
        # Detect batch activation: shell sets shell.batch_mode.
        await shell.command_parser(text)
        # A command's output (help text, status panel, ...) should be
        # visible immediately — re-tail the log and redraw.
        log_view.scroll_to_bottom()
        # Sync batch flag back to TUI state.
        _batch_active[0] = shell.batch_mode
        if shell.batch_mode:
            _batch_args_cmd[0] = text.split()[0]   # "dl" or "download"
        if app_ref:
            app_ref[0].invalidate()

    async def _on_batch_submit(urls: list[str], cmd_prefix: str) -> None:
        """Submit collected URLs from the batch panel."""
        _batch_active[0] = False
        shell.batch_mode = False
        for url in urls:
            full_cmd = f"{cmd_prefix} {url}"
            await shell.command_parser(full_cmd)
        log_view.scroll_to_bottom()
        if app_ref:
            app_ref[0].invalidate()

    def _on_batch_cancel() -> None:
        _batch_active[0] = False
        shell.batch_mode = False
        if app_ref:
            app_ref[0].invalidate()

    input_bar = InputBar(
        completer=shell.completer(),
        on_submit=_on_command,
        is_batch=is_batch,
    )

    batch_panel = BatchPanel(
        is_active=is_batch,
        on_submit=_on_batch_submit,
        on_cancel=_on_batch_cancel,
        get_cmd_prefix=lambda: _batch_args_cmd[0],
    )

    # ── 4. layout ─────────────────────────────────────────────────────────
    layout, floats = build_layout(
        log_view, task_list, input_bar, batch_panel, status_bar,
        show_tasks=Condition(is_narrow_tasks),
    )

    # ── 5. keybindings ────────────────────────────────────────────────────
    kb = _build_keybindings(
        log_view     = log_view,
        input_bar    = input_bar,
        batch_panel  = batch_panel,
        is_batch     = is_batch,
        shell        = shell,
        app_ref      = app_ref,
        _batch_active = _batch_active,
        is_narrow_tasks = is_narrow_tasks,
        _narrow_tasks   = _narrow_tasks,
    )

    # ── 6. Application ────────────────────────────────────────────────────
    app = Application(
        layout          = layout,
        style           = TUI_STYLE,
        key_bindings    = kb,
        full_screen     = True,
        # Disable mouse in narrow terminals: Termux touch events can yank
        # focus away from the input bar.  Wide terminals keep mouse support.
        mouse_support   = not is_narrow_tasks(),
        refresh_interval = 0.5,
    )
    app_ref.append(app)

    # Store app reference on shell so command handlers can call invalidate().
    shell._tui_app = app  # type: ignore[attr-defined]

    # ── 7. run ────────────────────────────────────────────────────────────
    try:
        await app.run_async()
    finally:
        log_sink.uninstall()
        if it(Config).localInstance.enable:
            shell.localInstance.terminate()


# ---------------------------------------------------------------------------
# Keybindings
# ---------------------------------------------------------------------------

def _build_keybindings(
    log_view:     LogView,
    input_bar:    InputBar,
    batch_panel:  BatchPanel,
    is_batch:     "Callable[[], bool]",
    shell:        "InteractiveShell",
    app_ref:      list,
    _batch_active: list,
    is_narrow_tasks: "Callable[[], bool]" = lambda: False,
    _narrow_tasks:   list = None,
) -> KeyBindings:

    kb = KeyBindings()

    def invalidate():
        if app_ref:
            app_ref[0].invalidate()

    # ── exit ─────────────────────────────────────────────────────────────
    @kb.add("f10")
    async def _exit(event):
        await shell.confirm_and_exit()

    @kb.add("c-c")
    @kb.add("c-q")
    async def _ctrl_exit(event):
        await shell.confirm_and_exit()

    # ── focus toggle (Tab) ───────────────────────────────────────────────
    @kb.add("tab")
    def _focus_toggle(event):
        focus_next(event)

    # ── log scroll (only when the log pane is focused) ──────────────────
    # The has_focus guard is essential: without it these bindings steal
    # up/down from the command input's history navigation.
    from prompt_toolkit.filters import has_focus as _has_focus
    log_focused = _has_focus(log_view.window)

    @kb.add("up",    filter=log_focused & ~is_batch())
    def _scroll_up(event):
        log_view.scroll_up(1)
        invalidate()

    @kb.add("down",  filter=log_focused & ~is_batch())
    def _scroll_down(event):
        log_view.scroll_down(1)
        invalidate()

    @kb.add("pageup", filter=log_focused & ~is_batch())
    def _page_up(event):
        log_view.scroll_up(10)
        invalidate()

    @kb.add("pagedown", filter=log_focused & ~is_batch())
    def _page_down(event):
        log_view.scroll_down(10)
        invalidate()

    @kb.add("end", filter=log_focused)
    def _tail(event):
        log_view.scroll_to_bottom()
        invalidate()

    # ── sidebar scroll (Alt+↑ / Alt+↓, no focus needed) ─────────────────
    # ScrollablePane handles its own scroll internally through the mouse /
    # its own key events; we bind Alt+arrows as an explicit override.
    @kb.add("escape", "up")    # Alt+↑ in many terminals
    def _sidebar_up(event):
        task_list_pane = event.app.layout.container
        # ScrollablePane exposes scroll via its own internal state;
        # we trigger it by simulating the ScrollablePane's up action.
        try:
            event.app.layout.focus(batch_panel.window if is_batch()
                                   else input_bar.window)
        except Exception:
            pass

    # ── batch panel: submit (Ctrl+D) / cancel (Esc) ──────────────────────
    @kb.add("c-d", filter=Condition(is_batch))
    async def _batch_submit(event):
        await batch_panel.submit()
        invalidate()

    @kb.add("escape", filter=Condition(is_batch))
    def _batch_cancel(event):
        batch_panel.cancel()
        invalidate()

    # When batch mode just activated, shift focus to the batch panel.
    @kb.add("c-b")   # internal: triggered programmatically after batch detect
    def _focus_batch(event):
        if is_batch():
            try:
                event.app.layout.focus(batch_panel.window)
            except Exception:
                pass

    # ── F2: narrow-terminal LOG <-> TASKS toggle ─────────────────────────
    @kb.add("f2", filter=Condition(lambda: not is_batch()))
    def _toggle_tasks(event):
        if _narrow_tasks is not None:
            _narrow_tasks[0] = not _narrow_tasks[0]
            invalidate()

    # ── F1 help ──────────────────────────────────────────────────────────
    @kb.add("f1")
    def _help(event):
        # Inject the "help" command into the shell.
        async def _run_help():
            await shell.command_parser("help")
            log_view.scroll_to_bottom()
            invalidate()
        asyncio.get_event_loop().create_task(_run_help())

    # Input-bar scoped bindings: ↑/↓ history, Home/End line jumps.
    return merge_key_bindings([kb, input_bar.key_bindings()])
