"""Internal task status system.

Provides a granular status vocabulary for ripping tasks and a panel renderer
used by the REPL's ``status`` command to show a live snapshot of every running
task (stage, item, download/decrypt progress).
"""

from enum import StrEnum
from typing import Iterable

from src.task import Task


class StatusCode(StrEnum):
    """Granular per-task stage (superset of :class:`src.task.Status`)."""
    WAITING = "WAITING"
    PARSING = "PARSING"
    DOWNLOADING = "DOWNLOADING"
    DECRYPTING = "DECRYPTING"
    SAVING = "SAVING"
    DONE = "DONE"
    ALREADY_EXIST = "ALREADY_EXIST"
    FAILED = "FAILED"


class WarningCode(StrEnum):
    NO_AVAILABLE_ACCOUNT_FOR_LYRICS = "NO_AVAILABLE_ACCOUNT_FOR_LYRICS"
    UNABLE_GET_LYRICS = "UNABLE_GET_LYRICS"
    RETRYABLE_DECRYPT_FAILED = "RETRYABLE_DECRYPT_FAILED"


class ErrorCode(StrEnum):
    NOT_EXIST_IN_STOREFRONT = "NOT_EXIST_IN_STOREFRONT"
    AUDIO_NOT_EXIST = "AUDIO_NOT_EXIST"
    LOSSLESS_AUDIO_NOT_EXIST = "LOSSLESS_AUDIO_NOT_EXIST"
    DECRYPT_FAILED = "DECRYPT_FAILED"
    INTEGRITY_FAILED = "INTEGRITY_FAILED"


def _fmt_bytes(n: int) -> str:
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.0f} kB"
    return f"{n} B"


def status_panel(tasks: Iterable[Task]) -> str:
    """Render a text panel of the given tasks (newest first)."""
    rows = []
    for t in tasks:
        if t.status.is_terminal() and not t.error:
            continue  # skip quietly-finished tasks unless they failed
        stage = t.status.value
        name = t.display_name
        dl = f"dl {_fmt_bytes(t.downloaded_bytes)}" if t.downloaded_bytes else ""
        dec = f"dec {_fmt_bytes(t.decrypted_bytes)}" if t.decrypted_bytes else ""
        extra = " ".join(x for x in (dl, dec) if x)
        line = f"  [{stage:<12}] {name}"
        if extra:
            line += f"  ({extra})"
        if t.error:
            line += f"  ERROR: {t.error}"
        rows.append(line)
    if not rows:
        return "  (no active tasks)"
    return "\n".join(rows)