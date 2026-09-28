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
F1         help
Ctrl+V     paste from the system clipboard (right-click does the same)
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
from src.tui.win_console import disable_quick_edit, get_clipboard_text
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


def _read_clipboard() -> str:
    """System clipboard text; falls back to prompt_toolkit's in-app one."""
    text = get_clipboard_text()
    if not text:
        try:
            from prompt_toolkit.application.current import get_app
            text = get_app().clipboard.get_data().text or ""
        except Exception:
            text = ""
    return text


def _paste_into(buffer) -> None:
    """Insert clipboard text into ``buffer`` and focus it.

    Single-line buffers get newlines folded to spaces, otherwise every
    line would act as Enter and submit a separate command.
    """
    text = _read_clipboard().replace("\r\n", "\n").replace("\r", "\n")
    if not buffer.multiline():
        text = " ".join(part.strip() for part in text.split("\n") if part.strip())
    if not text:
        return
    buffer.insert_text(text)
    try:
        from prompt_toolkit.application.current import get_app
        get_app().layout.focus(buffer)
    except Exception:
        pass


def _install_right_click_paste(control, buffer) -> None:
    """Wrap ``control.mouse_handler``: right-click pastes, everything else
    falls through to the original handler (cursor placement, scrolling)."""
    from prompt_toolkit.mouse_events import MouseButton, MouseEventType

    orig = control.mouse_handler

    def _handler(mouse_event):
        if mouse_event.button == MouseButton.RIGHT:
            if mouse_event.event_type == MouseEventType.MOUSE_DOWN:
                _paste_into(buffer)
            return None
        return orig(mouse_event)

    control.mouse_handler = _handler


# ---------------------------------------------------------------------------
# Main TUI runner
# ---------------------------------------------------------------------------

async def run_tui(shell: "InteractiveShell") -> None:
    """Build and run the full-screen TUI, returning when the user exits."""

    # ── 1. log sink ───────────────────────────────────────────────────────
    log_sink.install()

    # ── 1b. reliable input source for Termux / proot ─────────────────────
    import os as _os
    if _os.environ.get("TERMUX_VERSION"):
        from src.logger import GlobalLogger as _GL
        try:
            from creart import it as _it
            _it(_GL).logger.warning(
                "Termux detected: if the on-screen keyboard is not responding, "
                "long-press the Termux terminal and use 'Text input', or enable "
                "the extra keys row (Termux settings -> Keyboard -> Show extra keys).")
        except Exception:
            pass

    # prompt_toolkit silently falls back to DummyInput when sys.stdin has no
    # usable fileno() — that makes every key (including Ctrl+C) dead.  In
    # such environments try /dev/tty explicitly.
    import sys as _sys
    _input_source = None
    try:
        _sys.stdin.fileno()
    except Exception:
        try:
            _tty_fd = _os.open("/dev/tty", _os.O_RDONLY)
            _input_source = _os.fdopen(_tty_fd, "r", encoding="utf-8", errors="replace")
        except Exception:
            _input_source = None
    from prompt_toolkit.input.defaults import create_input as _create_input
    if _input_source is None:
        _input_source = _create_input()   # may still be DummyInput
    else:
        _input_source = _create_input(_input_source)

    # ── 2. widget state ───────────────────────────────────────────────────
    tree   = it(TaskTree)
    app_ref: list[Application] = []   # filled after app construction

    # Shared mutable state (plain lists used as mutable cells).
    _batch_active = [False]
    _batch_args_cmd = ["dl"]         # stores the command prefix for batch
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

    # Right-click paste: with mouse_support on, the terminal forwards the
    # right button to the app instead of doing its own paste action.
    _install_right_click_paste(input_bar._textarea.control, input_bar._textarea.buffer)
    _install_right_click_paste(batch_panel._textarea.control, batch_panel._textarea.buffer)

    # ── 4. layout ─────────────────────────────────────────────────────────
    layout, floats = build_layout(
        log_view, task_list, input_bar, batch_panel, status_bar,
    )

    # ── 5. keybindings ────────────────────────────────────────────────────
    kb = _build_keybindings(
        log_view     = log_view,
        input_bar    = input_bar,
        batch_panel  = batch_panel,
        task_list    = task_list,
        is_batch     = is_batch,
        shell        = shell,
        app_ref      = app_ref,
        _batch_active = _batch_active,
    )

    # ── 6. Application ────────────────────────────────────────────────────
    app = Application(
        layout          = layout,
        style           = TUI_STYLE,
        key_bindings    = kb,
        # Always full-screen for a proper TUI.  Touch/mouse stays enabled so
        # tapping the log pane or the input bar switches focus (Termux users
        # explicitly prefer this over relying only on Tab).
        full_screen     = True,
        input           = _input_source,   # None -> prompt_toolkit default
        mouse_support   = True,
        refresh_interval = 0.5,
    )
    app_ref.append(app)

    # Store app reference on shell so command handlers can call invalidate().
    shell._tui_app = app  # type: ignore[attr-defined]

    # ── 7. run ────────────────────────────────────────────────────────────
    # Windows cmd: a QuickEdit selection blocks console writes and freezes
    # the event loop, so turn QuickEdit off while the TUI owns the console.
    restore_console = disable_quick_edit()
    try:
        await app.run_async()
    finally:
        restore_console()
        log_sink.uninstall()
        if it(Config).localInstance.enable:
            await shell.localInstance.terminate()


