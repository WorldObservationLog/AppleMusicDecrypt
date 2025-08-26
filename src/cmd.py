import argparse
import asyncio
import sys

from creart import it
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from src.api import WebAPI
from src.config import Config
from src.flags import Flags
from src.grpc.manager import WrapperManager
from src.logger import GlobalLogger
from src.measurer import SpeedMeasurer
from src.rip import on_decrypt_success, on_decrypt_failed, rip_song, rip_album, rip_artist, rip_playlist
from src.url import AppleMusicURL, URLType
from src.utils import check_dep, run_sync, safely_create_task, get_tasks_num


class InteractiveShell:
    loop: asyncio.AbstractEventLoop
    parser: argparse.ArgumentParser

    def __init__(self, loop: asyncio.AbstractEventLoop):
        dep_installed, missing_dep = check_dep()
        if not dep_installed:
            it(GlobalLogger).logger.error(f"Dependence {missing_dep} was not installed!")
            loop.stop()
            sys.exit()

        self.loop = loop
        loop.run_until_complete(run_sync(it(WebAPI).init))
        loop.run_until_complete(it(WrapperManager).init(it(Config).instance.url, it(Config).instance.secure))
        safely_create_task(it(WrapperManager).decrypt_init(on_success=on_decrypt_success, on_failure=on_decrypt_failed))
        loop.run_until_complete(self.show_status())

        self.parser = argparse.ArgumentParser(exit_on_error=False)
        subparser = self.parser.add_subparsers()
        download_parser = subparser.add_parser("download", aliases=["dl"])
        download_parser.add_argument("url", type=str)
        download_parser.add_argument("-c", "--codec",
                                     choices=["alac", "ec3", "aac", "aac-binaural", "aac-downmix", "aac-legacy", "ac3"],
                                     default="alac")
        download_parser.add_argument("-f", "--force", default=False, action="store_true")
        download_parser.add_argument("-l", "--language", default=it(Config).region.language, action="store")
        download_parser.add_argument("--include-participate-songs", default=False, dest="include", action="store_true")

        subparser.add_parser("status")
        subparser.add_parser("exit")

    async def show_status(self):
        st_resp = await it(WrapperManager).status()
        it(GlobalLogger).logger.info(f"Regions available on wrapper-manager instance: {', '.join(st_resp.regions)}")

    async def command_parser(self, cmd: str):
        if not cmd.strip():
            return
        cmds = cmd.split(" ")
        try:
            args = self.parser.parse_args(cmds)
        except (argparse.ArgumentError, argparse.ArgumentTypeError, SystemExit):
            it(GlobalLogger).logger.warning(f"Unknown command: {cmd}")
            return
        match cmds[0]:
            case "download" | "dl":
                await self.do_download(args.url, args.codec, args.force, args.language, args.include)
            case "status":
                await self.show_status()
            case "exit":
                self.loop.stop()
                sys.exit()

    async def do_download(self, raw_url: str, codec: str, force_download: bool, language: str, include: bool = False):
        url = AppleMusicURL.parse_url(raw_url)
        if not url:
            real_url = await it(WebAPI).get_real_url(raw_url)
            url = AppleMusicURL.parse_url(real_url)
            if not url:
                it(GlobalLogger).logger.error("Illegal URL!")
                return
        match url.type:
            case URLType.Song:
                safely_create_task(rip_song(url, codec, Flags(force_save=force_download, language=language)))
            case URLType.Album:
                safely_create_task(rip_album(url, codec, Flags(force_save=force_download, language=language)))
            case URLType.Artist:
                safely_create_task(rip_artist(url, codec, Flags(force_save=force_download, language=language,
                                                                include_participate_in_works=include)))
            case URLType.Playlist:
                safely_create_task(rip_playlist(url, codec, Flags(force_save=force_download, language=language)))
            case _:
                it(GlobalLogger).logger.error("Unsupported URLType")
                return

    def bottom_toolbar(self):
        return f"Download Speed: {it(SpeedMeasurer).download_speed()}, Decrypt Speed: {it(SpeedMeasurer).decrypt_speed()}, Tasks: {get_tasks_num()}"

    async def handle_command(self):
        session = PromptSession("> ", bottom_toolbar=self.bottom_toolbar, refresh_interval=1)

        while True:
            try:
                command = await session.prompt_async()
                await self.command_parser(command)
            except (EOFError, KeyboardInterrupt):
                return

    async def start(self):
        with patch_stdout():
            try:
                await self.handle_command()
            finally:
                it(GlobalLogger).logger.info("Exit.")
