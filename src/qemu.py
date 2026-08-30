"""Local wrapper-lite launcher (QEMU).

Brings up a local wrapper-lite instance by booting the wrapper-lite QEMU guest
(kernel + initramfs + data.img) and port-forwarding its HTTP port to the host.
This mirrors the wrapper repo's ``wrapper-lite-qemu`` launcher:

    qemu-system-x86_64 -accel <accel> -cpu <cpu> -m <mem> -smp <smp>
        -kernel <assetDir>/vmlinuz-lite-qemu
        -initrd <assetDir>/lite-initramfs.cpio.gz
        -append "console=ttyS0 quiet net.ifnames=0 biosdevname=0 [lite_args_b64=...]"
        -display none -serial stdio -no-reboot
        -nic user,model=e1000,hostfwd=tcp:127.0.0.1:<hostPort>-:<guestPort>
        -drive file=<assetDir>/data.img,format=raw,if=virtio
        [-fw_cfg name=lite_args,file=<argsFile>]

The guest's init script mounts /data, reads the forwarded lite args, and runs
``/system/bin/lite`` automatically — no guest agent is involved.
"""

import asyncio
import base64
import os
import shlex
import shutil
import sys
from pathlib import Path

import httpx
from creart import it

from src.config import Config
from src.logger import GlobalLogger


class QemuCrashedException(Exception):
    def __init__(self, msg: str):
        self.msg = msg
        super().__init__(msg)


def _qemu_binary(cfg) -> str:
    if cfg.qemuBin:
        return cfg.qemuBin
    name = "qemu-system-x86_64.exe" if os.name == "nt" else "qemu-system-x86_64"
    return shutil.which(name) or name


def _auto_accel(cfg) -> str:
    if cfg.hardwareAccelerator:
        return cfg.hardwareAccelerator
    if not cfg.enableHardwareAcceleration:
        return "tcg"
    if sys.platform == "darwin":
        return "tcg"  # HVF cannot accelerate x86_64 guests on Apple Silicon
    if os.name == "nt":
        return "whpx"
    # Linux: use KVM only if /dev/kvm is accessible.
    return "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg"


def _cpu_for_accel(accel: str) -> str:
    return {"kvm": "host", "whpx": "qemu64-v1"}.get(accel, "max")


def _lite_arg_lines(start_args: str) -> list[str]:
    """Split ``startArgs`` into one argument per line.

    If it looks like a single shell command line (spaces, no newlines), use
    shlex; otherwise treat each line as one argument.
    """
    if not start_args.strip():
        return []
    if "\n" not in start_args:
        return shlex.split(start_args)
    return [line for line in start_args.splitlines() if line.strip()]


def build_qemu_args(cfg) -> list[str]:
    """Build the qemu-system-x86_64 command line for the wrapper-lite guest."""
    accel = _auto_accel(cfg)
    asset = Path(cfg.assetDir)
    args = [
        _qemu_binary(cfg),
        "-accel", "whpx,kernel-irqchip=off" if accel == "whpx" else accel,
        "-cpu", cfg.cpuModel or _cpu_for_accel(accel),
        "-m", cfg.memorySize,
        "-smp", str(cfg.smp),
        "-kernel", str(asset / "vmlinuz-lite-qemu"),
        "-initrd", str(asset / "lite-initramfs.cpio.gz"),
    ]
    append = "console=ttyS0 quiet net.ifnames=0 biosdevname=0"
    arg_lines = _lite_arg_lines(cfg.startArgs)
    args_file = None
    if arg_lines:
        content = "\n".join(arg_lines) + "\n"
        append += " lite_args_b64=" + base64.b64encode(content.encode()).decode()
        args_file = asset / ".lite-qemu-args"
        args_file.write_text(content, encoding="utf-8")
    args += ["-append", append]
    if not cfg.showWindow:
        args += ["-display", "none"]
    args += ["-serial", "stdio", "-no-reboot"]
    args += ["-nic", f"user,model=e1000,hostfwd=tcp:127.0.0.1:{cfg.hostPort}-:{cfg.guestPort}"]
    args += ["-drive", f"file={asset / 'data.img'},format=raw,if=virtio"]
    if args_file is not None and args_file.exists():
        args += ["-fw_cfg", f"name=lite_args,file={args_file}"]
    return args


class QemuInstance:
    proc = None

    @staticmethod
    def assets_ready(cfg) -> bool:
        d = Path(cfg.assetDir)
        return (d / "vmlinuz-lite-qemu").exists() \
            and (d / "lite-initramfs.cpio.gz").exists() \
            and (d / "data.img").exists()

    @staticmethod
    def _asset_help(cfg) -> str:
        return (
            "wrapper-lite QEMU assets are missing in "
            f"'{cfg.assetDir}'. Build them from the wrapper repository:\n"
            "  git clone https://github.com/WorldObservationLog/wrapper\n"
            "  cd wrapper && apt-get download busybox-static\n"
            "  sudo ./qemu/build.sh      # produces qemu/vmlinuz-lite-qemu,\n"
            "                            # qemu/lite-initramfs.cpio.gz, qemu/data.img\n"
            f"then place those three files in '{cfg.assetDir}'."
        )

    async def launch_instance(self, loop: asyncio.AbstractEventLoop):
        cfg = it(Config).localInstance
        if not self.assets_ready(cfg):
            raise QemuCrashedException(self._asset_help(cfg))

        args = build_qemu_args(cfg)
        it(GlobalLogger).logger.info(
            f"Launching wrapper-lite QEMU guest (port {cfg.hostPort} -> {cfg.guestPort}, "
            f"mem {cfg.memorySize}, smp {cfg.smp})")
        self.proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for the guest to boot and the wrapper-lite HTTP service to answer.
        status_url = f"http://127.0.0.1:{cfg.hostPort}/status"
        for attempt in range(120):
            if self.proc.returncode is not None:
                stderr = (await self.proc.stderr.read()).decode(errors="replace")
                raise QemuCrashedException("qemu exited early:\n" + stderr[-2000:])
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get(status_url)
                    if resp.status_code == 200:
                        it(GlobalLogger).logger.info("wrapper-lite guest is ready")
                        return
            except Exception:
                pass
            await asyncio.sleep(2)
        raise QemuCrashedException("timed out waiting for wrapper-lite to become ready")

    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def terminate(self):
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=10)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass