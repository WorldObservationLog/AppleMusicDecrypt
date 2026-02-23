import os
from collections import defaultdict
from src.config import Config
from src.types import Codec
from creart import it
from src.utils import get_codec_from_codec_id, get_song_name_and_dir_path, get_suffix
from src.task import Status
from extras.telegram_bot.src.upload import UploadTask

# Global state moved here to avoid circular imports between handlers/download.py and main.py
telegram_tasks_listeners = defaultdict(list)

async def handle_task_complete(task, upload_worker):
    listeners = telegram_tasks_listeners.pop(task.adamId, [])
    if not listeners:
        return

    if task.status == Status.DONE:
        try:
            # Predict the saved filename via same config paths
            codec = get_codec_from_codec_id(task.m3u8Info.codec_id) if (
                    task.m3u8Info and getattr(task.m3u8Info, 'codec_id', None)) else Codec.AAC_LEGACY
            song_name, dir_path = get_song_name_and_dir_path(codec.upper(), task.metadata, task.playlist)
            filename = str(
                (dir_path / (song_name + get_suffix(codec, it(Config).download.atmosConventToM4a))).absolute())

            if os.path.exists(filename):
                for listener in listeners:
                    title = task.metadata.title if task.metadata and task.metadata.title else None
                    artist = task.metadata.artist if task.metadata and task.metadata.artist else None
                    cover = task.metadata.cover if task.metadata and task.metadata.cover else None

                    await upload_worker.enqueue(UploadTask(
                        chat_id=listener["chat_id"],
                        filename=filename,
                        message_id=listener["message_id"],
                        title=title,
                        performer=artist,
                        cover=cover,
                        cache_metadata={
                            "adam_id": task.adamId,
                            "codec": listener["codec"],
                            "language": listener["language"]
                        },
                        on_upload_start=listener.get("on_upload_start"),
                        on_upload_done=listener.get("on_upload_done"),
                        warning=task.error
                    ))
                return
        except Exception:
            pass

    # Prevent deadlocks by signaling failure if file missing or errored
    for listener in listeners:
        cb = listener.get("on_upload_done")
        if cb:
            await cb(False, task.error)
