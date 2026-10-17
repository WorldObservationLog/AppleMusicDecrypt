"""Plain-terminal REPL — the ``--legacy-ui`` fallback.

This is the "simple terminal" path: no alternate screen, no mouse tracking, no
full-screen redraw.  It exists for environments where Textual cannot drive the
terminal at all (no pty, an exotic ``TERM``, a serial console, a Termux session
with an unusual keyboard) and as a debugging escape hatch.

Design notes
------------
The previous implementation was a ``prompt_toolkit.PromptSession`` wrapped in
``patch_stdout()``.  Both are gone; this module uses the stdlib ``input()`` and
Rich.  Two consequences are worth recording, because they are properties of
``input()`` and not bugs:

* **No completion menu.**  ``input()`` cannot render one.  The nested command
  grammar is instead exposed on demand: a command ending in ``?`` prints the
  words valid at that point (``dl --codec ?`` → the codec list).  Line editing
  and history still work, but they come from the terminal's own line
  discipline (GNU readline on POSIX, the console host on Windows), not from us.

* **The prompt line is not reflowable.**  ``patch_stdout`` could redraw the
  partially typed line because it owned the renderer.  Here the log sink
  erases the prompt line, writes the log line, and redraws the bare prompt —
  characters the user had already typed disappear from the screen (the kernel's
  line buffer still holds them, so the command they finally submit is intact).
  Keeping the two from interleaving mid-line is the best available behaviour
  without taking over the terminal.

Reading happens in a worker thread (``asyncio.to_thread``) so the event loop
keeps running downloads and decrypt jobs while the user is typing.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from typing import TYPE_CHECKING, Optional

from creart import it
from rich.console import Console
from rich.text import Text

from src.config import Config
from src.logger import GlobalLogger

if TYPE_CHECKING:
    from src.cmd import InteractiveShell

PROMPT = "> "

# Erase the current line and return the cursor to column 0.
_ERASE_LINE = "\r\x1b[2K"


class LegacyConsole:
    """Owns the terminal for the duration of a legacy REPL session.

    Serves three roles at once: it renders the prompt and the status line, it
    is the log sink (so log output cannot corrupt the line being typed), and it
    provides the ``?``-suffix completion listing that replaces the old popup
    menu.

    ``read_fn`` / ``password_fn`` exist so tests can drive the REPL without a
    tty; they default to ``input`` and ``getpass.getpass``.
    """

    def __init__(self, read_fn=None, password_fn=None) -> None:
        # markup is on here (unlike the logger's console) because this console
        # also renders our own Rich-markup chrome.
        self._console = Console(highlight=False, soft_wrap=True, markup=True)
        self._lock = threading.RLock()
        self._reading: bool = False
        self._prompt_text: str = ""
        self._original_safe_print = None
        self._read_fn = read_fn
        self._password_fn = password_fn

    def _read_plain(self) -> str:
        if self._read_fn is not None:
            return self._read_fn()
        return input()

    def _read_secret(self, prompt: str) -> str:
        if self._password_fn is not None:
            return self._password_fn(prompt)
        import getpass
        return getpass.getpass(prompt)

    # ------------------------------------------------------------------ #
    # Log sink
    # ------------------------------------------------------------------ #

    def install(self) -> None:
        """Route logger output through this console."""
        import src.logger as _logger_mod

        self._original_safe_print = _logger_mod._safe_print

        def _sink(msg: str) -> None:
            self.emit(msg)

        _logger_mod._safe_print = _sink  # type: ignore[attr-defined]

    def uninstall(self) -> None:
        import src.logger as _logger_mod

        if self._original_safe_print is not None:
            _logger_mod._safe_print = self._original_safe_print  # type: ignore[attr-defined]
            self._original_safe_print = None

    def emit(self, msg: str) -> None:
        """Write one pre-formatted (ANSI) log line, preserving the prompt.

        Called from the loop thread and from worker threads; the lock keeps a
        log line from landing in the middle of the prompt.
        """
        with self._lock:
            # Flush raw writes before handing over to Rich so ordering holds.
            try:
                sys.stdout.flush()
            except Exception:
                pass
            if self._reading:
                sys.stdout.write(_ERASE_LINE)
                sys.stdout.flush()
            try:
                self._console.print(Text.from_ansi(msg), end="")
            except Exception:
                sys.__stderr__.write(msg)
                sys.__stderr__.flush()
            if self._reading:
                sys.stdout.write(self._prompt_text)
                sys.stdout.flush()

    # ------------------------------------------------------------------ #
    # Output helpers
    # ------------------------------------------------------------------ #

    def write(self, markup: str) -> None:
        """Print a Rich-markup line of our own (outside the log stream)."""
        with self._lock:
            if self._reading:
                sys.stdout.write(_ERASE_LINE)
                sys.stdout.flush()
            try:
                self._console.print(markup)
            except Exception:
                sys.__stderr__.write(markup + "\n")
            if self._reading:
                sys.stdout.write(self._prompt_text)
                sys.stdout.flush()

    def banner(self) -> None:
        """One-time greeting; documents what this mode does and does not do."""
        self.write(
            "[bold #a6e3a1]AppleMusicDecrypt[/] [dim]— legacy UI "
            "(no full-screen interface)[/]"
        )
        self.write(
            "[dim]Type a command, e.g. [bold]dl <url>[/].  "
            "[bold]help[/] lists commands, [bold]exit[/] quits.[/]"
        )
        self.write(
            "[dim]Completion: end a command with [bold]?[/] to list the words "
            "valid there (e.g. [bold]dl --codec ?[/]).  "
            "Line editing/history come from the terminal itself.[/]"
        )

    def print_completions(self, tree: dict, typed: str) -> None:
        """List the words valid after *typed*, walking the command grammar."""
        level: object = tree
        for token in typed.split():
            if not isinstance(level, dict):
                level = None
                break
            level = level.get(token)  # type: ignore[union-attr]
        if not isinstance(level, dict) or not level:
            self.write(f"[dim]No completions for[/] [bold]{typed or '?'}[/]")
            return
        words = "  ".join(sorted(level))
        label = typed or "(root)"
        self.write(f"[bold #89b4fa]{label}[/] [dim]→[/] {words}")

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #

    def _read_blocking(
        self,
        toolbar: Optional[str],
        prompt: str,
        password: bool,
    ) -> str:
        """Draw the status line + prompt, then read one line.

        Runs in a worker thread.  ``input("")`` is used with no argument so
        that prompt rendering stays under our control — the prompt is written
        here, next to the ``_reading`` flag, so a log line arriving mid-read can
        erase and restore exactly that text.

        ``getpass`` is used for password input: it disables terminal echo and
        renders its own prompt, so the erase/restore dance is unnecessary.
        """
        if password:
            with self._lock:
                self._prompt_text = prompt
                self._reading = True
            try:
                return self._read_secret(prompt)
            finally:
                with self._lock:
                    self._reading = False

        with self._lock:
            if toolbar:
                sys.stdout.write(_ERASE_LINE + toolbar + "\n")
            sys.stdout.write(_ERASE_LINE + prompt)
            sys.stdout.flush()
            self._prompt_text = prompt
            self._reading = True
        try:
            return self._read_plain()
        finally:
            with self._lock:
                self._reading = False

    async def read_line(
        self,
        toolbar: Optional[str] = None,
        prompt: str = PROMPT,
        password: bool = False,
    ) -> Optional[str]:
        """Read one line, or ``None`` on EOF / Ctrl+D.

        Ctrl+C is *not* handled here: on POSIX it is delivered to the main
        thread, so it surfaces as ``KeyboardInterrupt`` from this ``await`` and
        is handled by the caller.
        """
        try:
            return await asyncio.to_thread(
                self._read_blocking, toolbar, prompt, password
            )
        except EOFError:
            return None


# ---------------------------------------------------------------------------
# REPL loop
# ---------------------------------------------------------------------------

async def run_legacy_repl(
    shell: "InteractiveShell",
    console: Optional["LegacyConsole"] = None,
) -> None:
    """Drive the plain-terminal REPL until the user exits.

    *console* is injectable so tests can supply a canned input source; the
    default builds a real one bound to the terminal.
    """
    from src.cmd import _LegacyExit

    if console is None:
        console = LegacyConsole()
    console.install()
    # ``_ask()`` needs the same console so login / logout prompts keep the
    # prompt line intact instead of writing over it.
    shell._legacy_console = console  # type: ignore[attr-defined]
    console.banner()
    try:
        while True:
            toolbar = shell.bottom_toolbar()
            try:
                line = await console.read_line(toolbar)
            except KeyboardInterrupt:
                line = None

            if line is None:
                # EOF (Ctrl+D) or Ctrl+C — go through the same two-step
                # confirmation the full-screen UI uses, so a stray Ctrl+C
                # cannot kill a running download.
                await shell.confirm_and_exit()
                continue

            text = line.strip()
            if not text:
                continue

            # `?` suffix: list what may follow this prefix.
            if text.endswith("?"):
                console.print_completions(shell.completion_tree(), text[:-1].strip())
                continue

            await shell.command_parser(text)
    except _LegacyExit:
        return
    finally:
        shell._legacy_console = None  # type: ignore[attr-defined]
        console.uninstall()
        if it(Config).localInstance.enable:
            await shell.localInstance.terminate()
        else:
            it(GlobalLogger).logger.info("Bye.")