# ---------------------------------------------------------------------------
# Keybindings
# ---------------------------------------------------------------------------

def _build_keybindings(
    log_view:     LogView,
    input_bar:    InputBar,
    batch_panel:  BatchPanel,
    task_list:    "TaskListWidget",
    is_batch:     "Callable[[], bool]",
    shell:        "InteractiveShell",
    app_ref:      list,
    _batch_active: list,
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

    # ── focus guards (defined once, used by both tab and scroll) ─────────
    from prompt_toolkit.filters import has_focus as _has_focus
    from prompt_toolkit.filters import Condition as _Condition
    from prompt_toolkit.layout.dimension import Dimension as _D

    log_focused = _has_focus(log_view.window)
    input_focused = _has_focus(input_bar.window)
    task_focused = _has_focus(task_list._inner_window)
    not_batch = _Condition(lambda: not is_batch())

    # ── focus toggle (Tab): log -> sidebar -> input -> ... ───────────────

    @kb.add("tab")
    def _focus_toggle(event):
        if log_focused():
            event.app.layout.focus(task_list._inner_window)
        elif task_focused():
            event.app.layout.focus(input_bar.window)
        else:
            event.app.layout.focus(log_view.window)
        invalidate()

    # sidebar scroll when focused
    @kb.add("up", filter=task_focused & not_batch)
    def _sidebar_scroll_up(event):
        task_list.scroll(-1)
        invalidate()

    @kb.add("down", filter=task_focused & not_batch)
    def _sidebar_scroll_down(event):
        task_list.scroll(1)
        invalidate()

    @kb.add("pageup", filter=task_focused & not_batch)
    def _sidebar_page_up(event):
        task_list.scroll(-10)
        invalidate()

    @kb.add("pagedown", filter=task_focused & not_batch)
    def _sidebar_page_down(event):
        task_list.scroll(10)
        invalidate()

    # ── log scroll (only when the log pane is focused) ──────────────────
    # The has_focus guard is essential: without it these bindings steal
    # up/down from the command input's history navigation.

    @kb.add("up",    filter=log_focused & not_batch)
    def _scroll_up(event):
        log_view.scroll_up(1)
        invalidate()

    @kb.add("down",  filter=log_focused & not_batch)
    def _scroll_down(event):
        log_view.scroll_down(1)
        invalidate()

    @kb.add("pageup", filter=log_focused & not_batch)
    def _page_up(event):
        log_view.scroll_up(10)
        invalidate()

    @kb.add("pagedown", filter=log_focused & not_batch)
    def _page_down(event):
        log_view.scroll_down(10)
        invalidate()

    @kb.add("end", filter=log_focused)
    def _tail(event):
        log_view.scroll_to_bottom()
        invalidate()


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

    # ── paste (Ctrl+V) from the system clipboard ────────────────────────
    @kb.add("c-v")
    def _paste(event):
        if is_batch():
            _paste_into(batch_panel._textarea.buffer)
        else:
            _paste_into(input_bar._textarea.buffer)
        invalidate()

    # ── sidebar width (Ctrl+Left / Ctrl+Right) ──────────────────────────
    @kb.add("c-left")
    def _sidebar_narrower(event):
        from src.tui.layout import set_sidebar_width
        set_sidebar_width(-4)
        invalidate()

    @kb.add("c-right")
    def _sidebar_wider(event):
        from src.tui.layout import set_sidebar_width
        set_sidebar_width(4)
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
