import asyncio

from creart import add_creator

loop = asyncio.new_event_loop()

from src.logger import LoggerCreator
add_creator(LoggerCreator)
from src.config import ConfigCreator
add_creator(ConfigCreator)
from src.api import APICreator
add_creator(APICreator)
from src.wrapper import WrapperCreator
add_creator(WrapperCreator)
from src.measurer import MeasurerCreator
add_creator(MeasurerCreator)

from src.cmd import InteractiveShell

if __name__ == '__main__':
    cmd = InteractiveShell(loop)
    try:
        loop.run_until_complete(cmd.start())
    except KeyboardInterrupt:
        loop.stop()