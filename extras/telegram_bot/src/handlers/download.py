import asyncio
from tabulate import tabulate

from telegram import Update
from telegram.ext import ContextTypes

import m3u8
from creart import it

from extras.telegram_bot.src.auth import check_auth
from extras.telegram_bot.src.config import bot_config
from extras.telegram_bot.src.db import user_db
from extras.telegram_bot.src.handlers.notifications import telegram_tasks_listeners
from extras.telegram_bot.src.upload import UploadTask, check_disk_space

from src.api import WebAPI
from src.config import Config
from src.flags import Flags
from src.grpc.manager import WrapperManager
from src.metadata import SongMetadata
from src.task import Status
from src.types import ParentDoneHandler, Codec
from src.url import AppleMusicURL, URLType, Song
from src.utils import get_codec_from_codec_id, safely_create_task, playlist_write_song_index


@check_auth
async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ripper = context.bot_data.get("ripper")
    if not ripper:
        return

    chat_id = update.effective_chat.id
    active_ids = []
    for k, v in telegram_tasks_listeners.items():
        if any(listener["chat_id"] == chat_id for listener in v):
            active_ids.append(k)

    tasks_str = []
    for track_id in active_ids:
        t = ripper.download_manager.get_task(track_id)
        if t:
            title = t.metadata.title if t.metadata else 'Unknown'
            tasks_str.append(f"- [{track_id[-4:]}] {title}: {t.status.value}")

    if not tasks_str:
        await update.message.reply_text("No active tasks for you.")
        return

    text = "Your Active Tasks:\n" + "\n".join(tasks_str)
    await update.message.reply_text(text)


@check_auth
async def quality_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        codecs = it(Config).download.codecPriority
        await update.message.reply_text(
            f"Available system codecs: {', '.join(codecs)}\n\nUsage to inspect a song: `/quality <url>`",
            parse_mode="Markdown")
        return

    raw_url = context.args[0]
    url_obj = AppleMusicURL.parse_url(raw_url)
    if not url_obj or url_obj.type != URLType.Song:
        await update.message.reply_text("Please provide a valid single Apple Music Song URL.")
        return

    msg = await update.message.reply_text("Fetching audio qualities...")
    try:
        user_settings = await user_db.get_user_settings(update.effective_user.id)
        language = user_settings.get("language", bot_config.user_default.language)
        if language == "follow-user":
            language = update.effective_user.language_code or it(Config).region.language

        m3u8_url = await it(WrapperManager).m3u8(url_obj.id)
        if not m3u8_url:
            await msg.edit_text("Failed to get M3U8 URL from WrapperManager.")
            return

        raw_metadata = await it(WebAPI).get_song_info(url_obj.id, url_obj.storefront, language)
        if not raw_metadata:
            await msg.edit_text("Failed to fetch song metadata.")
            return

        metadata = SongMetadata.parse_from_song_data(raw_metadata)
        parsed_m3u8 = m3u8.loads(await it(WebAPI).download_m3u8(m3u8_url), uri=m3u8_url)

        headers = ["Codec ID", "Codec", "Bitrate", "Average Bitrate", "Channels", "Sample Rate", "Bit Depth"]
        table_data = []
        for playlist in parsed_m3u8.playlists:
            codec = get_codec_from_codec_id(playlist.stream_info.audio)
            if codec:
                codec_id = playlist.stream_info.audio
                bitrate = playlist.stream_info.bandwidth
                average_bitrate = getattr(playlist.stream_info, "average_bandwidth", None)
                channels = playlist.media[0].channels if playlist.media else None
                sample_rate = playlist.media[0].extras.get("sample_rate", None) if playlist.media else None
                bit_depth = playlist.media[0].extras.get("bit_depth", None) if playlist.media else None
                table_data.append([codec_id, codec, bitrate, average_bitrate, channels, sample_rate, bit_depth])

        if not table_data:
            await msg.edit_text("No playable audio tracks found in M3U8.")
            return

        table_str = tabulate(table_data, headers=headers, tablefmt="presto")
        title_text = f"Available audio qualities for song: {metadata.artist} - {metadata.title}\n"
        await msg.edit_text(f"{title_text}```text\n{table_str}\n```", parse_mode="Markdown")

    except Exception as e:
        await msg.edit_text(f"Error checking quality: {str(e)}")


