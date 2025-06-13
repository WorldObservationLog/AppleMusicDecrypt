import asyncio
import logging
from asyncio import AbstractEventLoop
from typing import Awaitable, Callable, Type

from async_lru import alru_cache
from creart import AbstractCreator, CreateTargetInfo, exists_module, it
from grpc.aio import insecure_channel, Channel
from tenacity import retry_if_exception_type, retry, wait_random_exponential, stop_after_attempt, \
    retry_if_not_exception_message, before_sleep_log

from src.grpc.manager_pb2 import *
from src.grpc.manager_pb2_grpc import WrapperManagerServiceStub, google_dot_protobuf_dot_empty__pb2
from src.logger import GlobalLogger


class WrapperManagerException(Exception):
    def __init__(self, msg: str):
        self.msg = msg


class WrapperManager:
    _channel: Channel
    _stub: WrapperManagerServiceStub
    _decrypt_queue: asyncio.Queue[DecryptRequest]
    _login_lock: asyncio.Lock

    def __init__(self):
        self._login_lock = asyncio.Lock()
        self._decrypt_queue = asyncio.Queue()

    @classmethod
    async def create(cls, url: str) -> "WrapperManager":
        self = cls()
        self._channel = insecure_channel(url)
        self._stub = WrapperManagerServiceStub(self._channel)
        return self

    async def init(self, url: str):
        self._channel = insecure_channel(url)
        self._stub = WrapperManagerServiceStub(self._channel)
        return self

    @alru_cache
    async def status(self) -> StatusData:
        resp: StatusReply = await self._stub.Status(google_dot_protobuf_dot_empty__pb2.Empty)
        if resp.header.code != 0:
            raise WrapperManagerException(resp.header.msg)
        return resp.data

    async def login(self, username: str, password: str, on_2fa: Callable[[str, str], Awaitable[int]]):
        await self._login_lock.acquire()

        login_queue = asyncio.Queue()

        async def request_stream():
            while True:
                item = await login_queue.get()
                if item is None:
                    break
                yield item

        stream = self._stub.Login(request_stream())

        await login_queue.put(LoginRequest(data=LoginData(username=username, password=password)))

        async for reply in stream:
            reply: LoginReply
            match reply.header.code:
                case -1:
                    self._login_lock.release()
                    await login_queue.put(None)
                    raise WrapperManagerException(reply.header.msg)
                case 0:
                    self._login_lock.release()
                    await login_queue.put(None)
                    return
                case 2:
                    two_step_code = await on_2fa(username, password)
                    await login_queue.put(LoginRequest(data=LoginData(
                        username=username,
                        password=password,
                        two_step_code=two_step_code)))

    async def decrypt(self, adam_id: str, key: str, sample: bytes, sample_index: int):
        await self._decrypt_queue.put(
            DecryptRequest(data=DecryptData(adam_id=adam_id, key=key, sample_index=sample_index,
                                            sample=sample)))

    async def _decrypt_request_generator(self):
        while True:
            yield await self._decrypt_queue.get()

    async def decrypt_init(self, on_success: Callable[[str, str, bytes, int], Awaitable[None]],
                           on_failure: Callable[[str, str, bytes, int], Awaitable[None]]):
        stream = self._stub.Decrypt(self._decrypt_request_generator())
        async for reply in stream:
            reply: DecryptReply
            match reply.header.code:
                case -1:
                    it(AbstractEventLoop).create_task(on_failure(reply.data.adam_id, reply.data.key, reply.data.sample, reply.data.sample_index))
                case 0:
                    it(AbstractEventLoop).create_task(on_success(reply.data.adam_id, reply.data.key, reply.data.sample, reply.data.sample_index))

    @retry(retry=((retry_if_exception_type(WrapperManagerException)) & (retry_if_not_exception_message('no available instance'))),
           wait=wait_random_exponential(multiplier=1, max=60),
           stop=stop_after_attempt(32), before_sleep=before_sleep_log(it(GlobalLogger).logger, logging.WARN))
    async def m3u8(self, adam_id: str) -> str:
        resp: M3U8Reply = await self._stub.M3U8(M3U8Request(data=M3U8DataRequest(adam_id=adam_id)))
        if resp.header.code != 0:
            raise WrapperManagerException(resp.header.msg)
        return resp.data.m3u8

    @retry(retry=((retry_if_exception_type(WrapperManagerException)) & (retry_if_not_exception_message('no available instance'))),
           wait=wait_random_exponential(multiplier=1, max=60),
           stop=stop_after_attempt(32), before_sleep=before_sleep_log(it(GlobalLogger).logger, logging.WARN))
    async def lyrics(self, adam_id: str, language: str, region: str) -> str:
        resp: LyricsReply = await self._stub.Lyrics(LyricsRequest(
            data=LyricsDataRequest(adam_id=adam_id, language=language, region=region)))
        if resp.header.code != 0:
            raise WrapperManagerException(resp.header.msg)
        return resp.data.lyrics


class WMCreator(AbstractCreator):
    targets = (
        CreateTargetInfo("src.grpc.manager", "WrapperManager"),
    )

    @staticmethod
    def available() -> bool:
        return exists_module("src.grpc.manager")

    @staticmethod
    def create(create_type: Type[WrapperManager]) -> WrapperManager:
        return create_type()
