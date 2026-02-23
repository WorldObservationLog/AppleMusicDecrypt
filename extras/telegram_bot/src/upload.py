import asyncio
import os
import shutil
from typing import NamedTuple, Optional, Callable, Awaitable

from mutagen import File as MutagenFile
from telegram.error import NetworkError, TimedOut
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from extras.telegram_bot.src.config import bot_config
from extras.telegram_bot.src.db import user_db


class UploadTask(NamedTuple):
    chat_id: int
    filename: Optional[str]
    message_id: int
    title: Optional[str] = None
    performer: Optional[str] = None
    cover: Optional[bytes] = None
    cache_metadata: dict = None
    cached_file_id: Optional[str] = None
    on_upload_start: Optional[Callable[[], Awaitable[None]]] = None
    on_upload_done: Optional[Callable[[bool, Optional[Exception]], Awaitable[None]]] = None
    warning: Optional[Exception] = None


class UploadWorker:
    def __init__(self, bot):
        self.queue = asyncio.Queue()
        self.bot = bot
        self._worker_task = None

    def start(self):
        if not self._worker_task:
            self._worker_task = asyncio.create_task(self._process_queue())

    def stop(self):
        if self._worker_task:
            self._worker_task.cancel()
            self._worker_task = None

    async def enqueue(self, task: UploadTask):
        await self.queue.put(task)

    @retry(
        retry=retry_if_exception_type((NetworkError, TimedOut)),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(5)
    )
    async def _send_file_with_retry(self, task: UploadTask):
        try:
            duration = 0
            if task.filename:
                try:
                    m_file = MutagenFile(task.filename)
                    if m_file and m_file.info:
                        duration = int(m_file.info.length)
                except Exception:
                    pass

            thumb_bytes = None
            if task.cover:
                def resize_cover(cover_bytes):
                    from io import BytesIO
                    from PIL import Image
                    try:
                        img = Image.open(BytesIO(cover_bytes))
                        img.thumbnail((320, 320), Image.Resampling.LANCZOS)
                        if img.mode != "RGB":
                            img = img.convert("RGB")
                        out = BytesIO()
                        img.save(out, format="JPEG", quality=85)
                        t_bytes = out.getvalue()

                        if len(t_bytes) > 200 * 1024:
                            out = BytesIO()
                            img.save(out, format="JPEG", quality=60)
                            t_bytes = out.getvalue()
                        return t_bytes
                    except Exception as e:
                        print(f"Thumbnail resize failed: {e}")
                        return None

                try:
                    thumb_bytes = await asyncio.to_thread(resize_cover, task.cover)
                except Exception as e:
                    print(f"Async thumbnail resize failed: {e}")

            # Use large timeout for big files
            return await self.bot.send_audio(
                chat_id=task.chat_id,
                audio=task.filename,
                title=task.title,
                performer=task.performer,
                duration=duration,
                thumbnail=thumb_bytes,
                write_timeout=300,
                read_timeout=300,
                reply_to_message_id=task.message_id
            )
        except Exception as e:
            await self.bot.send_message(chat_id=task.chat_id,
                                        text=f"Upload Error for {os.path.basename(task.filename)}: {e}",
                                        reply_to_message_id=task.message_id)
            raise e

    @retry(
        retry=retry_if_exception_type((NetworkError, TimedOut)),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(5)
    )
    async def _send_cached_file_with_retry(self, chat_id: int, file_id: str, report_msg_id: int):
        try:
            await self.bot.send_audio(chat_id=chat_id, audio=file_id, reply_to_message_id=report_msg_id)
        except Exception as e:
            await self.bot.send_message(chat_id=chat_id, text=f"Cache Delivery Error: {e}",
                                        reply_to_message_id=report_msg_id)
            raise e

    async def _process_queue(self):
        while True:
            try:
                task: UploadTask = await self.queue.get()

                if task.cached_file_id:
                    try:
                        await self._send_cached_file_with_retry(task.chat_id, task.cached_file_id, task.message_id)
                    finally:
                        if task.on_upload_done: await task.on_upload_done(True, task.warning)
                        self.queue.task_done()
                    continue

                if not task.filename or not os.path.exists(task.filename):
                    if task.on_upload_done: await task.on_upload_done(False, task.warning)
                    self.queue.task_done()
                    continue

                success = False
                try:
                    if task.on_upload_start: await task.on_upload_start()
                    msg = await self._send_file_with_retry(task)
                    if msg and getattr(msg, 'audio', None) and task.cache_metadata:
                        await user_db.set_cache(
                            adam_id=task.cache_metadata["adam_id"],
                            codec=task.cache_metadata["codec"],
                            language=task.cache_metadata["language"],
                            file_id=msg.audio.file_id
                        )
                    success = True
                except Exception as e:
                    success = False
                finally:
                    if task.on_upload_done: await task.on_upload_done(success, task.warning)
                    if not bot_config.system.keep_file:
                        try:
                            os.remove(task.filename)
                        except OSError:
                            pass
                    self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Protect worker from dying due to unexpected errors
                print(f"UploadWorker unexpected error: {e}")


def check_disk_space(required_bytes: int = 5 * 1024 * 1024 * 1024) -> bool:
    """Check if there is at least `required_bytes` of free disk space in the downloads directory."""
    target_dir = os.path.abspath("downloads")
    if not os.path.exists(target_dir):
        os.makedirs(target_dir, exist_ok=True)
    total, used, free = shutil.disk_usage(target_dir)
    return free > required_bytes
