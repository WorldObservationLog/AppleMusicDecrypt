"""HTTP client for the wrapper/lite service (v3).

Replaces the old gRPC wrapper-manager client (``src/grpc/``, now deleted).
Talks to a wrapper/lite instance over its JSON HTTP API:

    GET  /m3u8 /key /lyrics /webplayback /status
    POST /license

Every response uses the envelope ``{"code":0,"msg":"SUCCESS","data":{...}}``;
a non-zero ``code`` raises :class:`WrapperManagerException`. Transport
failures are retried with exponential backoff (``[download] retryTime /
maxWaitTime``); envelope errors are retried unless the message is a terminal
marker (``'no such account'``).
"""

import asyncio
import json
import ssl
from typing import Type

import httpx
from async_lru import alru_cache
from creart import AbstractCreator, CreateTargetInfo, exists_module, it
from tenacity import (
    before_sleep_log,
    retry,
    retry_base,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from src.config import Config
from src.logger import GlobalLogger

if __name__ == "__main__":
    # Standalone run (`python -m src.wrapper`) needs the creart creators
    # registered before the @retry decorators below evaluate it(Config) /
    # it(GlobalLogger) at class-definition time. When imported by the app,
    # main.py registers these creators beforehand, so this block is skipped.
    from creart import add_creator

    from src.config import ConfigCreator
    from src.logger import LoggerCreator

    add_creator(LoggerCreator)
    add_creator(ConfigCreator)


class WrapperManagerException(Exception):
    """Raised when the wrapper returns code != 0 or transport fails."""

    def __init__(self, msg: str):
        super().__init__(msg)
        self.msg = msg


class _retry_unless_terminal(retry_base):
    """Retry a wrapper error unless its message *contains* a terminal marker.

    tenacity's built-in ``retry_if_not_exception_message`` compares by exact
    equality, but the terminal condition here is substring containment
    (``'no such account'`` inside e.g. ``'no such account: foo'``), so a small
    custom predicate is used instead.
    """

    def __init__(self, marker: str):
        self.marker = marker

    def __call__(self, retry_state) -> bool:
        exception = retry_state.outcome.exception()
        if exception is None:
            return True
        return self.marker not in str(exception)


def _retry_policy():
    """Tenacity retry predicate for wrapper requests.

    - Transport failures (any httpx error / TLS failure) are always retried.
    - Envelope errors (``code != 0`` -> ``WrapperManagerException``) are
      retried unless the message contains the terminal marker
      ``'no such account'``. ``'no available instance'`` is transient and is
      retried (it does not contain the terminal marker).
    """
    return (
        retry_if_exception_type((httpx.HTTPError, ssl.SSLError))
        | (
            retry_if_exception_type(WrapperManagerException)
            & _retry_unless_terminal("no such account")
        )
    )


class WrapperClient:
    """Async HTTP client for a wrapper/lite instance."""

    _client: httpx.AsyncClient
    _semaphore: asyncio.Semaphore
    _base_url: str

    def __init__(self, url: str, secure: bool):
        self._base_url = f"{'https' if secure else 'http'}://{url}"
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=(10, 30),
            http2=False,
        )
        self._semaphore = asyncio.Semaphore(64)

    async def __aenter__(self) -> "WrapperClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    @property
    def base_url(self) -> str:
        """``scheme://host:port`` of the wrapper instance."""
        return self._base_url

    async def init(self) -> "WrapperClient":
        """Warm-ping the wrapper once and return ``self``.

        Fails with a clear :class:`WrapperManagerException` that includes the
        base URL when the wrapper cannot be reached (after retries are
        exhausted).
        """
        try:
            await self.status()
        except Exception as e:
            raise WrapperManagerException(
                f"unable to connect to wrapper at {self._base_url}: {e}"
            ) from e
        return self

    @alru_cache
    async def status(self) -> dict:
        """Return the ``data`` object of GET /status (e.g. ``{"regions": [...]}``).

        Cached with :func:`async_lru.alru_cache`; call
        ``status.cache_invalidate()`` to drop the cached value.
        """
        return await self._request("GET", "/status")

    async def m3u8(self, adam_id: str) -> str:
        data = await self._request("GET", "/m3u8", params={"adamId": adam_id})
        return data["m3u8"]

    async def key_template(self, adam_id: str, uri: str) -> dict:
        """Return the full ``data`` dict of GET /key (ctx/state/registers).

        The returned dict is exactly what ``temari`` expects
        (``ctx``, ``state``, ``rcx/rax/rdx/r9/rbp``).
        """
        return await self._request("GET", "/key", params={"adamId": adam_id, "uri": uri})

    async def key_template_json(self, adam_id: str, uri: str) -> str:
        """Return the JSON text of the GET /key ``data`` object.

        Convenience for ``temari.Temari.from_json()``.
        """
        data = await self.key_template(adam_id, uri)
        return json.dumps(data)

    async def lyrics(self, adam_id: str, language: str, region: str) -> str:
        # wrapper/lite's /lyrics consumes adamId + language (+ optional
        # syllable); `region` is kept in the signature for interface
        # compatibility and is not sent to the server.
        data = await self._request(
            "GET", "/lyrics", params={"adamId": adam_id, "language": language}
        )
        return data["lyrics"]

    async def webplayback(self, adam_id: str) -> str:
        data = await self._request("GET", "/webplayback", params={"adamId": adam_id})
        return data["m3u8"]

    async def license(self, adam_id: str, challenge: str, uri: str) -> str:
        data = await self._request(
            "POST",
            "/license",
            json={"adamId": adam_id, "challenge": challenge, "uri": uri},
        )
        return data["license"]

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=_retry_policy(),
        wait=wait_random_exponential(multiplier=1, max=it(Config).download.maxWaitTime),
        stop=stop_after_attempt(it(Config).download.retryTime),
        reraise=True,
        before_sleep=before_sleep_log(it(GlobalLogger).logger, "WARNING"),
    )
    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Send one request, acquire the semaphore and validate the envelope.

        Returns the ``data`` object of the envelope. Raises
        :class:`WrapperManagerException` for transport failures, HTTP 404
        (endpoint not available on this wrapper), non-JSON bodies, unexpected
        envelopes and ``code != 0`` responses.
        """
        async with self._semaphore:
            try:
                response = await self._client.request(method, path, **kwargs)
            except (httpx.HTTPError, ssl.SSLError) as e:
                raise WrapperManagerException(
                    f"{method} {path} transport error at {self._base_url}: {e!r}"
                ) from e
            return self._decode_response(method, path, response)

    @staticmethod
    def _decode_response(method: str, path: str, response: httpx.Response) -> dict:
        # Some wrapper/lite builds do not register every endpoint (e.g.
        # /webplayback); surface that as a WrapperManagerException instead of
        # failing silently.
        if response.status_code == 404:
            raise WrapperManagerException(
                f"wrapper endpoint {method} {path} is not available at "
                f"{response.url} (HTTP 404)"
            )

        try:
            payload = response.json()
        except ValueError:
            raise WrapperManagerException(
                f"wrapper returned a non-JSON body for {method} {path} "
                f"(HTTP {response.status_code})"
            )

        if not isinstance(payload, dict) or "code" not in payload:
            # NB: do not embed the raw payload here - loguru's message
            # formatting (used by before_sleep) interprets literal braces.
            raise WrapperManagerException(
                f"wrapper returned an unexpected envelope for {method} {path} "
                f"(HTTP {response.status_code}, missing 'code' field)"
            )

        code = payload.get("code")
        msg = payload.get("msg")
        if code != 0:
            raise WrapperManagerException(
                str(msg) if msg else f"wrapper error code {code} on {method} {path}"
            )

        data = payload.get("data")
        return data if isinstance(data, dict) else {}


class WrapperCreator(AbstractCreator):
    """creart creator for :class:`WrapperClient` (mirrors ``APICreator``)."""

    targets = (
        CreateTargetInfo("src.wrapper", "WrapperClient"),
    )

    @staticmethod
    def available() -> bool:
        return exists_module("src.wrapper")

    @staticmethod
    def create(create_type: Type[WrapperClient]) -> WrapperClient:
        return create_type(it(Config).instance.url, it(Config).instance.secure)


if __name__ == "__main__":
    # Minimal self-check: construct the client and print base_url. No network.
    async def _self_check() -> None:
        client = WrapperClient("127.0.0.1:8080", False)
        print(f"WrapperClient constructed; base_url={client.base_url}")
        print(
            "status.cache_invalidate available:",
            hasattr(client.status, "cache_invalidate"),
        )
        await client.close()

    asyncio.run(_self_check())