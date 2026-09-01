"""Local wrapper-lite login helper.

Boots the bundled wrapper-lite instance (via wrapper-lite-qemu), prompts
for Apple ID credentials, and relaunches wrapper-lite with ``--login``.
Run from the package root:

    python qemu/login.py

This is the interactive counterpart of setting ``[localInstance] startArgs``
in ``config.toml``.
"""

import asyncio
import getpass
import os
import sys
from pathlib import Path

# Allow running from the package root (python qemu/login.py) or from
# inside the qemu/ directory.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from creart import add_creator, it  # noqa: E402

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from src.logger import LoggerCreator, GlobalLogger  # noqa: E402
add_creator(LoggerCreator)
from src.config import ConfigCreator  # noqa: E402
add_creator(ConfigCreator)
from src.wrapper import WrapperCreator, WrapperError  # noqa: E402
add_creator(WrapperCreator)
from src.qemu import QemuInstance  # noqa: E402

from src.config import Config  # noqa: E402
from src.wrapper import WrapperClient  # noqa: E402


def _print_banner():
    print("=" * 60)
    print(" AppleMusicDecrypt - local wrapper-lite login")
    print("=" * 60)


async def _regions_available() -> bool:
    client = it(WrapperClient)
    client.status.cache_invalidate()
    try:
        resp = await client.status()
    except WrapperError:
        return False
    return bool(resp.get("regions"))


async def main() -> int:
    _print_banner()
    cfg = it(Config).localInstance
    if not cfg.enable:
        print("Local instance is disabled ([localInstance] enable = false).")
        print("Enable it in config.toml first, then re-run this script.")
        return 1

    # Boot wrapper-lite without login args.
    qemu = QemuInstance()
    await qemu.launch_instance(loop)
    it(Config).instance.url = f"127.0.0.1:{cfg.hostPort}"
    it(Config).instance.secure = False
    await it(WrapperClient).init()

    if await _regions_available():
        print("\nAn Apple account is already logged in. Nothing to do.")
        await qemu.terminate()
        return 0

    username = input("\nApple ID / Username: ")
    password = getpass.getpass("Password: ")
    two_fa = input("2FA code (leave empty if not prompted): ").strip()

    lite_args = f"--login {username}:{password}"
    if two_fa:
        # wrapper-lite reads the 2FA code from a file (--code-from-file).
        code_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "2fa_code.txt")
        Path(code_file).write_text(two_fa, encoding="utf-8")
        lite_args += f" --code-from-file {code_file}"

    # Relaunch wrapper-lite with the login args: the login is performed by
    # the guest at boot, so the instance must be restarted with the creds.
    print("\nRestarting wrapper-lite with login credentials...")
    await qemu.terminate()
    cfg.startArgs = lite_args
    qemu2 = QemuInstance()
    await qemu2.launch_instance(loop)
    it(Config).instance.url = f"127.0.0.1:{cfg.hostPort}"
    await it(WrapperClient).init()

    ok = await _regions_available()
    await qemu2.terminate()

    if ok:
        print("\nLogin successful! You can now start the app with start.bat.")
        return 0
    print("\nLogin failed: the wrapper reports no available account.")
    print("Check your credentials (and 2FA code) and try again.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(loop.run_until_complete(main()))
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
