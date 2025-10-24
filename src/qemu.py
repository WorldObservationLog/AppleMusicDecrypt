import asyncio
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import httpx
from creart import it

from src.config import Config
from src.logger import GlobalLogger

ARGUMENTS = ["qemu-system-x86_64", "-machine q35", f"-cpu {it(Config).localInstance.cpuModel}",
             f"-m {it(Config).localInstance.memorySize}", "-hda assets/wrapper-manager.qcow2",
             "-device virtio-net-pci,netdev=net0", "-netdev user,id=net0,hostfwd=tcp:127.0.0.1:32767-:32767"]
HWACCEL = f"-accel {it(Config).localInstance.hardwareAccelerator}"


class QemuInstance:
    proc = None

    async def launch_instance(self, loop: asyncio.AbstractEventLoop):
        if not self.image_available():
            await self.get_instance_image()
        if it(Config).localInstance.enableHardwareAcceleration:
            ARGUMENTS.insert(3, HWACCEL)
        if not it(Config).localInstance.showWindow:
            ARGUMENTS.insert(5, "-display none")
        self.proc = loop.create_task(
            asyncio.create_subprocess_shell(" ".join(ARGUMENTS), stdout=asyncio.subprocess.PIPE,
                                            stderr=asyncio.subprocess.PIPE))

    def terminate(self):
        self.proc.result().kill()

    def running(self):
        if self.proc.done():
            return not bool(self.proc.result().returncode)
        else:
            return True

    async def get_instance_image(self):
        it(GlobalLogger).logger.warning("The wrapper-manager image does not exist. Downloading...")
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(
                "https://nightly.link/WorldObservationLog/wrapper-manager/workflows/wrapper-manager-image/main/wrapper-manager-image.zip")
            with zipfile.ZipFile(BytesIO(resp.content), "r") as f:
                f.extractall("assets/")

    def image_available(self):
        return Path("assets/wrapper-manager.qcow2").exists()
