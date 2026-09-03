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
    base = "wrapper-manager-qemu" if cfg.wrapperType == "manager" else "wrapper-lite-qemu"
    name = base + (".exe" if os.name == "nt" else "")
    return shutil.which(name) or name


def _manager_mode(cfg) -> bool:
    """True when the configured local backend is wrapper-manager."""
    return getattr(cfg, "wrapperType", "manager") == "manager"


def _lite_args(start_args: str) -> list[str]:
    """Forwarded args, parsed like a shell command line (newlines are
    whitespace); each token becomes one forwarded argument."""
    return shlex.split(start_args) if start_args.strip() else []


def _memory_mb(memory_size: str) -> str:
    digits = "".join(ch for ch in memory_size if ch.isdigit())
    return digits or "512"


def build_launcher_args(cfg) -> list[str]:
    """Return the wrapper launcher argv for the current config.

    wrapper-manager-qemu only takes QEMU options (no forwarded guest args);
    wrapper-lite-qemu additionally forwards ``startArgs`` to the lite guest.
    """
    args = [_launcher_binary(cfg)]
    if cfg.hardwareAccelerator:
        args += ["--accel", cfg.hardwareAccelerator]
    if not _manager_mode(cfg):
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
            "Launching {} via {} (port {} -> {})".format("wrapper-manager" if _manager_mode(cfg) else "wrapper-lite", args[0], cfg.hostPort, cfg.guestPort))
        # Windows: launch qemu in its own console so it cannot share (and
        # compete for) the interactive console input with the REPL/TUI.
        # Other platforms: /dev/tty stdin inheritance is prevented with
        # DEVNULL, which also stops qemu from swallowing keystrokes.
        creationflags = 0
        if os.name == "nt":
            import subprocess as _sp
            creationflags = _sp.CREATE_NO_WINDOW

        self.proc = await asyncio.create_subprocess_exec(
            *args, env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
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
                            it(GlobalLogger).logger.info("{} is ready".format("wrapper-manager" if _manager_mode(cfg) else "wrapper-lite"))
                            return
                        # HTTP 200 alone is not enough for login flows: the
                        # wrapper is usable only once an account region exists.
                        try:
                            regions = resp.json().get("data", {}).get("regions") or []
                        except Exception:
                            regions = []
                        if regions:
                            it(GlobalLogger).logger.info("{} is ready (regions available)".format("wrapper-manager" if _manager_mode(cfg) else "wrapper-lite"))
                            return
            except Exception:
                pass
            await asyncio.sleep(2)
        raise QemuCrashedException("timed out waiting for wrapper {} to become ready".format("manager" if _manager_mode(cfg) else "lite"))

    async def run_login(self, loop: asyncio.AbstractEventLoop) -> int:
        """Launch wrapper-lite in one-shot login mode and wait for exit.

        Only meaningful for ``wrapperType = "lite"``. wrapper-manager has its
        own HTTP ``/login`` endpoint and does not use a one-shot guest login.
        """
        cfg = it(Config).localInstance
        if _manager_mode(cfg):
            raise RuntimeError("wrapper-manager does not support one-shot guest login; use the client's login command")
        args = build_launcher_args(cfg)
        env = build_launcher_env(cfg)
        it(GlobalLogger).logger.info(
            f"Running one-shot login via {args[0]} (port {cfg.hostPort} -> {cfg.guestPort})")
        creationflags = 0
        if os.name == "nt":
            import subprocess as _sp
            creationflags = _sp.CREATE_NO_WINDOW
        self.proc = await asyncio.create_subprocess_exec(
            *args, env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
        await asyncio.wait_for(self.proc.wait(), timeout=300)
        return self.proc.returncode

    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def terminate(self):
        if self.proc is None:
            return
        pid = self.proc.pid
        if self.proc.returncode is None:
            if os.name == "nt":
                # wrapper-lite-qemu spawns a real qemu-system-x86_64 child;
                # killing only the launcher leaves the child holding the
                # forwarded port.  Kill the whole process tree.
                import subprocess as _sp
                try:
                    _sp.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True, timeout=10,
                    )
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
            else:
                # Unix: try the process group first (launcher may not have
                # created a new one), then fall back to descendants.
                try:
                    os.killpg(os.getpgid(pid), 9)
                except Exception:
                    try:
                        self.proc.terminate()
                        await asyncio.wait_for(self.proc.wait(), timeout=10)
                    except Exception:
                        try:
                            self.proc.kill()
                        except Exception:
                            pass
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=10)
        except Exception:
            pass
        self.proc = None