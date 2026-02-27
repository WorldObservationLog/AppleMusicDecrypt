from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from extras.telegram_bot.src.config import bot_config
from extras.telegram_bot.src.db import user_db


def is_admin(user_id: int) -> bool:
    return user_id in bot_config.system.admin_ids


def require_admin(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if not is_admin(user_id):
            await update.message.reply_text("Permission denied. Admin only.")
            return None
        return await func(update, context, *args, **kwargs)

    return wrapper


def check_auth(func):
    """
    Checks if a user can use normal commands based on whitelist/blacklist.
    """

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id

        if is_admin(user_id):
            return await func(update, context, *args, **kwargs)

        if user_db.is_blacklisted(user_id):
            await update.message.reply_text("Permission denied. You are blacklisted.")
            return None

        if bot_config.system.whitelist_mode and not user_db.is_whitelisted(user_id):
            await update.message.reply_text("Whitelist mode is enabled. You are not allowed to use this bot.")
            return None

        return await func(update, context, *args, **kwargs)

    return wrapper
