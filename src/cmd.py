import argparse
import asyncio
import random
import sys
from asyncio import Task

from loguru import logger
from prompt_toolkit import PromptSession, print_formatted_text, ANSI
from prompt_toolkit.patch_stdout import patch_stdout

from src.adb import Device
from src.api import get_token
from src.config import Config
from src.rip import rip_song, rip_album
from src.types import GlobalAuthParams
from src.url import AppleMusicURL, URLType


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
        self.anonymous_access_token = loop.run_until_complete(get_token())

        self.parser = argparse.ArgumentParser(exit_on_error=False)
        subparser = self.parser.add_subparsers()
        download_parser = subparser.add_parser("download")
        download_parser.add_argument("url", type=str)
        download_parser.add_argument("-c", "--codec",
                                     choices=["alac", "ec3", "aac", "aac-binaural", "aac-downmix", "ac3"],
                                     default="alac")
        download_parser.add_argument("-f", "--force", type=bool, default=False)
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
            device.start_inject_frida(device_info.agentPort)

    async def command_parser(self, cmd: str):
        if not cmd.strip():
            return
        cmds = cmd.split(" ")
        try:
            args = self.parser.parse_args(cmds)
        except argparse.ArgumentError:
            logger.warning(f"Unknown command: {cmd}")
            return
        match cmds[0]:
            case "download":
                await self.do_download(args.url, args.codec, args.force)
            case "exit":
                self.loop.stop()
                sys.exit()

    async def do_download(self, raw_url: str, codec: str, force_download: bool):
        url = AppleMusicURL.parse_url(raw_url)
        devices = self.storefront_device_mapping.get(url.storefront)
        if not devices:
            logger.error(f"No device is available to decrypt the specified region: {url.storefront}")
        available_devices = [device for device in devices if not device.decryptLock.locked()]
        if not available_devices:
            available_device: Device = random.choice(devices)
        else:
            available_device: Device = random.choice(available_devices)
        global_auth_param = GlobalAuthParams.from_auth_params_and_token(available_device.get_auth_params(),
                                                                        self.anonymous_access_token)
        match url.type:
            case URLType.Song:
                task = self.loop.create_task(
                    rip_song(url, global_auth_param, codec, self.config, available_device, force_download))
            case URLType.Album:
                task = self.loop.create_task(rip_album(url, global_auth_param, codec, self.config, available_device))

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
