from telegram import Update
from telegram.ext import ContextTypes

from extras.telegram_bot.src.auth import require_admin
from extras.telegram_bot.src.config import bot_config
from extras.telegram_bot.src.db import user_db
from src.config import Config
from src.grpc.manager import WrapperManager
from src.task import Status
from creart import it


@require_admin
async def logout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /logout <username>\nPlease provide the Apple ID to logout.")
        return

    username = context.args[0]
    await it(WrapperManager).init(it(Config).instance.url, it(Config).instance.secure)
    try:
        await it(WrapperManager).logout(username)
        await update.message.reply_text(f"Logout Success for {username}!")
        it(WrapperManager).status.cache_invalidate()
    except Exception as e:
        await update.message.reply_text(f"Logout Failed: {e}")


@require_admin
async def whitelist_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /whitelist <add/remove> <user_id>")
        return
    action = context.args[0]
    try:
        target_id = int(context.args[1])
        if action == "add":
            await user_db.add_whitelist(target_id)
            await update.message.reply_text(f"Added {target_id} to whitelist.")
        elif action == "remove":
            await user_db.remove_whitelist(target_id)
            await update.message.reply_text(f"Removed {target_id} from whitelist.")
    except Exception:
        await update.message.reply_text("Invalid arguments.")


@require_admin
async def blacklist_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /blacklist <add/remove> <user_id>")
        return
    action = context.args[0]
    try:
        target_id = int(context.args[1])
        if action == "add":
            await user_db.add_blacklist(target_id)
            await update.message.reply_text(f"Added {target_id} to blacklist.")
        elif action == "remove":
            await user_db.remove_blacklist(target_id)
            await update.message.reply_text(f"Removed {target_id} from blacklist.")
    except Exception:
        await update.message.reply_text("Invalid arguments.")


from extras.telegram_bot.src.auth import check_auth

@check_auth
async def gstatus_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ripper = context.bot_data.get("ripper")
    if not ripper:
        return

    regions = (await it(WrapperManager).status()).regions
    active_tasks = sum(1 for t in ripper.download_manager.adam_id_task_mapping.values() if
                       t.status not in (Status.DONE, Status.FAILED))
    queued_uploads = context.bot_data.get("upload_worker").queue.qsize() if context.bot_data.get("upload_worker") else 0
    total_tasks = len(ripper.download_manager.adam_id_task_mapping)

    text = (
        "**Global Bot Status**\n\n"
        f"**Instance Region: ** `{' '.join(regions)}`\n"
        f"**Active Downloads: ** `{active_tasks}`\n"
        f"**Upload Queue: ** `{queued_uploads}`\n"
        f"**Total Handled Tasks: ** `{total_tasks}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")
