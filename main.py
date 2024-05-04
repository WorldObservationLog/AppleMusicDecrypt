import asyncio

from src.cmd import NewInteractiveShell

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    cmd = NewInteractiveShell(loop)
    try:
        loop.run_until_complete(cmd.start())
    except KeyboardInterrupt:
        loop.stop()
