import asyncio

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

from src.logger import RipLogger
from src.metadata import SongMetadata
from src.models import PlaylistInfo
from src.types import M3U8Info, ParentDoneHandler


class Status(StrEnum):
    WAITING = "WAITING"
    PARSING = "PARSING"
    DOWNLOADING = "DOWNLOADING"
    DECRYPTING = "DECRYPTING"
    SAVING = "SAVING"
    DONE = "DONE"
    ALREADY_EXIST = "ALREADY_EXIST"
    FAILED = "FAILED"

    def is_terminal(self) -> bool:
        return self in (Status.DONE, Status.ALREADY_EXIST, Status.FAILED)


@dataclass
class RipRequest:
    """What was asked for, kept so a finished task can be run again.

    Only the pieces ``InteractiveShell.do_download`` needs are stored.  The URL
    is *rebuilt* from the storefront and the adam id rather than kept as a
    string, because most tasks have no URL to keep: an album's tracks are
    created as ``Song(id=…, storefront=…, url="")`` and the per-track URL is
    never known at registration time.
    """

    adam_id: str
    storefront: str = "us"
    codec: str = "alac"
    force: bool = False
    language: str = ""
    include: bool = False

    @property
    def url(self) -> str:
        return f"https://music.apple.com/{self.storefront}/song/{self.adam_id}"


@dataclass
class Task:
    adamId: str
    parentDone: Optional[ParentDoneHandler] = None
    playlist: Optional[PlaylistInfo] = None
    status: Status = Status.WAITING
    m3u8Info: Optional[M3U8Info] = None
    metadata: Optional[SongMetadata] = None
    logger: Optional[RipLogger] = None
    error: Optional[Exception] = None
    # TUI task-tree: ID of the parent TreeNode (album / playlist / artist).
    # Empty string means the task is a top-level (direct single-song dl).
    group_node_id: str = ""
    # Streaming/decrypt progress (bytes), used by the status toolbar.
    downloaded_bytes: int = 0
    decrypted_bytes: int = 0
    # Expected size of the media stream.  0 means "not known yet": the value
    # arrives from the byte-range in the m3u8 (``M3U8Info.range_length``) or
    # from the response headers on the first request, so progress is unknown
    # until the stream actually starts.
    total_bytes: int = 0
    # Set by the ripper: how to run this again, and the coroutine running it
    # now.  Both are read only by the sidebar's retry / cancel keys.
    request: Optional[RipRequest] = None
    runner: Optional[asyncio.Task] = None

    @property
    def progress(self) -> Optional[float]:
        """Download progress as a fraction in ``[0, 1]``, or ``None``.

        ``None`` means the total size is not known, which is different from
        "0% done" — callers use the distinction to decide whether to show a
        percentage at all rather than displaying a stuck 0%.
        """
        if self.total_bytes <= 0:
            return None
        return min(1.0, self.downloaded_bytes / self.total_bytes)

    @property
    def display_name(self) -> str:
        if self.metadata and self.metadata.artist and self.metadata.title:
            return f"{self.metadata.artist} - {self.metadata.title}"
        if self.logger and getattr(self.logger, "full_name", None):
            return self.logger.full_name
        return self.adamId

    def update_status(self, status: Status):
        self.status = status