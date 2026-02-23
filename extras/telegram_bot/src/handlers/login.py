import asyncio

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CommandHandler

from extras.telegram_bot.src.auth import require_admin
from src.config import Config
from src.grpc.manager import WrapperManager
from creart import it

# --- Login ConversationHandler states ---
WAIT_USERNAME = 1
WAIT_PASSWORD = 2
WAIT_2FA = 3


@require_admin
async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await it(WrapperManager).init(it(Config).instance.url, it(Config).instance.secure)
    await update.message.reply_text(
        "Initiating Apple Music Login.\nPlease enter your Apple ID Username:\nSend /cancel to abort.")
    return WAIT_USERNAME


async def login_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['login_username'] = update.message.text
    await update.message.reply_text("Please enter your Password:")
    return WAIT_PASSWORD


async def login_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['login_password'] = update.message.text
    msg = await update.message.reply_text("Authenticating...")

    username = context.user_data['login_username']
    password = context.user_data['login_password']

    # We use a Future to track the 2FA string result from the user, making it easier to manage
    from asyncio import Future
    two_fa_future = Future()
    context.user_data['2fa_future'] = two_fa_future

    async def on_2fa(usr: str, pwd: str):
        await msg.edit_text("2FA Required! Please type the 6-digit code sent to your devices:")
        return await two_fa_future

    try:
        login_task = asyncio.create_task(it(WrapperManager).login(username, password, on_2fa))
        context.user_data['login_task'] = login_task

        done, pending = await asyncio.wait([login_task], timeout=5.0)
        if login_task in pending:
            # It's waiting for 2FA
            return WAIT_2FA
        else:
            # Finished without 2FA or failed immediately
            res = login_task.result()
            await msg.edit_text("Login Success!")
            it(WrapperManager).status.cache_invalidate()
            return ConversationHandler.END

    except Exception as e:
        await msg.edit_text(f"Login Failed: {e}")
        return ConversationHandler.END


async def login_2fa_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['login_2fa_code'] = update.message.text
    msg = await update.message.reply_text("Verifying 2FA...")

    future = context.user_data.get('2fa_future')
    if future and not future.done():
        future.set_result(context.user_data['login_2fa_code'])

    login_task = context.user_data.get('login_task')
    if login_task:
        try:
            await login_task
            await msg.edit_text("Login Success!")
            it(WrapperManager).status.cache_invalidate()
        except asyncio.CancelledError:
            await msg.edit_text("Login Cancelled.")
        except Exception as e:
            await msg.edit_text(f"Login Failed during 2FA: {e}")
        finally:
            # Always clean up tasks
            context.user_data.pop('login_task', None)
            context.user_data.pop('2fa_future', None)

    return ConversationHandler.END


async def login_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Login aborted.")
    login_task = context.user_data.get('login_task')
    if login_task and not login_task.done():
        login_task.cancel()
        
    future = context.user_data.get('2fa_future')
    if future and not future.done():
        future.cancel()
        
    context.user_data.clear()
    return ConversationHandler.END


def get_login_handler():
    return ConversationHandler(
        entry_points=[CommandHandler('login', login_start)],
        states={
            WAIT_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_user)],
            WAIT_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_pass)],
            WAIT_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_2fa_receive)]
        },
        fallbacks=[CommandHandler('cancel', login_cancel)],
        allow_reentry=True
    )
