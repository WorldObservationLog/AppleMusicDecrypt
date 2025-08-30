import time
from collections import deque
from typing import Type

from creart import CreateTargetInfo, AbstractCreator, exists_module


class Measurer:
    def __init__(self, sample_window=1):
        self._sample_window = sample_window
        self._download_records = deque()  # 存储 (时间戳, 字节数)
        self._decrypt_records = deque()  # 存储 (时间戳, 字节数)
        self._running_tasks = 0

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

    def download_speed(self) -> str:
        now = time.time()
        self._evict_old(self._download_records, now)
        return self._calc_speed(self._download_records)

    def decrypt_speed(self) -> str:
        now = time.time()
        self._evict_old(self._decrypt_records, now)
        return self._calc_speed(self._decrypt_records)

    def tasks_count(self):
        return self._running_tasks

    def _evict_old(self, dq, now):
        """只保留采样窗口内的数据"""
        while dq and now - dq[0][0] > self._sample_window:
            dq.popleft()

    def _calc_speed(self, dq):
        bytes_sum = sum(x[1] for x in dq)
        speed_bps = bytes_sum / self._sample_window  # 字节/秒
        speed_kb_s = speed_bps / 1024
        if speed_kb_s < 1024:
            return f"{speed_kb_s:.2f} kB/s"
        else:
            speed_mb_s = speed_kb_s / 1024
            return f"{speed_mb_s:.2f} MB/s"


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
