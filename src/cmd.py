import argparse
import asyncio
import copy
import os
import sys

from creart import it

from src.api import WebAPI
from src.config import Config
from src.flags import Flags
from src.logger import GlobalLogger
from src.measurer import Measurer
from src.qemu import QemuInstance
from src.quality import print_song_quality, print_album_quality, print_playlist_quality, key_to_Headers
from src.rip import Ripper
from src.url import AppleMusicURL, URLType
from src.utils import check_dep, run_sync, safely_create_task, config_outdated
from src.wrapper import WrapperClient, WrapperError


class _LegacyExit(BaseException):
    """Internal control-flow signal used to leave the legacy REPL cleanly."""


class InteractiveShell:
    loop: asyncio.AbstractEventLoop
    parser: argparse.ArgumentParser
    localInstance: QemuInstance = QemuInstance()
    ripper: Ripper

    def __init__(self, loop: asyncio.AbstractEventLoop, legacy_ui: bool = False):
        self.ripper = Ripper()
        self.legacy_ui = legacy_ui
        dep_installed, missing_dep = check_dep()
        if not dep_installed:
            it(GlobalLogger).logger.error(f"Dependency {missing_dep} was not installed!")
            loop.stop()
            sys.exit()

        # ffmpeg is used for ALAC integrity verification.  It is optional:
        # when absent, the post-save ffmpeg check is skipped.
        import shutil as _shutil
        if _shutil.which("ffmpeg") is None:
            it(GlobalLogger).logger.warning(
                "ffmpeg was not found on PATH. ALAC integrity verification "
                "will be skipped; install ffmpeg to enable it.")

        self.loop = loop
        loop.run_until_complete(run_sync(it(WebAPI).init))
        if it(Config).localInstance.enable:
            loop.run_until_complete(self.localInstance.launch_instance(loop))
            it(Config).instance.url = f"127.0.0.1:{it(Config).localInstance.hostPort}"
            it(Config).instance.secure = False
            # First access to WrapperClient reads the (mutated) config.
            loop.run_until_complete(it(WrapperClient).init())
            it(WrapperClient).status.cache_invalidate()
            _start_status = loop.run_until_complete(it(WrapperClient).status())
            it(WrapperClient)._last_status = _start_status  # type: ignore[attr-defined]
            if not _start_status.get("regions"):
                manage = loop.run_until_complete(it(WrapperClient).supports_management())
                if manage:
                    it(GlobalLogger).logger.error(
                        "No Apple account is logged in on wrapper-manager.\n"
                        "Type  login  to sign in, then retry the download.")
                else:
                    it(GlobalLogger).logger.error(
                        "No Apple account is logged in on the wrapper instance.\n"
                        "Log in on the wrapper side first.")
                    if it(Config).localInstance.enable:
                        loop.run_until_complete(self.localInstance.terminate())
                    sys.exit(1)
        else:
            loop.run_until_complete(it(WrapperClient).init())

        try:
            loop.run_until_complete(self.show_status())
        except WrapperError:
            it(GlobalLogger).logger.error("Unable to connect to the wrapper-lite instance")
            sys.exit()

        if config_outdated():
            it(GlobalLogger).logger.warning(
                "The configuration file is out of date. Please refer to config.example.toml to update it")

        self.parser = argparse.ArgumentParser(exit_on_error=False)
        subparser = self.parser.add_subparsers()
        download_parser = subparser.add_parser("download", aliases=["dl"])
        quality_parser = subparser.add_parser("quality", aliases=["qa"])
        download_parser.add_argument("url", nargs='*', type=str)
        download_parser.add_argument("-c", "--codec",
                                     choices=["alac", "ec3", "aac", "aac-binaural", "aac-downmix", "aac-legacy", "ac3"],
                                     default="alac")
        download_parser.add_argument("-f", "--force", default=False, action="store_true")
        download_parser.add_argument("-b", "--batch", default=False, action="store_true")
        download_parser.add_argument("-l", "--language", default=it(Config).region.language, action="store")
        download_parser.add_argument("--include-participate-songs", default=False, dest="include", action="store_true")

        quality_parser.add_argument("url", nargs='*', type=str)
        quality_parser.add_argument("-i", "--invert", default=False, action="store_true")
        quality_parser.add_argument("--codec-id", default=True, action="store_false")
        quality_parser.add_argument("--codec", default=True, action="store_false")
        quality_parser.add_argument("--bitrate", default=True, action="store_false")
        quality_parser.add_argument("--average-bitrate", default=True, action="store_false")
        quality_parser.add_argument("--channels", default=True, action="store_false")
        quality_parser.add_argument("--sample-rate", default=True, action="store_false")
        quality_parser.add_argument("--bit-depth", default=True, action="store_false")
        quality_parser.add_argument("-b", "--batch", default=False, action="store_true")

        subparser.add_parser("status")
        subparser.add_parser("login")
        subparser.add_parser("logout")
        subparser.add_parser("help")
        subparser.add_parser("exit")
        subparser.add_parser("cl")
        subparser.add_parser("clear")

        self.batch_mode = False

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #
    async def show_status(self):
        it(WrapperClient).status.cache_invalidate()
        st_resp = await it(WrapperClient).status()
        regions = st_resp.get("regions", []) if isinstance(st_resp, dict) else []
        # Cache regions and full status for the TUI status bar / manager check.
        it(WrapperClient)._last_regions = regions  # type: ignore[attr-defined]
        it(WrapperClient)._last_status = st_resp  # type: ignore[attr-defined]
        if not regions:
            if await it(WrapperClient).supports_management():
                it(GlobalLogger).logger.error(
                    "wrapper-manager has no available account. Type `login` to sign in.")
            else:
                it(GlobalLogger).logger.error(
                    "The current wrapper instance has no available account. Please log in on the wrapper side "
                    "(e.g. run `lite --login user:pass`).")
        it(GlobalLogger).logger.info(f"Regions available on wrapper instance: {', '.join(regions)}")
    def _is_manager(self) -> bool:
        """Manager mode if locally configured OR the backend reports manager
        fields (works for remote instances too)."""
        local = getattr(it(Config).localInstance, "wrapperType", "manager")
        if local == "manager":
            return True
        data = getattr(it(WrapperClient), "_last_status", None)
        if isinstance(data, dict) and ("clientCount" in data or "ready" in data):
            return True
        return False

    async def _ask(self, prompt: str, password: bool = False) -> str:
        """Ask the user for one line of input.

        Which prompt is used follows the active UI:

        * full-screen TUI — a Textual modal screen, because the app owns the
          terminal (the old code built a second ``PromptSession`` inside the
          running app, so two frameworks fought over the same tty);
        * legacy REPL — ``input()`` / ``getpass`` on the plain terminal, owned
          by ``src.tui.legacy.LegacyConsole``;
        * headless — a bare stdlib read, so the flows stay scriptable.

        Cancellation always surfaces as ``EOFError``, which is what the
        ``except (EOFError, KeyboardInterrupt)`` guards around the login and
        logout flows already expect.
        """
        app = getattr(self, "_tui_app", None)
        if app is not None and getattr(app, "is_running", False):
            value = await app.ask_text(prompt, password=password)
            if value is None:
                raise EOFError("input cancelled")
            return value

        console = getattr(self, "_legacy_console", None)
        if console is not None:
            value = await console.read_line(prompt=prompt, password=password)
            if value is None:
                raise EOFError("input cancelled")
            return value

        if password:
            import getpass
            return await asyncio.to_thread(getpass.getpass, prompt)
        return await asyncio.to_thread(input, prompt)

    async def login_flow(self):
        """Login through the wrapper backend.

        - wrapper-manager: uses HTTP POST /login (with 2FA two-phase flow).
        - wrapper-lite:    login is not supported via this client; prints a
                           hint pointing at the lite one-shot login helper.
        """
        if not await it(WrapperClient).supports_management():
            it(GlobalLogger).logger.info(
                "This wrapper instance (lite or legacy) does not expose /login.\n"
                "Log in on the wrapper side instead (e.g. python qemu/login.py).")
            return
        try:
            username = await self._ask("Username: ")
            password = await self._ask("Password: ", password=True)
        except (EOFError, KeyboardInterrupt):
            it(GlobalLogger).logger.warning("Login cancelled.")
            return
        try:
            result = await it(WrapperClient).login(username, password)
        except WrapperError as e:
            # code 2 = 2FA code required.  The HTTP helper surfaces code 2 as
            # WrapperError with msg; detect by message content to prompt code.
            if "2fa" not in str(e).lower() and "code require" not in str(e).lower():
                it(GlobalLogger).logger.error(f"Login failed: {e}")
                return
            try:
                code = await self._ask("Two-step code: ")
            except (EOFError, KeyboardInterrupt):
                it(GlobalLogger).logger.warning("Login cancelled.")
                return
            try:
                result = await it(WrapperClient).login(username, password, code=code)
            except WrapperError as e2:
                it(GlobalLogger).logger.error(f"Login failed: {e2}")
                return
        it(GlobalLogger).logger.info("Login success.")
        it(WrapperClient).status.cache_invalidate()

    async def logout_flow(self):
        """Logout through wrapper-manager; unsupported on wrapper-lite."""
        if not await it(WrapperClient).supports_management():
            it(GlobalLogger).logger.info(
                "This wrapper instance (lite or legacy) does not expose /logout.\n"
                "Restart the wrapper instance or log out on the wrapper side.")
            return
        try:
            username = await self._ask("Username: ")
        except (EOFError, KeyboardInterrupt):
            it(GlobalLogger).logger.warning("Logout cancelled.")
            return
        try:
            await it(WrapperClient).logout(username)
        except WrapperError as e:
            it(GlobalLogger).logger.error(f"Logout failed: {e}")
            return
        it(GlobalLogger).logger.info("Logout success.")
        it(WrapperClient).status.cache_invalidate()

    async def handle_batch_mode(self, args, cmds):
        try:
            if args.batch:
                self.batch_mode = True
                self.batch_args = args
                self.batch_command = cmds[0]
                it(GlobalLogger).logger.info(
                    "Entering batch mode. Enter one or more URLs per line (space-separated), type 'exit' to quit")
        except Exception:
            pass

    async def batch_mode_parser(self, cmds: str):
        args = self.batch_args
        args.url = copy.deepcopy(cmds)
        if cmds[0] != "exit":
            cmds[0] = self.batch_command
        return cmds, args

    async def command_parser(self, cmd: str):
        if not cmd.strip():
            return
        cmds = cmd.split(" ")
        if self.batch_mode:
            cmds, args = await self.batch_mode_parser(cmds)
        else:
            try:
                args = self.parser.parse_args(cmds)
            except (argparse.ArgumentError, argparse.ArgumentTypeError, SystemExit):
                it(GlobalLogger).logger.warning(f"Unknown command: {cmd}")
                return
            await self.handle_batch_mode(args, cmds)
        match cmds[0]:
            case "download" | "dl":
                safely_create_task(self.do_download(args.url, args.codec, args.force, args.language, args.include))
            case "status":
                await self.show_status()
            case "login":
                await self.login_flow()
            case "logout":
                await self.logout_flow()
            case "help":
                self.print_help()
            case "exit":
                if self.batch_mode:
                    self.batch_mode = False
                    it(GlobalLogger).logger.info("Batch mode exited. Returning to normal command mode.")
                else:
                    await self.confirm_and_exit()
            case "cl" | "clear":
                await self.do_clear_tasks()
            case "quality" | "qa":
                safely_create_task(self.do_quality(args.url, args))

    def print_help(self):
        # The two UIs do not share a keyboard: the full-screen app owns Tab /
        # F-keys / mouse, while the legacy REPL leaves line editing to the
        # terminal and only understands Ctrl+C / Ctrl+D plus the '?' suffix.
        if self.legacy_ui:
            extra = (
                "Keys:\n"
                "  Ctrl+C / Ctrl+D  quit (press again within 5s when tasks are running)\n"
                "  <command> ?      list the words valid at that point, e.g. 'dl --codec ?';\n"
                "                   line editing and history come from your terminal\n"
            )
            cl_hint = "clear finished tasks"
        else:
            extra = (
                "Keys:\n"
                "  Tab         move focus: log <-> task sidebar <-> input\n"
                "  Ctrl+Left/Right  resize the task sidebar\n"
                "  Up/Down     scroll log (log focused) / command history (input focused)\n"
                "  End         re-enable log auto-follow after scrolling\n"
                "  F1          help panel (keys, commands, examples)\n"
                "  F2          cycle the log level floor: all -> WARNING+ -> ERROR+\n"
                "  F3          filter the log by substring; Esc clears it\n"
                "  F4          cycle the colour theme\n"
                "  F5/F6       expand / fold every group in the sidebar\n"
                "  F7          copy the highlighted task's name\n"
                "  Delete      remove the highlighted task, once it has finished\n"
                "  Ctrl+P      command palette\n"
                "  F10/Ctrl+C  quit (press again within 5s when tasks are running)\n"
                "  Ctrl+D      submit batch panel; Esc cancels it\n"
                "\n"
                "Mouse: click a pane to focus it, wheel scrolls. Tasks appear as a tree\n"
                "in the sidebar (album -> tracks); 'cl' prunes finished ones. Errors are\n"
                "also raised as a notification, so one that scrolls past is not missed."
            )
            cl_hint = "clear finished tasks from the sidebar"

        it(GlobalLogger).logger.info(
            "Commands:\n"
            "  dl <url...> [-c codec] [-f] [-b] [-l lang] [--include-participate-songs]  Download\n"
            "     -c codec    alac (default) / ec3 / ac3 / aac / aac-binaural /\n"
            "                 aac-downmix / aac-legacy\n"
            "     -f          overwrite existing files\n"
            "     -b          batch mode: opens the URL input panel\n"
            "     -l lang     metadata language, e.g. en-US, zh-Hant-HK, ja\n"
            "  qa <url...> [--invert] [--codec-id] [--codec] [--bitrate] ...   Show quality\n"
            "  status        wrapper status + available regions\n"
            f"  cl            {cl_hint}\n"
            "  login/logout  login is handled on the wrapper side\n"
            "  help          this help\n"
            "  exit          quit\n"
            "\n" + extra
        )

    async def do_clear_tasks(self):
        try:
            from src.tui.task_tree import TaskTree
            removed = it(TaskTree).clear_done()
            it(GlobalLogger).logger.info(f"Cleared {removed} finished task(s) from the sidebar.")
        except Exception as e:
            it(GlobalLogger).logger.warning(f"cl: {e}")

    async def retry_task(self, task) -> bool:
        """Run a finished task again from its recorded request.

        Returns whether it could be retried at all.  The work goes back through
        ``do_download`` rather than straight to the ripper, so a retry goes down
        exactly the same path as the original command — same URL parsing, same
        codec handling, same register / de-duplicate logic.
        """
        request = getattr(task, "request", None)
        if request is None:
            return False
        await self.do_download(
            [request.url], request.codec, request.force,
            request.language or it(Config).region.language, request.include,
        )
        return True

    async def do_download(self, raw_urls: list[str], codec: str, force_download: bool, language: str,
                          include: bool = False):
        for raw_url in raw_urls:
            url = AppleMusicURL.parse_url(raw_url)
            if not url:
                real_url = await it(WebAPI).get_real_url(raw_url)
                url = AppleMusicURL.parse_url(real_url)
                if not url:
                    it(GlobalLogger).logger.error(f"Illegal URL! - {raw_url}")
                    continue
            match url.type:
                case URLType.Song:
                    safely_create_task(
                        self.ripper.rip_song(url, codec, Flags(force_save=force_download, language=language)))
                case URLType.Album:
                    safely_create_task(
                        self.ripper.rip_album(url, codec, Flags(force_save=force_download, language=language)))
                case URLType.Artist:
                    safely_create_task(
                        self.ripper.rip_artist(url, codec, Flags(force_save=force_download, language=language,
                                                                 include_participate_in_works=include)))
                case URLType.Playlist:
                    safely_create_task(
                        self.ripper.rip_playlist(url, codec, Flags(force_save=force_download, language=language)))
                case URLType.MusicVideo:
                    safely_create_task(
                        self.ripper.rip_music_video(url, Flags(force_save=force_download, language=language)))
                case _:
                    it(GlobalLogger).logger.error(f"Unsupported URLType - {raw_url}")
                    continue

    async def do_quality(self, raw_urls: list[str], args):
        all_fields = list(key_to_Headers.keys())
        show_fields = []
        for field in all_fields:
            if args.invert:
                show_fields = [f for f in key_to_Headers if not getattr(args, f)]
            else:
                show_fields = [f for f in key_to_Headers if getattr(args, f)]

        for raw_url in raw_urls:
            url = AppleMusicURL.parse_url(raw_url)
            if not url:
                real_url = await it(WebAPI).get_real_url(raw_url)
                url = AppleMusicURL.parse_url(real_url)
                if not url:
                    it(GlobalLogger).logger.error(f"Illegal URL! - {raw_url}")
                    continue
            match url.type:
                case URLType.Song:
                    safely_create_task(print_song_quality(url, show_fields))
                case URLType.Album:
                    safely_create_task(print_album_quality(url, show_fields))
                case URLType.Playlist:
                    safely_create_task(print_playlist_quality(url, show_fields))
                case _:
                    it(GlobalLogger).logger.error(f"Unsupported URLType - {raw_url}")
                    continue

    # ------------------------------------------------------------------ #
    # UI plumbing
    # ------------------------------------------------------------------ #
    def bottom_toolbar(self):
        return f"Download Speed: {it(Measurer).download_speed()}, Decrypt Speed: {it(Measurer).decrypt_speed()}, Tasks: {it(Measurer).tasks_count()}"

    def completion_tree(self) -> dict:
        """Nested command grammar.

        Framework-independent: the full-screen TUI feeds it to its Textual
        suggester, and the legacy REPL walks it to answer a ``?`` query.
        """
        return {
            "dl": {
                "--batch": None,
                "--codec": {
                    "ec3": None,
                    "aac": None,
                    "alac": None,
                    "aac-binaural": None,
                    "aac-downmix": None,
                    "aac-legacy": None,
                    "ac3": None
                },
                "--force": None,
                "--language": {
                    "en-US": None,
                    "en-GB": None,
                    "zh-Hans-CN": None,
                    "zh-Hant-HK": None,
                    "zh-Hant-TW": None,
                    "ja": None,
                    "ko": None
                },
                "--include-participate-songs": None
            },
            "qa": {
                "--invert": None,
                "--codec-id": None,
                "--codec": None,
                "--bitrate": None,
                "--average-bitrate": None,
                "--channels": None,
                "--sample-rate": None,
                "--bit-depth": None,
                "--batch": None
            },
            "status": None,
            "login": None,
            "logout": None,
            "help": None,
            "exit": None,
            "cl": None,
            "clear": None,
        }

    async def confirm_and_exit(self):
        """Two-step confirm: first press logs a warning; pressing again
        within 5 seconds exits.  Avoids any blocking text prompt inside
        the TUI event loop."""
        import time
        now = time.monotonic()
        last = getattr(self, "_exit_warn_at", 0.0)
        if it(Measurer).tasks_count() > 0 and now - last > 5:
            self._exit_warn_at = now
            it(GlobalLogger).logger.warning(
                f"There are still {it(Measurer).tasks_count()} running tasks — "
                f"press exit again within 5s to confirm.")
            return
        self.handle_exit()

    def handle_exit(self):
        it(GlobalLogger).logger.info("Exit.")
        # In the full-screen TUI, ask Textual to exit normally so it restores
        # the terminal (alternate screen, raw mode, mouse tracking).  os._exit
        # would skip that cleanup and leave the terminal broken (wheel / input
        # garbage) after quitting.
        app = getattr(self, "_tui_app", None)
        if app is not None and app.is_running:
            app.exit()
            return
        # Legacy REPL: raise a private exception so run_legacy_repl() unwinds
        # from the read loop cleanly.  loop.stop() here would abort the event
        # loop mid-frame.
        raise _LegacyExit()

    async def start(self):
        """Entry point — legacy REPL or full-screen TUI."""
        if self.legacy_ui:
            from src.tui.legacy import run_legacy_repl
            await run_legacy_repl(self)
            return
        from src.tui.app import run_tui
        await run_tui(self)

    async def start_legacy(self):
        """Deprecated alias — kept for callers that used the old name.

        The implementation moved to :func:`src.tui.legacy.run_legacy_repl`.
        """
        from src.tui.legacy import run_legacy_repl
        await run_legacy_repl(self)