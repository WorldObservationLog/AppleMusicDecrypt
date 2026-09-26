"""Headless downloader: boots wrapper-lite, rips one album, waits for all tracks, exits.

Usage: uv run python scripts/headless_dl.py <album-url> [codec]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from creart import add_creator, it

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from src.logger import LoggerCreator
add_creator(LoggerCreator)
from src.config import ConfigCreator
add_creator(ConfigCreator)
from src.api import APICreator
add_creator(APICreator)
from src.wrapper import WrapperCreator
add_creator(WrapperCreator)
from src.decrypt import DecryptorCreator
add_creator(DecryptorCreator)
from src.measurer import MeasurerCreator
add_creator(MeasurerCreator)
from src.tui.task_tree import TaskTreeCreator
add_creator(TaskTreeCreator)

from src.cmd import InteractiveShell
from src.flags import Flags
from src.url import AppleMusicURL
from src.utils import background_tasks


def main():
    url_str = sys.argv[1]
    codec = sys.argv[2] if len(sys.argv) > 2 else "alac"
    # Constructor runs its own loop.run_until_complete() calls, so it must be
    # invoked while the loop is NOT already running.
    shell = InteractiveShell(loop, legacy_ui=True)
    url = AppleMusicURL.parse_url(url_str)
    if url is None:
        print("Failed to parse URL:", url_str)
        sys.exit(1)

    async def _run():
        await shell.ripper.rip_album(url, codec, Flags())
        while background_tasks:
            await asyncio.sleep(2)
        # let the last saves flush
        await asyncio.sleep(5)
        print("ALL_TASKS_DONE")
        await shell.localInstance.terminate()

    try:
        loop.run_until_complete(_run())
    except KeyboardInterrupt:
        loop.run_until_complete(shell.localInstance.terminate())


if __name__ == "__main__":
    main()
