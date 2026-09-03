"""Local wrapper login helper.

Supports both local backends:
- wrapper-manager (default): uses HTTP POST /login (including 2FA two-phase).
- wrapper-lite:              boots the guest and runs a one-shot guest login.
"""

import asyncio
import getpass
import os
import sys

# Allow running from the package root (python qemu/login.py) or from
# inside the qemu/ directory.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from creart import add_creator, it  # noqa: E402

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from src.logger import LoggerCreator  # noqa: E402
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
    print(" AppleMusicDecrypt - local wrapper login")
    print("=" * 60)


async def _regions_available() -> bool:
    client = it(WrapperClient)
    client.status.cache_invalidate()
    try:
        resp = await client.status()
    except WrapperError:
        return False
    return bool(resp.get("regions"))


async def _manager_login_flow() -> int:
    """Login to wrapper-manager through its HTTP /login endpoint."""
    cfg = it(Config).localInstance
    qemu = QemuInstance()
    await qemu.launch_instance(loop, wait_for_regions=False)
    it(Config).instance.url = f"127.0.0.1:{cfg.hostPort}"
    it(Config).instance.secure = False
    await it(WrapperClient).init()

    if await _regions_available():
        print("\nwrapper-manager already has a logged-in account. Nothing to do.")
        await qemu.terminate()
        return 0

    username = input("\nApple ID / Username: ")
    password = getpass.getpass("Password: ")

    try:
        await it(WrapperClient).login(username, password)
    except WrapperError as e:
        msg = str(e).lower()
        if "2fa" not in msg and "code require" not in msg:
            print("\nLogin failed:", e)
            await qemu.terminate()
            return 1
        code = input("2FA code: ").strip()
        try:
            await it(WrapperClient).login(username, password, code=code)
        except WrapperError as e2:
            print("\nLogin failed:", e2)
            await qemu.terminate()
            return 1

    it(WrapperClient).status.cache_invalidate()
    ok = await _regions_available()
    await qemu.terminate()
    if ok:
        print("\nLogin successful! You can now start the app.")
        return 0
    print("\nLogin reported success but no region is available yet.")
    print("Wait a moment and run `status` inside the app, or retry.")
    return 0


async def _lite_login_flow() -> int:
    """Boot wrapper-lite and perform a one-shot guest login."""
    import tempfile

    cfg = it(Config).localInstance

    # Boot wrapper-lite without login args; unauthenticated instance is fine.
    qemu = QemuInstance()
    await qemu.launch_instance(loop, wait_for_regions=False)
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

    login_args = f"--login {username}:{password}"
    code_file = None
    if two_fa:
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qemu")
        os.makedirs(data_dir, exist_ok=True)
        code_file = os.path.join(data_dir, "2fa.txt")
        with open(code_file, "w", encoding="utf-8") as f:
            f.write(two_fa)
        login_args += " --code-from-file"

    print("\nRunning one-shot login with wrapper-lite...")
    await qemu.terminate()
    old_start_args = cfg.startArgs
    cfg.startArgs = login_args
    try:
        qemu_login = QemuInstance()
        await qemu_login.run_login(loop)
    finally:
        cfg.startArgs = old_start_args
        if code_file:
            try:
                os.remove(code_file)
            except OSError:
                pass

    print("\nStarting wrapper-lite with cached account...")
    qemu_final = QemuInstance()
    await qemu_final.launch_instance(loop, wait_for_regions=True)
    it(Config).instance.url = f"127.0.0.1:{cfg.hostPort}"
    await it(WrapperClient).init()

    ok = await _regions_available()
    await qemu_final.terminate()

    if ok:
        print("\nLogin successful! You can now start the app with start.bat.")
        return 0
    print("\nLogin failed: the wrapper reports no available account.")
    print("Check your credentials (and 2FA code) and try again.")
    return 1


async def main() -> int:
    _print_banner()
    cfg = it(Config).localInstance
    if not cfg.enable:
        print("Local instance is disabled ([localInstance] enable = false).")
        print("Enable it in config.toml first, then re-run this script.")
        return 1

    if getattr(cfg, "wrapperType", "manager") == "manager":
        return await _manager_login_flow()
    return await _lite_login_flow()


if __name__ == "__main__":
    try:
        sys.exit(loop.run_until_complete(main()))
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
