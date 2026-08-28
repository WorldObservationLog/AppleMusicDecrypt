"""Decryption layer for v3.

Two code paths:
- FPS (default): the client fetches a *template* JSON from the wrapper/lite
  `/key` endpoint and decrypts samples **locally** with `temari` (bundled Rust
  cdylib). Streaming mode (default) uses `temari.StreamDecryptor` so samples
  are decrypted while the media file is still downloading (边下边解).
- Legacy (aac-legacy / Widevine): pywidevine generates a license challenge,
  the wrapper `/license` endpoint returns the license, and samples are
  decrypted in pure Python with AES-CBC (pycryptodome).

Temari handles are immutable and thread-safe; we cache them per (adam_id, uri).
The prefetch key template is fetched once and reused for every song.
"""

import asyncio
import base64
from typing import Optional, Tuple

from creart import it

import temari

from src.config import Config
from src.legacy.decrypt import WidevineDecrypt
from src.wrapper import WrapperClient

# Template for this key is content-independent: one fetch is reused for all songs.
PREFETCH_KEY = "skd://itunes.apple.com/P000000000/s1/e1"


class Decryptor:
    def __init__(self, wrapper: WrapperClient):
        self._wrapper = wrapper
        self._templates: dict[Tuple[str, str], temari.Temari] = {}
        self._prefetch: Optional[temari.Temari] = None
        self._prefetch_lock = asyncio.Lock()
        self._legacy: Optional[WidevineDecrypt] = None

    async def get_template(self, adam_id: str, uri: str) -> temari.Temari:
        """Return a cached Temari template handle for (adam_id, uri).

        The prefetch key's template is fetched lazily once and reused globally.
        """
        if uri == PREFETCH_KEY:
            if self._prefetch is None:
                async with self._prefetch_lock:
                    if self._prefetch is None:
                        self._prefetch = await self._load(adam_id, uri)
            return self._prefetch

        key = (adam_id, uri)
        template = self._templates.get(key)
        if template is None:
            template = await self._load(adam_id, uri)
            self._templates[key] = template
        return template

    async def _load(self, adam_id: str, uri: str) -> temari.Temari:
        json_text = await self._wrapper.key_template_json(adam_id, uri)
        try:
            return temari.Temari.from_json(json_text)
        except temari.TemariError as e:
            raise RuntimeError(
                f"Failed to build decryption template for {adam_id} {uri}: {e}"
            ) from e

    async def stream(self, adam_id: str, uri: str,
                     batch_size: Optional[int] = None) -> temari.StreamDecryptor:
        """Create a Temari streaming decryptor (边下边解)."""
        batch = batch_size or it(Config).download.decryptBatchSize
        template = await self.get_template(adam_id, uri)
        return template.stream(batch)

    # ------------------------------------------------------------------ #
    # Legacy (Widevine / aac-legacy)
    # ------------------------------------------------------------------ #
    async def legacy_content_key(self, adam_id: str, key_uri: str) -> Tuple[bytes, bytes]:
        """Acquire the (kid, key) pair for a legacy Widevine-encrypted track.

        ``key_uri`` is the EXT-X-KEY URI (e.g. ``skd://...;kid``). The KID is
        the part after the last ';' when present, otherwise the whole URI.
        """
        kid = key_uri.rsplit(";", 1)[-1]
        wv = WidevineDecrypt()
        challenge = wv.generate_challenge(kid)
        license_text = await self._wrapper.license(adam_id, challenge, key_uri)
        keys = wv.generate_key(license_text)
        for k in keys:
            if k.type == "CONTENT":
                return k.kid, k.key
        raise RuntimeError(f"No content key found in license for {adam_id}")

    def close(self):
        for t in self._templates.values():
            try:
                t.close()
            except Exception:
                pass
        self._templates.clear()
        if self._prefetch is not None:
            try:
                self._prefetch.close()
            except Exception:
                pass
            self._prefetch = None