@check_auth
async def dl_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/dl <url> [-f] [-c codec]`", parse_mode="Markdown")
        return

    force_download = False
    codec_override = None
    url_str = None

    i = 0
    while i < len(args):
        if args[i] == '-f':
            force_download = True
            i += 1
        elif args[i] == '-c':
            if i + 1 < len(args):
                codec_override = args[i + 1]
                i += 2
            else:
                await update.message.reply_text("Missing codec value after `-c`", parse_mode="Markdown")
                return
        else:
            url_str = args[i]
            i += 1

    if not url_str:
        await update.message.reply_text("Missing Apple Music URL.")
        return

    raw_url = url_str
    url_obj = AppleMusicURL.parse_url(raw_url)
    if not url_obj:
        await update.message.reply_text("Invalid Apple Music URL.")
        return

    if url_obj.type.lower() not in bot_config.limits.allowed_types:
        await update.message.reply_text(
            f"Error: Downloading {url_obj.type} is disabled by the limits config.")
        return

    msg = await update.message.reply_text(f"Fetching {url_obj.type} metadata...")

    user_settings = await user_db.get_user_settings(update.effective_user.id)
    codec = codec_override if codec_override else user_settings.get("default_codec",
                                                                    bot_config.user_default.default_codec)
    SUPPORTED_CODECS = ["alac", "ec3", "aac", "aac-binaural", "aac-downmix", "aac-legacy", "ac3"]
    if codec.lower() not in SUPPORTED_CODECS:
        await update.message.reply_text(f"Invalid codec `{codec}`. Available: {', '.join(SUPPORTED_CODECS)}",
                                        parse_mode="Markdown")
        return
    codec = codec.lower()

    language = user_settings.get("language", bot_config.user_default.language)
    if language == "follow-user":
        tg_lang = update.effective_user.language_code
        lang_map = {
            "zh-hans": "zh-Hans-CN",
            "zh-hant": "zh-Hant-TW",
            "en": "en-US"
        }
        language = lang_map.get(tg_lang.lower(), tg_lang) if tg_lang else it(Config).region.language

    loose_cache = bot_config.system.loose_cache

    if not check_disk_space(5 * 1024 * 1024 * 1024):
        await update.message.reply_text("Error: Request rejected. Bot server has less than 5GB of free disk space.")
        for admin_id in bot_config.system.admin_ids:
            try:
                await context.bot.send_message(chat_id=admin_id,
                                               text="CRITICAL: Disk space has dropped below safety threshold (< 5GB). Please clean up!")
            except Exception:
                pass
        return

    # Check Global and User Quotas
    current_global_tasks = 0
    current_user_tasks = 0
    user_id = update.effective_user.id
    for song_id, listeners in telegram_tasks_listeners.items():
        if listeners:
            current_global_tasks += 1
            if any(l["chat_id"] == update.effective_chat.id for l in listeners):  # Use chat_id loosely as user isolation here
                current_user_tasks += 1

    if current_global_tasks >= bot_config.limits.max_tasks_global:
        await msg.edit_text(
            f"Error: Global task limit reached ({bot_config.limits.max_tasks_global}). Please try again later.")
        return

    if current_user_tasks >= bot_config.limits.max_tasks_per_user:
        await msg.edit_text(
            f"Error: Your personal task limit reached ({bot_config.limits.max_tasks_per_user}). Please wait for your current tasks to finish.")
        return

    songs_to_rip = []

    # Pre-flight Check Length & Size Limits
    if url_obj.type == URLType.Song:
        try:
            song_info = await it(WebAPI).get_song_info(url_obj.id, url_obj.storefront, language)
            if song_info and song_info.data:
                duration_ms = song_info.data[0].attributes.durationInMillis
                duration_sec = duration_ms / 1000
                if duration_sec > bot_config.limits.max_song_duration_sec:
                    await msg.edit_text(
                        f"Error: Song duration ({duration_sec}s) exceeds the limit of {bot_config.limits.max_song_duration_sec}s.")
                    return

                estimated_bytes = duration_sec * 375 * 1024
                if estimated_bytes > 1.95 * 1024 ** 3:
                    await msg.edit_text(
                        f"Estimated file size ({estimated_bytes / 1024 ** 3:.2f} GB) exceeds 1.95 GB limit. Task rejected.")
                    return
        except Exception:
            pass
        songs_to_rip.append((Song(id=url_obj.id, storefront=url_obj.storefront, url="", type=URLType.Song), None))

    elif url_obj.type == URLType.Album:
        album_info = await it(WebAPI).get_album_info(url_obj.id, url_obj.storefront, language)
        if album_info and album_info.data:
            tracks = album_info.data[0].relationships.tracks.data
            if len(tracks) > bot_config.limits.max_tracks:
                await msg.edit_text(
                    f"Error: Album tracks ({len(tracks)}) exceeds the limit of {bot_config.limits.max_tracks}.")
                return

            total_duration_sec = sum(
                [t.attributes.durationInMillis / 1000 for t in tracks if getattr(t.attributes, 'durationInMillis', 0)])
            if total_duration_sec > bot_config.limits.max_total_duration_sec:
                await msg.edit_text(
                    f"Error: Album total duration ({total_duration_sec}s) exceeds the limit of {bot_config.limits.max_total_duration_sec}s.")
                return

            for track in tracks:
                songs_to_rip.append((Song(id=track.id, storefront=url_obj.storefront, url="", type=URLType.Song), None))

    elif url_obj.type == URLType.Playlist:
        playlist_info = await it(WebAPI).get_playlist_info_and_tracks(url_obj.id, url_obj.storefront, language)
        if playlist_info and playlist_info.data:
            tracks = playlist_info.data[0].relationships.tracks.data
            if len(tracks) > bot_config.limits.max_tracks:
                await msg.edit_text(
                    f"Error: Playlist tracks ({len(tracks)}) exceeds the limit of {bot_config.limits.max_tracks}.")
                return

            total_duration_sec = sum(
                [t.attributes.durationInMillis / 1000 for t in tracks if getattr(t.attributes, 'durationInMillis', 0)])
            if total_duration_sec > bot_config.limits.max_total_duration_sec:
                await msg.edit_text(
                    f"Error: Playlist total duration ({total_duration_sec}s) exceeds the limit of {bot_config.limits.max_total_duration_sec}s.")
                return

            playlist_info = playlist_write_song_index(playlist_info)
            for track in playlist_info.data[0].relationships.tracks.data:
                songs_to_rip.append(
                    (Song(id=track.id, storefront=url_obj.storefront, url="", type=URLType.Song), playlist_info))

    elif url_obj.type == URLType.Artist:
        artist_info = await it(WebAPI).get_artist_info(url_obj.id, url_obj.storefront, language)
        if artist_info and artist_info.data:
            albums = getattr(artist_info.data[0].relationships.albums, 'data', []) if getattr(
                artist_info.data[0].relationships, 'albums', None) else []
            all_tracks = []
            for album in albums:
                album_info = await it(WebAPI).get_album_info(album.id, url_obj.storefront, language)
                if album_info and album_info.data:
                    all_tracks.extend(album_info.data[0].relationships.tracks.data)

            if len(all_tracks) > bot_config.limits.max_tracks:
                await msg.edit_text(
                    f"Error: Artist tracks ({len(all_tracks)}) exceeds the limit of {bot_config.limits.max_tracks}.")
                return

            total_duration_sec = sum([t.attributes.durationInMillis / 1000 for t in all_tracks if
                                      getattr(t.attributes, 'durationInMillis', 0)])
            if total_duration_sec > bot_config.limits.max_total_duration_sec:
                await msg.edit_text(
                    f"Error: Artist total duration ({total_duration_sec}s) exceeds the limit of {bot_config.limits.max_total_duration_sec}s.")
                return

            for track in all_tracks:
                songs_to_rip.append((Song(id=track.id, storefront=url_obj.storefront, url="", type=URLType.Song), None))

    if not songs_to_rip:
        await msg.edit_text("No tracks found or unsupported URL structure.")
        return

    req_delta = len(songs_to_rip)
    if current_global_tasks + req_delta > bot_config.limits.max_tasks_global:
        await msg.edit_text(
            f"Error: Adding {req_delta} tasks would exceed the global limit ({bot_config.limits.max_tasks_global}).")
        return

    if current_user_tasks + req_delta > bot_config.limits.max_tasks_per_user:
        await msg.edit_text(
            f"Error: Adding {req_delta} tasks would exceed your personal limit ({bot_config.limits.max_tasks_per_user}).")
        return

    await msg.edit_text(f"Task passes limit verification. Added {req_delta} items.")

    ripper = context.bot_data["ripper"]
    upload_worker = context.bot_data["upload_worker"]

    state = {
        "tasks": {},
        "last_text": "",
        "done": False
    }

    async def update_loop():
        try:
            while not state["done"]:
                await asyncio.sleep(4)
                if not state["tasks"]:
                    continue

                # Update statuses cleanly
                for track_id in list(state["tasks"].keys()):
                    if "\u2728" in state["tasks"][track_id] or "\u26A1" in state["tasks"][track_id] or "\u2B06" in \
                            state["tasks"][track_id] or "\u274C" in state["tasks"][track_id] or "Finished" in state["tasks"][track_id] or "Error" in state["tasks"][track_id]:
                        continue
                        
                    t = ripper.download_manager.get_task(track_id)
                    if t:
                        if t.status == Status.FAILED:
                            if t.error:
                                new_val = f"[{track_id[-4:]}] Error ({t.error})"
                            else:
                                new_val = f"[{track_id[-4:]}] Error"
                        else:
                            title = t.metadata.title if t.metadata else 'Unknown'
                            new_val = f"[{track_id[-4:]}] {title} : {t.status.value}"
                        
                        if state["tasks"].get(track_id) != new_val:
                            state["tasks"][track_id] = new_val
                    else:
                        # Task is unrecorded (likely unregistered from the queue after finishing download)
                        # We should not display stale status like DECRYPTING
                        current_status = state["tasks"].get(track_id, "")
                        if "QUEUED" not in current_status:
                            parts = current_status.rsplit(":", 1)
                            prefix = parts[0].strip() if len(parts) > 1 else f"[{track_id[-4:]}] Unknown"
                            new_val = f"{prefix} : Pending Upload \u23F3"
                            if state["tasks"].get(track_id) != new_val:
                                state["tasks"][track_id] = new_val
                            
                text = "Active Tasks:\n" + "\n".join(state["tasks"].values())
                if text != state["last_text"]:
                    try:
                        await context.bot.edit_message_text(text[:4000], chat_id=msg.chat_id, message_id=msg.message_id)
                        state["last_text"] = text
                    except Exception:
                        pass
        finally:
            pass

    loop_task = asyncio.create_task(update_loop())

    flags = Flags(force_save=force_download, language=language)

    completed_event = asyncio.Event()

    async def on_all_done():
        completed_event.set()

    upload_event = asyncio.Event()
    pending_uploads = 0

    def make_on_start(s_id):
        async def on_s():
            current = state["tasks"].get(s_id, "")
            parts = current.rsplit(":", 1)
            prefix = parts[0].strip() if len(parts) > 1 else f"[{s_id[-4:]}] Unknown"
            state["tasks"][s_id] = f"{prefix} : Uploading \u2B06"

        return on_s

    def make_on_done(s_id):
        async def on_d(success: bool, warning=None):
            nonlocal pending_uploads
            current = state["tasks"].get(s_id, "")
            parts = current.rsplit(":", 1)
            prefix = parts[0].strip() if len(parts) > 1 else f"[{s_id[-4:]}] Unknown"
            if success:
                if warning:
                    state["tasks"][s_id] = f"{prefix} : Finished ({warning})"
                else:
                    state["tasks"][s_id] = f"{prefix} : Finished"
            else:
                if warning:
                    state["tasks"][s_id] = f"{prefix} : Error ({warning})"
                else:
                    state["tasks"][s_id] = f"{prefix} : Error"

            pending_uploads -= 1
            if pending_uploads <= 0:
                upload_event.set()

        return on_d

    # Process each song eagerly validated
    async def process_song(song_id: str, storefront: str, p_index=None):
        if not force_download:
            file_id = await user_db.get_cache(song_id, codec, language, loose_cache)
            if file_id:
                state["tasks"][song_id] = f"[{song_id[-4:]}] Cached \u26A1"
                await upload_worker.enqueue(UploadTask(
                    chat_id=msg.chat_id,
                    filename=None,
                    message_id=msg.message_id,
                    cached_file_id=file_id
                ))
                return True

        state["tasks"][song_id] = f"[{song_id[-4:]}] QUEUED"
        telegram_tasks_listeners[song_id].append({
            "chat_id": update.effective_chat.id,
            "message_id": msg.message_id,
            "codec": codec,
            "language": language,
            "on_upload_start": make_on_start(song_id),
            "on_upload_done": make_on_done(song_id)
        })
        return False

    actual_songs_to_rip = []
    for song_obj, p_index in songs_to_rip:
        is_cached = await process_song(song_obj.id, song_obj.storefront, p_index)
        if not is_cached:
            actual_songs_to_rip.append((song_obj, p_index))

    parent_done = ParentDoneHandler(len(actual_songs_to_rip) if actual_songs_to_rip else 1, on_all_done)

    pending_uploads = len(actual_songs_to_rip)
    if pending_uploads == 0:
        upload_event.set()

    # Spawn Rip Tasks
    if actual_songs_to_rip:
        for song_obj, p_index in actual_songs_to_rip:
            safely_create_task(ripper.rip_song(song_obj, codec, flags, parent_done=parent_done, playlist=p_index, timeout_sec=bot_config.limits.task_timeout_sec))
    else:
        # Everything was cached
        completed_event.set()

    # Wait for completion
    try:
        await completed_event.wait()
        await upload_event.wait()
    finally:
        state["done"] = True
        loop_task.cancel()

    # Final Update
    text = "All requested tasks completed.\n\nFinal Status:\n" + "\n".join(state["tasks"].values())
    try:
        await context.bot.edit_message_text(text[:4000], chat_id=msg.chat_id, message_id=msg.message_id)
    except Exception:
        pass
