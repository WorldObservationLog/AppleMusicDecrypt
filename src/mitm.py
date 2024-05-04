import plistlib

import mitmproxy.http
from mitmproxy import options
from mitmproxy.tools import dump
from loguru import logger


class RequestHandler:
    def __init__(self, callback):
        self.callback = callback

    def response(self, flow: mitmproxy.http.HTTPFlow):
        if flow.request.host == "play.itunes.apple.com" and flow.request.path == "/WebObjects/MZPlay.woa/wa/subPlaybackDispatch":
            data = plistlib.loads(flow.response.content)
            m3u8 = data["songList"][0]["hls-playlist-url"]
            flow.response.status_code = 500
            self.callback(m3u8)


async def start_proxy(host, port, callback):
    opts = options.Options(listen_host=host, listen_port=port, mode=["socks5"])

    master = dump.DumpMaster(
        opts,
        with_termlog=False,
        with_dumper=False,
    )
    master.addons.add(RequestHandler(callback))

    logger.info(f"Mitmproxy started at socks5://{host}:{port}")

    await master.run()
