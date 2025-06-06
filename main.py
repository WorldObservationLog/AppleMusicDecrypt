import asyncio

from creart import add_creator

from src.api import APICreator
from src.cmd import InteractiveShell
from src.config import ConfigCreator
from src.grpc.manager import WMCreator

loop = asyncio.new_event_loop()

add_creator(ConfigCreator)
add_creator(APICreator)
add_creator(WMCreator)

if __name__ == '__main__':
    cmd = InteractiveShell(loop)
    try:
        loop.run_until_complete(cmd.start())
    except KeyboardInterrupt:
        loop.stop()
