from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from extras.telegram_bot.src.auth import check_auth
from extras.telegram_bot.src.config import bot_config
from extras.telegram_bot.src.db import user_db


@check_auth
async def settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_settings = await user_db.get_user_settings(user_id)
    current_codec = user_settings.get("default_codec", bot_config.user_default.default_codec)
    current_lang = user_settings.get("language", bot_config.user_default.language)

    keyboard = [
        [InlineKeyboardButton(f"Codec: {current_codec}", callback_data="settings_menu_codec")],
        [InlineKeyboardButton(f"Language: {current_lang}", callback_data="settings_menu_lang")],
        [InlineKeyboardButton("Close", callback_data="settings_close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("**Download Settings**", reply_markup=reply_markup, parse_mode="Markdown")


async def settings_main_menu(query, user_id):
    user_settings = await user_db.get_user_settings(user_id)
    current_codec = user_settings.get("default_codec", bot_config.user_default.default_codec)
    current_lang = user_settings.get("language", bot_config.user_default.language)

    keyboard = [
        [InlineKeyboardButton(f"Codec: {current_codec}", callback_data="settings_menu_codec")],
        [InlineKeyboardButton(f"Language: {current_lang}", callback_data="settings_menu_lang")],
        [InlineKeyboardButton("Close", callback_data="settings_close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("**Download Settings**", reply_markup=reply_markup, parse_mode="Markdown")


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if not bot_config.system.whitelist_mode:
        if user_id in user_db.data["blacklist"]:
            await query.edit_message_text("Not authorized.")
            return
    else:
        if user_id not in user_db.data["whitelist"] and user_id not in bot_config.system.admin_ids:
            await query.edit_message_text("Not authorized.")
            return

    if data == "settings_close":
        await query.edit_message_text("Settings closed.", parse_mode="Markdown")
        return

    if data == "settings_menu_codec":
        codecs = ["alac", "ec3", "aac", "aac-binaural", "aac-downmix", "aac-legacy", "ac3"]
        keyboard = []
        for i in range(0, len(codecs), 2):
            row = [InlineKeyboardButton(c, callback_data=f"set_codec_{c}") for c in codecs[i:i + 2]]
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("Back", callback_data="settings_main")])
        await query.edit_message_text("Select default Codec:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "settings_menu_lang":
        langs = ["en-US", "en-GB", "zh-Hans-CN", "zh-Hant-HK", "zh-Hant-TW", "ja", "ko", "follow-user"]
        keyboard = []
        for i in range(0, len(langs), 2):
            row = [InlineKeyboardButton(l, callback_data=f"set_lang_{l}") for l in langs[i:i + 2]]
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("Back", callback_data="settings_main")])
        await query.edit_message_text("Select interface Language:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("set_codec_"):
        new_codec = data.replace("set_codec_", "")
        await user_db.update_user_settings(user_id, {"default_codec": new_codec})
        await settings_main_menu(query, user_id)

    elif data.startswith("set_lang_"):
        new_lang = data.replace("set_lang_", "")
        await user_db.update_user_settings(user_id, {"language": new_lang})
        await settings_main_menu(query, user_id)

    elif data == "settings_main":
        await settings_main_menu(query, user_id)
