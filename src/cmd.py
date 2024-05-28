import argparse
import asyncio
import random
import sys
from asyncio import Task
from typing import Literal

from loguru import logger
from prompt_toolkit import PromptSession, print_formatted_text, ANSI
from prompt_toolkit.patch_stdout import patch_stdout

from src.adb import Device
from src.api import get_token, init_client_and_lock, upload_m3u8_to_api, get_song_info, get_real_url
from src.config import Config
from src.rip import rip_song, rip_album, rip_artist, rip_playlist
from src.status import LogStatus, BaseStatus
from src.types import GlobalAuthParams
from src.url import AppleMusicURL, URLType, Song
from src.utils import get_song_id_from_m3u8


class NewInteractiveShell:
    loop: asyncio.AbstractEventLoop
    config: Config
    tasks: list[Task] = []
    devices: list[Device] = []
    storefront_device_mapping: dict[str, list[Device]] = {}
    anonymous_access_token: str
    parser: argparse.ArgumentParser

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.config = Config.load_from_config()
        init_client_and_lock(self.config.download.proxy, self.config.download.parallelNum)
        self.anonymous_access_token = loop.run_until_complete(get_token())

        self.parser = argparse.ArgumentParser(exit_on_error=False)
        subparser = self.parser.add_subparsers()
        download_parser = subparser.add_parser("download", aliases=["dl"])
        download_parser.add_argument("url", type=str)
        download_parser.add_argument("-c", "--codec",
                                     choices=["alac", "ec3", "aac", "aac-binaural", "aac-downmix", "ac3"],
                                     default="alac")
        download_parser.add_argument("-f", "--force", default=False, action="store_true")
        download_parser.add_argument("--include-participate-songs", default=False, dest="include", action="store_true")
        download_from_file_parser = subparser.add_parser("download-from-file", aliases=["dlf"])
        download_from_file_parser.add_argument("file", type=str)
        download_from_file_parser.add_argument("-f", "--force", default=False, action="store_true")
        download_from_file_parser.add_argument("-c", "--codec",
                                     choices=["alac", "ec3", "aac", "aac-binaural", "aac-downmix", "ac3"],
                                     default="alac")
        m3u8_parser = subparser.add_parser("m3u8")
        m3u8_parser.add_argument("url", type=str)
        m3u8_parser.add_argument("-c", "--codec",
                                 choices=["alac", "ec3", "aac", "aac-binaural", "aac-downmix", "ac3"],
                                 default="alac")
        m3u8_parser.add_argument("-f", "--force", default=False, action="store_true")
        subparser.add_parser("exit")

        logger.remove()
        logger.add(lambda msg: print_formatted_text(ANSI(msg), end=""), colorize=True, level="INFO")

        for device_info in self.config.devices:
            device = Device(su_method=device_info.suMethod)
            device.connect(device_info.host, device_info.port)
            logger.info(f"Device {device_info.host}:{device_info.port} has connected")
            self.devices.append(device)
            auth_params = device.get_auth_params()
            if not self.storefront_device_mapping.get(auth_params.storefront.lower()):
                self.storefront_device_mapping.update({auth_params.storefront.lower(): []})
            self.storefront_device_mapping[auth_params.storefront.lower()].append(device)
            if device_info.hyperDecrypt:
                device.hyper_decrypt(list(range(device_info.agentPort, device_info.agentPort + device_info.hyperDecryptNum)))
            else:
                device.start_inject_frida(device_info.agentPort)

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
            case "m3u8":
                await self.do_m3u8(args.url, args.codec, args.force)
            case "download-from-file" | "dlf":
                await self.do_download_from_file(args.file, args.codec, args.force)
            case "exit":
                self.loop.stop()
                sys.exit()

    async def do_download(self, raw_url: str, codec: str, force_download: bool, include: bool = False):
        url = AppleMusicURL.parse_url(raw_url)
        if not url:
            real_url = await get_real_url(raw_url)
            url = AppleMusicURL.parse_url(real_url)
            if not url:
                logger.error("Illegal URL!")
                return
        available_device = await self._get_available_device(url.storefront)
        global_auth_param = GlobalAuthParams.from_auth_params_and_token(available_device.get_auth_params(),
                                                                        self.anonymous_access_token)
        match url.type:
            case URLType.Song:
                status = LogStatus(status_type=URLType.Song)
                task = self.loop.create_task(
                    rip_song(url, global_auth_param, codec, self.config, available_device, status,
                             force_save=force_download))
            case URLType.Album:
                status = LogStatus(status_type=URLType.Album)
                task = self.loop.create_task(
                    rip_album(url, global_auth_param, codec, self.config, available_device, status,
                              force_save=force_download))
            case URLType.Artist:
                status = LogStatus(status_type=URLType.Artist)
                task = self.loop.create_task(
                    rip_artist(url, global_auth_param, codec, self.config, available_device, status,
                               force_save=force_download, include_participate_in_works=include))
            case URLType.Playlist:
                status = LogStatus(status_type=URLType.Playlist)
                task = self.loop.create_task(
                    rip_playlist(url, global_auth_param, codec, self.config, available_device, status,
                                 force_save=force_download))
            case _:
                logger.error("Unsupported URLType")
                return
        self.tasks.append(task)
        task.add_done_callback(self.tasks.remove)

    async def do_m3u8(self, m3u8_url: str, codec: str, force_download: bool):
        song_id = get_song_id_from_m3u8(m3u8_url)
        song = Song(id=song_id, storefront=self.config.region.defaultStorefront, url="", type=URLType.Song)
        available_device = await self._get_available_device(self.config.region.defaultStorefront)
        global_auth_param = GlobalAuthParams.from_auth_params_and_token(available_device.get_auth_params(),
                                                                        self.anonymous_access_token)
        self.loop.create_task(
            rip_song(song, global_auth_param, codec, self.config, available_device, force_save=force_download,
                     specified_m3u8=m3u8_url)
        )

    async def do_download_from_file(self, file: str, codec: str, force_download: bool):
        with open(file, "r", encoding="utf-8") as f:
            urls = f.readlines()
        for url in urls:
            task = self.loop.create_task(self.do_download(raw_url=url, codec=codec, force_download=force_download))
            self.tasks.append(task)
            task.add_done_callback(self.tasks.remove)

    async def _get_available_device(self, storefront: str):
        devices = self.storefront_device_mapping.get(storefront)
        if not devices:
            logger.warning(f"No device is available to decrypt the specified region: {storefront.upper()}. "
                           f"Use storefront {self.config.region.defaultStorefront.upper()} to decrypt")
            storefront = self.config.region.defaultStorefront
            devices = self.storefront_device_mapping.get(storefront)
        available_devices = [device for device in devices if not device.decryptLock.locked()]
        if not available_devices:
            available_device: Device = random.choice(devices)
        else:
            available_device: Device = random.choice(available_devices)
        return available_device

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
