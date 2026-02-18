import asyncio
import base64
import json
import zipfile
from io import BytesIO
from pathlib import Path

import httpx
from creart import it
from tenacity import retry, retry_if_exception_type, wait_random_exponential, stop_after_attempt, before_sleep_log

from src.config import Config
from src.logger import GlobalLogger

ARGUMENTS = ["qemu-system-x86_64", "-machine q35", f"-cpu {it(Config).localInstance.cpuModel}",
             f"-m {it(Config).localInstance.memorySize}", "-hda assets/wrapper-manager.qcow2",
             "-device virtio-net-pci,netdev=net0",
             "-chardev socket,id=qga0,host=127.0.0.1,port=32766,server=on,wait=off",
             "-device virtio-serial-pci",
             "-device virtserialport,chardev=qga0,name=org.qemu.guest_agent.0",
             "-netdev user,id=net0,hostfwd=tcp:127.0.0.1:32767-:32767"]
HWACCEL = f"-accel {it(Config).localInstance.hardwareAccelerator}"


class QGAException(Exception):
    msg: str

    def __init__(self, msg: str):
        self.msg = msg


class QemuCrashedException(Exception):
    msg: str

    def __init__(self, stdout: str, stderr: str):
        self.msg = stdout + "\n" + stderr


class QGAClient:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    @retry(retry=retry_if_exception_type(asyncio.TimeoutError),
           wait=wait_random_exponential(multiplier=1, max=it(Config).download.maxWaitTime),
           stop=stop_after_attempt(8), before_sleep=before_sleep_log(it(GlobalLogger).logger, "WARNING"))
    async def init(self):
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", 32766)

    async def ping(self):
        return await self.send_cmd("guest-ping", {})

    async def send_cmd(self, command: str, arguments: dict):
        self.writer.write(json.dumps({"execute": command, "arguments": arguments}).encode())
        result = json.loads(await self.reader.readline())
        if result.get("error"):
            raise QGAException(result.get("error"))
        else:
            return result.get("return")

    async def read_file(self, path: str):
        fp = await self.send_cmd("guest-file-open", {"path": path})
        raw_result = await self.send_cmd("guest-file-read", {"handle": fp, "count": 48000000})
        result = base64.standard_b64decode(raw_result["buf-b64"]).decode()
        await self.send_cmd("guest-file-close", {"handle": fp})
        return result

    async def write_file(self, path: str, content: str):
        fp = await self.send_cmd("guest-file-open", {"path": path, "mode": "w"})
        await self.send_cmd("guest-file-write",
                            {"handle": fp, "buf-b64": base64.standard_b64encode(content.encode()).decode()})
        await self.send_cmd("guest-file-close", {"handle": fp})

    async def execute(self, path: str, args: list[str]):
        return await self.send_cmd("guest-exec", {"path": path, "arg": args})


class QemuInstance:
    proc = None
    client = QGAClient()

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
        it(GlobalLogger).logger.info("Waiting for wrapper-manager to start...")
        try:
            await self.client.init()
            await self.client.ping()
        except ConnectionError:
            stdout, stderr = await self.proc.result().communicate()
            raise QemuCrashedException(stdout.decode(), stderr.decode())
        await self.client.write_file("/etc/wm-args", it(Config).localInstance.startArgs)
        await self.client.execute("/sbin/rc-service", ["wrapper-manager", "start"])
        while True:
            if await self.instance_running():
                break
            await asyncio.sleep(1)

    def qemu_running(self):
        if self.proc.done():
            return not bool(self.proc.result().returncode)
        else:
            return True

    async def instance_running(self):
        try:
            await self.client.read_file("/var/run/wrapper-manager/wrapper-manager.pid")
            return True
        except QGAException:
            return False

    async def terminate(self):
        await self.client.execute("/sbin/poweroff", [])

    async def logs(self):
        return await self.client.read_file("/var/run/wrapper-manager/wrapper-manager.log")

    async def get_instance_image(self):
        it(GlobalLogger).logger.warning("The wrapper-manager image does not exist. Downloading...")
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(
                "https://nightly.link/WorldObservationLog/wrapper-manager/workflows/wrapper-manager-image/main/wrapper-manager-image.zip")
            with zipfile.ZipFile(BytesIO(resp.content), "r") as f:
                f.extractall("assets/")

    def image_available(self):
        return Path("assets/wrapper-manager.qcow2").exists()
