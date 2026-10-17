import time
from collections import deque
from typing import Type

from creart import CreateTargetInfo, AbstractCreator, exists_module


def _fmt_speed(bytes_per_second: float) -> str:
    kb = bytes_per_second / 1024
    if kb < 1024:
        return f"{kb:.2f} kB/s"
    return f"{kb / 1024:.2f} MB/s"


class Measurer:
    """Rolling throughput measurements for the status bar.

    Speeds are computed over a one-second window of byte counts.  Both the
    formatted string and the raw byte rate are exposed: the status bar prints
    the string, while ETA and the speed sparkline need the number.
    """

    def __init__(self, sample_window=1, history=60):
        self._sample_window = sample_window
        self._download_records = deque()  # 存储 (时间戳, 字节数)
        self._decrypt_records = deque()  # 存储 (时间戳, 字节数)
        self._running_tasks = 0
        # Recent download rates, one entry per UI sample, oldest first.  This
        # is what the status-bar sparkline draws.
        self._speed_history: deque[float] = deque(maxlen=history)

    def record_download(self, content_length: int):
        now = time.time()
        self._download_records.append((now, content_length))

    def record_decrypt(self, content_length: int):
        now = time.time()
        self._decrypt_records.append((now, content_length))

    def record_task_start(self):
        self._running_tasks += 1

    def record_task_finish(self):
        self._running_tasks -= 1

    # ------------------------------------------------------------------ #
    # Speeds
    # ------------------------------------------------------------------ #

    def download_speed_bps(self) -> float:
        """Current download rate in bytes/second."""
        now = time.time()
        self._evict_old(self._download_records, now)
        return self._calc_speed_bps(self._download_records)

    def decrypt_speed_bps(self) -> float:
        """Current decrypt rate in bytes/second."""
        now = time.time()
        self._evict_old(self._decrypt_records, now)
        return self._calc_speed_bps(self._decrypt_records)

    def download_speed(self) -> str:
        return _fmt_speed(self.download_speed_bps())

    def decrypt_speed(self) -> str:
        return _fmt_speed(self.decrypt_speed_bps())

    def tasks_count(self):
        return self._running_tasks

    # ------------------------------------------------------------------ #
    # Speed history (sparkline)
    # ------------------------------------------------------------------ #

    def sample_speed(self) -> None:
        """Record the current download rate; called once per UI tick."""
        self._speed_history.append(self.download_speed_bps())

    def speed_history(self) -> list[float]:
        """Recent download rates, oldest first."""
        return list(self._speed_history)

    # ------------------------------------------------------------------ #

    def _evict_old(self, dq, now):
        """只保留采样窗口内的数据"""
        while dq and now - dq[0][0] > self._sample_window:
            dq.popleft()

    def _calc_speed_bps(self, dq) -> float:
        return sum(x[1] for x in dq) / self._sample_window

    def _calc_speed(self, dq) -> str:
        """Formatted rate for *dq* (kept for callers predating _bps)."""
        return _fmt_speed(self._calc_speed_bps(dq))


class MeasurerCreator(AbstractCreator):
    targets = (
        CreateTargetInfo("src.measurer", "Measurer"),
    )

    @staticmethod
    def available() -> bool:
        return exists_module("src.config")

    @staticmethod
    def create(create_type: Type[Measurer]) -> Measurer:
        return create_type()
