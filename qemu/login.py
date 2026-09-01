"""Local wrapper-lite login helper.

Boots the bundled wrapper-lite instance (via wrapper-lite-qemu), prompts
for Apple ID credentials, and performs the login without putting the
password on any command line: the credentials are written to a temporary
args file and passed to the launcher through the ``LITE_ARGS_FILE``
environment variable (wrapper-lite reads its arguments from that file).

Run from the package root:

    python qemu/login.py
"""

import asyncio
import getpass
import os
import sys
import tempfile

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

    # Boot wrapper-lite without login args.  An unauthenticated instance
    # legitimately reports an empty regions list, so only wait for HTTP 200
    # here; the post-login relaunch below waits for regions.
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

    # Write the login arguments to a temp file.  wrapper-lite reads its
    # argument list from LITE_ARGS_FILE, so the password never appears in
    # the qemu process command line (ps) — same spirit as v2 passing the
    # credentials through the login API instead of CLI args.
    login_args = [f"--login {username}:{password}"]
    code_file = None
    if two_fa:
        fd, code_file = tempfile.mkstemp(prefix="lite-2fa-", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(two_fa)
        login_args.append(f"--code-from-file {code_file}")

    fd_args, args_path = tempfile.mkstemp(prefix="lite-args-", text=True)
    with os.fdopen(fd_args, "w", encoding="utf-8") as f:
        f.write("\n".join(login_args) + "\n")

    # Relaunch wrapper-lite with the args file; wait for regions to confirm
    # the login actually succeeded.
    print("\nRestarting wrapper-lite with login credentials...")
    await qemu.terminate()
    old_env = os.environ.get("LITE_ARGS_FILE")
    os.environ["LITE_ARGS_FILE"] = args_path
    try:
        qemu2 = QemuInstance()
        await qemu2.launch_instance(loop, wait_for_regions=True)
        it(Config).instance.url = f"127.0.0.1:{cfg.hostPort}"
        await it(WrapperClient).init()

        ok = await _regions_available()
        await qemu2.terminate()
    finally:
        os.environ.pop("LITE_ARGS_FILE", None)
        if old_env is not None:
            os.environ["LITE_ARGS_FILE"] = old_env
        try:
            os.remove(args_path)
        except OSError:
            pass
        if code_file:
            try:
                os.remove(code_file)
            except OSError:
                pass

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
