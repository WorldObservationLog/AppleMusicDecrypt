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
class Task:
    adamId: str
    parentDone: Optional[ParentDoneHandler] = None
    playlist: Optional[PlaylistInfo] = None
    status: Status = Status.WAITING
    m3u8Info: Optional[M3U8Info] = None
    metadata: Optional[SongMetadata] = None
    logger: Optional[RipLogger] = None
    error: Optional[Exception] = None
    # Streaming/decrypt progress (bytes), used by the status toolbar.
    downloaded_bytes: int = 0
    decrypted_bytes: int = 0

    @property
    def display_name(self) -> str:
        if self.metadata and self.metadata.artist and self.metadata.title:
            return f"{self.metadata.artist} - {self.metadata.title}"
        if self.logger and getattr(self.logger, "full_name", None):
            return self.logger.full_name
        return self.adamId

    def update_status(self, status: Status):
        self.status = status