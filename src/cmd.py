import argparse
import asyncio
import copy
import os
import sys

from creart import it
from prompt_toolkit.completion import NestedCompleter

from src.api import WebAPI
from src.config import Config
from src.flags import Flags
from src.logger import GlobalLogger
from src.measurer import Measurer
from src.mv import MVRipper
from src.qemu import QemuInstance
from src.quality import print_song_quality, print_album_quality, print_playlist_quality, key_to_Headers
from src.rip import Ripper
from src.status import status_panel
from src.url import AppleMusicURL, URLType
from src.utils import check_dep, run_sync, safely_create_task, config_outdated
from src.wrapper import WrapperClient, WrapperError


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

        self.loop = loop
        loop.run_until_complete(run_sync(it(WebAPI).init))
        if it(Config).localInstance.enable:
            loop.run_until_complete(self.localInstance.launch_instance(loop))
            it(Config).instance.url = f"127.0.0.1:{it(Config).localInstance.hostPort}"
            it(Config).instance.secure = False
            # First access to WrapperClient reads the (mutated) config.
            loop.run_until_complete(it(WrapperClient).init())
            it(WrapperClient).status.cache_invalidate()
            if not loop.run_until_complete(it(WrapperClient).status()).get("regions"):
                # No account logged in on the wrapper side: the app cannot
                # rip anything.  Point the user at qemu/login.py and exit.
                it(GlobalLogger).logger.error(
                    "No Apple account is logged in on the wrapper instance.\n"
                    "Run  python qemu/login.py  to log in, then start the app again.")
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
        # Cache regions for the TUI status bar (non-blocking read).
        it(WrapperClient)._last_regions = regions  # type: ignore[attr-defined]
        if not regions:
            it(GlobalLogger).logger.error(
                "The current wrapper instance has no available account. Please log in on the wrapper side "
                "(e.g. run `lite --login user:pass`).")
        it(GlobalLogger).logger.info(f"Regions available on wrapper instance: {', '.join(regions)}")
        # live task panel
        tasks = list(self.ripper.download_manager.adam_id_task_mapping.values())
        it(GlobalLogger).logger.info(
            f"Active tasks ({len(tasks)}):\n" + status_panel(tasks))

    async def login_flow(self):
        it(GlobalLogger).logger.info(
            "Login is handled by the wrapper-lite instance itself, not by this client.\n"
            "For a local wrapper-lite run:  lite --login <user:pass>  (add --code-from-file for 2FA).\n"
            "For a remote instance, ask its administrator to add your account.")

    async def logout_flow(self):
        it(GlobalLogger).logger.info(
            "Logout is managed on the wrapper side. Restart the wrapper-lite instance to drop its session.")

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
            "  cl            clear finished tasks from the sidebar\n"
            "  login/logout  login is handled on the wrapper side\n"
            "  help          this help\n"
            "  exit          quit\n"
            "\n"
            "Keys:\n"
            "  Tab         move focus (log pane <-> input bar)\n"
            "  Up/Down     scroll log (log focused) / command history (input focused)\n"
            "  End         re-enable log auto-follow after scrolling\n"
            "  F1          this help\n"
            "  F10/Ctrl+C  quit (press again within 5s when tasks are running)\n"
            "  Ctrl+D      submit batch panel; Esc cancels it\n"
            "\n"
            "Mouse: click a pane to focus it, wheel scrolls. Tasks appear as a tree\n"
            "in the sidebar (album -> tracks); 'cl' prunes finished ones.")

    async def do_clear_tasks(self):
        try:
            from src.tui.task_tree import TaskTree
            removed = it(TaskTree).clear_done()
            it(GlobalLogger).logger.info(f"Cleared {removed} finished task(s) from the sidebar.")
        except Exception as e:
            it(GlobalLogger).logger.warning(f"cl: {e}")

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
                    safely_create_task(MVRipper().rip(url))
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

    def completer(self):
        mycompleter = {
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
        return NestedCompleter.from_nested_dict(mycompleter)

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
        self.loop.stop()
        os._exit(0)

    async def start(self):
        """Entry point — legacy REPL or full-screen TUI."""
        if self.legacy_ui:
            await self.start_legacy()
            return
        from src.tui.app import run_tui
        await run_tui(self)

    async def start_legacy(self):
        """v2-style REPL (PromptSession) for simple terminals.

        Mirrors the v2 UI: a plain "> " prompt with a bottom speed
        toolbar and Tab completion; no full-screen alternate buffer.
        """
        from prompt_toolkit import PromptSession
        from prompt_toolkit.patch_stdout import patch_stdout
        from prompt_toolkit.input.defaults import create_input

        # Reset the terminal to a sane state before entering the REPL:
        # startup network/log code can leave stdin in an inconsistent
        # termios mode on Termux/proot.  An explicit input object also
        # prevents prompt_toolkit from silently falling back to DummyInput.
        try:
            import termios
            import sys as _sys
            fd = _sys.stdin.fileno()
            termios.tcsetattr(fd, termios.TCSANOW, termios.tcgetattr(fd))
        except Exception:
            pass
        _legacy_input = create_input()
        it(GlobalLogger).logger.info(f"Legacy REPL input: {type(_legacy_input).__name__}")

        # Keep session creation inside patch_stdout exactly like v2: the
        # stdout proxy must be active before PromptSession captures stdout,
        # otherwise Termux/proot input can end up detached from the renderer.
        with patch_stdout():
            session = PromptSession(
                "> ",
                bottom_toolbar=self.bottom_toolbar,
                completer=self.completer(),
                refresh_interval=1,
                input=_legacy_input,
            )
            try:
                while True:
                    try:
                        command = await session.prompt_async()
                    except (EOFError, KeyboardInterrupt):
                        self.handle_exit()
                        return
                    if command.strip() == '':
                        continue
                    await self.command_parser(command)
            finally:
                if it(Config).localInstance.enable:
                    self.localInstance.terminate()