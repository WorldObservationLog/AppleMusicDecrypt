"""Local wrapper-lite launcher (thin wrapper around ``wrapper-lite-qemu``).

Delegates the actual QEMU invocation to the wrapper repo's ``wrapper-lite-qemu``
launcher, which boots the guest, forwards the lite args and port-forwards the
HTTP service. This module only:

- locates the launcher binary
- builds its command line (``--accel`` + forwarded lite args) and env
- spawns it, polls ``http://127.0.0.1:<hostPort>/status`` until ready
- terminates it on shutdown
"""

import asyncio
import os
import shlex
import shutil

import httpx
from creart import it

from src.config import Config
from src.logger import GlobalLogger


class QemuCrashedException(Exception):
    def __init__(self, msg: str):
        self.msg = msg
        super().__init__(msg)


def _launcher_binary(cfg) -> str:
    if cfg.launcherBin:
        return cfg.launcherBin
    name = "wrapper-lite-qemu.exe" if os.name == "nt" else "wrapper-lite-qemu"
    return shutil.which(name) or name


def _lite_args(start_args: str) -> list[str]:
    """Forwarded args, parsed like a shell command line (newlines are
    whitespace); each token becomes one forwarded argument."""
    return shlex.split(start_args) if start_args.strip() else []


def _memory_mb(memory_size: str) -> str:
    digits = "".join(ch for ch in memory_size if ch.isdigit())
    return digits or "512"


def build_launcher_args(cfg) -> list[str]:
    """Return the wrapper-lite-qemu argv for the current config."""
    args = [_launcher_binary(cfg)]
    if cfg.hardwareAccelerator:
        args += ["--accel", cfg.hardwareAccelerator]
    args += _lite_args(cfg.startArgs)
    return args


def build_launcher_env(cfg) -> dict:
    env = dict(os.environ)
    env["HOST_PORT"] = str(cfg.hostPort)
    env["GUEST_PORT"] = str(cfg.guestPort)
    env["MEMORY"] = _memory_mb(cfg.memorySize)
    env["SMP"] = str(cfg.smp)
    if cfg.hardwareAccelerator:
        env["LITE_QEMU_ACCEL"] = cfg.hardwareAccelerator
    return env


class QemuInstance:
    proc = None

    async def launch_instance(self, loop: asyncio.AbstractEventLoop,
                              wait_for_regions: bool = False):
        cfg = it(Config).localInstance
        args = build_launcher_args(cfg)
        env = build_launcher_env(cfg)
        it(GlobalLogger).logger.info(
            f"Launching wrapper-lite via {args[0]} (port {cfg.hostPort} -> {cfg.guestPort})")
        self.proc = await asyncio.create_subprocess_exec(
            *args, env=env,
            # Must NOT inherit the terminal stdin: qemu would compete with
            # the REPL/TUI for /dev/tty input and swallow keystrokes.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        status_url = f"http://127.0.0.1:{cfg.hostPort}/status"
        for _ in range(120):
            if self.proc.returncode is not None:
                stderr = (await self.proc.stderr.read()).decode(errors="replace")
                raise QemuCrashedException("wrapper-lite-qemu exited early:\n" + stderr[-2000:])
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get(status_url)
                    if resp.status_code == 200:
                        if not wait_for_regions:
                            it(GlobalLogger).logger.info("wrapper-lite is ready")
                            return
                        # HTTP 200 alone is not enough for login flows: the
                        # wrapper is usable only once an account region exists.
                        try:
                            regions = resp.json().get("data", {}).get("regions") or []
                        except Exception:
                            regions = []
                        if regions:
                            it(GlobalLogger).logger.info("wrapper-lite is ready (regions available)")
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