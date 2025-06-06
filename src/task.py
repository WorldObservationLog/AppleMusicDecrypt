from enum import StrEnum
from typing import Optional

from src.logger import RipLogger
from src.metadata import SongMetadata
from src.models import PlaylistInfo
from src.types import SongInfo, M3U8Info, ParentDoneHandler
from src.url import URLType


class Status(StrEnum):
    WAITING = "WAITING"
    DOWNLOADING = "DOWNLOADING"
    DECRYPTING = "DECRYPTING"
    DONE = "DONE"
    FAILED = "FAILED"


class Task:
    adamId: str
    status: Status
    info: SongInfo
    m3u8Info: M3U8Info
    metadata: SongMetadata
    logger: RipLogger
    parentDone: ParentDoneHandler
    playlist: PlaylistInfo = None
    decryptedSamples: list[Optional[bytes]]
    decryptedCount: int

    def __init__(self, adam_id: str, parent_done: ParentDoneHandler = None, playlist: PlaylistInfo = None):
        self.adamId = adam_id
        self.status = Status.WAITING
        self.parentDone = parent_done
        self.playlist = playlist

    def update_status(self, status: Status):
        self.status = status

    def init_decrypted_samples(self):
        self.decryptedSamples = [None for _ in self.info.samples]
        self.decryptedCount = 0

    def init_logger(self):
        self.logger = RipLogger(URLType.Song, self.adamId)

    def update_logger(self):
        self.logger.set_fullname(self.metadata.artist, self.metadata.title)
