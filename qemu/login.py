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

    # The guest entrypoint performs login when the account database is
    # missing: it launches wrapper-lite-rootless --login ... and exits after
    # the tokens are cached.  So the login run is a one-shot boot that
    # terminates itself; the next normal boot serves requests.
    login_args = f"--login {username}:{password}"
    code_file = None
    if two_fa:
        # wrapper-lite reads the 2FA code from <base-dir>/2fa.txt when
        # --code-from-file is set.  The qemu guest mounts data.img as /data,
        # so write the code into the host data directory used by the guest.
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
        # Login mode never starts the HTTP service; wait for the launcher
        # (and the guest) to exit after the tokens are cached.
        await qemu_login.run_login(loop)
    finally:
        cfg.startArgs = old_start_args
        if code_file:
            try:
                os.remove(code_file)
            except OSError:
                pass

    # Final boot without login args: the cached tokens should now make the
    # service report a region.
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


if __name__ == "__main__":
    try:
        sys.exit(loop.run_until_complete(main()))
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
