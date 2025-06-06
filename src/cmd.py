import argparse
import asyncio
import sys

from creart import it
from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from src.api import WebAPI
from src.config import Config
from src.flags import Flags
from src.grpc.manager import WrapperManager
from src.rip import on_decrypt_success, on_decrypt_failed, rip_song, rip_album, rip_artist, rip_playlist
from src.url import AppleMusicURL, URLType
from src.utils import check_dep


class InteractiveShell:
    loop: asyncio.AbstractEventLoop
    parser: argparse.ArgumentParser

    def __init__(self, loop: asyncio.AbstractEventLoop):
        dep_installed, missing_dep = check_dep()
        if not dep_installed:
            logger.error(f"Dependence {missing_dep} was not installed!")
            loop.stop()
            sys.exit()

        self.loop = loop
        loop.run_until_complete(it(WrapperManager).init(it(Config).instance))
        loop.create_task(it(WrapperManager).decrypt_init(on_success=on_decrypt_success, on_failure=on_decrypt_failed))

        self.parser = argparse.ArgumentParser(exit_on_error=False)
        subparser = self.parser.add_subparsers()
        download_parser = subparser.add_parser("download", aliases=["dl"])
        download_parser.add_argument("url", type=str)
        download_parser.add_argument("-c", "--codec",
                                     choices=["alac", "ec3", "aac", "aac-binaural", "aac-downmix", "ac3"],
                                     default="alac")
        download_parser.add_argument("-f", "--force", default=False, action="store_true")
        download_parser.add_argument("--include-participate-songs", default=False, dest="include", action="store_true")

        subparser.add_parser("exit")

    async def command_parser(self, cmd: str):
        if not cmd.strip():
            return
        cmds = cmd.split(" ")
        try:
            args = self.parser.parse_args(cmds)
        except (argparse.ArgumentError, argparse.ArgumentTypeError, SystemExit):
            logger.warning(f"Unknown command: {cmd}")
            return
        match cmds[0]:
            case "download" | "dl":
                await self.do_download(args.url, args.codec, args.force, args.include)
            case "exit":
                self.loop.stop()
                sys.exit()

    async def do_download(self, raw_url: str, codec: str, force_download: bool, include: bool = False):
        url = AppleMusicURL.parse_url(raw_url)
        if not url:
            real_url = await it(WebAPI).get_real_url(raw_url)
            url = AppleMusicURL.parse_url(real_url)
            if not url:
                logger.error("Illegal URL!")
                return
        match url.type:
            case URLType.Song:
                self.loop.create_task(
                    rip_song(url, codec, Flags(force_save=force_download)))
            case URLType.Album:
                self.loop.create_task(rip_album(url, codec, ))
            case URLType.Artist:
                self.loop.create_task(
                    rip_artist(url, codec, Flags(force_save=force_download, include_participate_in_works=include)))
            case URLType.Playlist:
                self.loop.create_task(rip_playlist(url, codec, Flags(force_save=force_download)))
            case _:
                logger.error("Unsupported URLType")
                return

    async def handle_command(self):
        session = PromptSession("> ")

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
                logger.info("Existing shell")
