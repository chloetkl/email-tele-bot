from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from telegram import Message, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
from email_client import Attachment, GmailApiClient, guess_content_type
from oauth_server import OAuthServer
from storage import CredentialStore
from validators import is_valid_email

logger = logging.getLogger(__name__)


FWD_TO, FWD_SUBJECT, FWD_BODY, FWD_FILES = range(4)


@dataclass
class PendingForward:
    to_address: str
    subject: str
    body: str
    attachments: List[Attachment]


def _get_store(context: CallbackContext) -> CredentialStore:
    return context.application.bot_data["store"]


def _get_gmail(context: CallbackContext) -> GmailApiClient:
    return context.application.bot_data.get("gmail")


def _get_oauth(context: CallbackContext) -> OAuthServer:
    return context.application.bot_data["oauth"]


async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Hi! Use /login to authenticate your Gmail sender.\n"
        "Then use /forward to compose and send an email with attachments.\n\n"
        "Commands:\n"
        "- /login\n"
        "- /forward\n"
        "- /cancel"
    )


async def cancel(update: Update, context: CallbackContext) -> int:
    context.user_data.pop("pending_forward", None)
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# -----------------
# /login flow
# -----------------
async def login_start(update: Update, context: CallbackContext) -> int:
    oauth = _get_oauth(context)
    setup_error = context.application.bot_data.get("oauth_setup_error")
    if setup_error:
        await update.message.reply_text(
            "OAuth is not configured yet.\n\n"
            f"{setup_error}\n\n"
            "Fix the issue (usually: download `client_secret.json` and set `GOOGLE_CLIENT_SECRETS_FILE` in `.env`), "
            "restart the bot, then run /login again.",
            disable_web_page_preview=True,
        )
        return ConversationHandler.END

    await update.message.chat.send_action(action=ChatAction.TYPING)
    auth_url = await oauth.create_auth_url(telegram_user_id=update.effective_user.id)

    await update.message.reply_text(
        "Open this link to sign in with Google and grant Gmail send access:\n"
        f"{auth_url}\n\n"
        "Important:\n"
        "- For local use, open this link in a browser on the same machine running the bot\n"
        f"  so it can reach the callback at: {oauth.callback_url}\n"
        "- After you approve, you'll see a “Login successful” page, then you can return here and use /forward.",
        disable_web_page_preview=True,
    )
    return ConversationHandler.END


async def on_error(update: object, context: CallbackContext) -> None:
    logger.exception("Unhandled error while handling update", exc_info=context.error)


# -----------------
# /forward flow
# -----------------
async def forward_start(update: Update, context: CallbackContext) -> int:
    store = _get_store(context)
    creds = store.get_gmail_oauth_credentials(telegram_user_id=update.effective_user.id)
    if not creds:
        await update.message.reply_text("You need to /login first.")
        return ConversationHandler.END

    if not _get_gmail(context):
        await update.message.reply_text("Gmail API is not configured on the server yet. Fix OAuth setup and restart the bot.")
        return ConversationHandler.END

    await update.message.reply_text("Email address to forward to?")
    return FWD_TO


async def forward_to(update: Update, context: CallbackContext) -> int:
    to_addr = (update.message.text or "").strip()
    if not is_valid_email(to_addr):
        await update.message.reply_text("That doesn't look like a valid email. Try again.")
        return FWD_TO

    context.user_data["pending_forward"] = PendingForward(
        to_address=to_addr,
        subject="",
        body="",
        attachments=[],
    )
    await update.message.reply_text("Subject? (optional) Send a message, or type '-' to skip.")
    return FWD_SUBJECT


async def forward_subject(update: Update, context: CallbackContext) -> int:
    pf: PendingForward = context.user_data["pending_forward"]
    text = (update.message.text or "").strip()
    pf.subject = "" if text == "-" else text
    await update.message.reply_text("Body? (optional) Send a message, or type '-' to skip.")
    return FWD_BODY


async def forward_body(update: Update, context: CallbackContext) -> int:
    pf: PendingForward = context.user_data["pending_forward"]
    text = (update.message.text or "").strip()
    pf.body = "" if text == "-" else text

    await update.message.reply_text(
        "Now send the files/photos to forward.\n"
        "- You can *forward* files from other chats into this chat\n"
        "- Or upload a new document/photo\n\n"
        "When you're done, send /done.\n"
        "To cancel, send /cancel.",
        parse_mode="Markdown",
    )
    return FWD_FILES


async def forward_receive_file(update: Update, context: CallbackContext) -> int:
    message: Message = update.message
    pf: PendingForward = context.user_data.get("pending_forward")
    if not pf:
        await message.reply_text("Please start with /forward.")
        return ConversationHandler.END

    # Accept document or photo. For photo, pick the largest size.
    tg_file = None
    filename = None

    if message.document:
        tg_file = message.document
        filename = message.document.file_name or "document"
    elif message.photo:
        tg_file = message.photo[-1]
        filename = "photo.jpg"
    else:
        await message.reply_text("Please send a document or photo, or /done to send.")
        return FWD_FILES

    await message.chat.send_action(action=ChatAction.UPLOAD_DOCUMENT)
    file_obj = await tg_file.get_file()
    data = await file_obj.download_as_bytearray()
    data_bytes = bytes(data)

    content_type = getattr(tg_file, "mime_type", None) or guess_content_type(filename)
    pf.attachments.append(Attachment(filename=filename, content_type=content_type, data=data_bytes))

    await message.reply_text(f"Added attachment: {filename} ({len(data_bytes)} bytes). Send more, or /done.")
    return FWD_FILES


async def forward_done(update: Update, context: CallbackContext) -> int:
    pf: PendingForward = context.user_data.get("pending_forward")
    if not pf:
        await update.message.reply_text("Nothing to send. Start with /forward.")
        return ConversationHandler.END

    if not pf.attachments:
        await update.message.reply_text("No attachments received yet. Send a file/photo first, or /cancel.")
        return FWD_FILES

    store = _get_store(context)
    creds = store.get_gmail_oauth_credentials(telegram_user_id=update.effective_user.id)
    if not creds:
        await update.message.reply_text("Your login is missing/expired. Please /login again.")
        return ConversationHandler.END

    gmail = _get_gmail(context)
    if not gmail:
        await update.message.reply_text("Gmail API is not configured on the server yet. Fix OAuth setup and restart the bot.")
        return ConversationHandler.END
    await update.message.chat.send_action(action=ChatAction.TYPING)
    try:
        message_id = gmail.send_email(
            refresh_token=creds.refresh_token,
            to_address=pf.to_address,
            subject=pf.subject,
            body=pf.body,
            attachments=pf.attachments,
        )
    except Exception as e:
        logger.exception("Send failed")
        await update.message.reply_text(f"Send failed: {type(e).__name__}: {e}")
        return ConversationHandler.END
    finally:
        context.user_data.pop("pending_forward", None)

    await update.message.reply_text(f"Sent successfully. Gmail message id: {message_id or '(unknown)'}")
    return ConversationHandler.END


def build_app() -> Application:
    logging.basicConfig(level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))

    store = CredentialStore(db_path=config.DATABASE_PATH, encryption_key=config.ENCRYPTION_KEY)

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.bot_data["store"] = store

    # Load client_id/client_secret/token_uri from Google secrets JSON.
    # If missing, still start the bot (but /login will show a setup error).
    import json

    gmail: GmailApiClient | None = None
    oauth_setup_error: str | None = None
    try:
        with open(config.GOOGLE_CLIENT_SECRETS_FILE, "r", encoding="utf-8") as f:
            secrets_json = json.load(f)
        secrets_section = secrets_json.get("installed") or secrets_json.get("web") or {}
        client_id = secrets_section.get("client_id", "")
        client_secret = secrets_section.get("client_secret", "")
        token_uri = secrets_section.get("token_uri", "https://oauth2.googleapis.com/token")
        if not client_id or not client_secret:
            raise RuntimeError(
                "Client secrets JSON missing client_id/client_secret (expected 'installed' or 'web' section)."
            )
        gmail = GmailApiClient(client_id=client_id, client_secret=client_secret, token_uri=token_uri)
    except FileNotFoundError:
        oauth_setup_error = (
            f"Missing Google client secrets file: `{config.GOOGLE_CLIENT_SECRETS_FILE}`.\n"
            "Download it from Google Cloud Console (OAuth client ID) and place it at that path."
        )
    except Exception as e:
        oauth_setup_error = f"OAuth setup error: {type(e).__name__}: {e}"

    app.bot_data["gmail"] = gmail
    app.bot_data["oauth_setup_error"] = oauth_setup_error

    async def on_oauth_success(telegram_user_id: int, refresh_token: str) -> None:
        try:
            if not gmail:
                await app.bot.send_message(chat_id=telegram_user_id, text="Login succeeded but Gmail API is not configured on the server.")
                return
            store.set_gmail_oauth_credentials(
                telegram_user_id=telegram_user_id,
                gmail_address="me",
                refresh_token=refresh_token,
            )
            await app.bot.send_message(chat_id=telegram_user_id, text="Login successful. You can now use /forward.")
        except Exception as e:
            logger.exception("OAuth success handler failed")
            await app.bot.send_message(chat_id=telegram_user_id, text=f"Login completed but saving failed: {type(e).__name__}: {e}")

    async def on_oauth_error(telegram_user_id: int, message: str) -> None:
        await app.bot.send_message(chat_id=telegram_user_id, text=f"Login failed: {message}")

    oauth = OAuthServer(
        port=config.OAUTH_PORT,
        redirect_base=config.OAUTH_REDIRECT_BASE,
        client_secrets_file=config.GOOGLE_CLIENT_SECRETS_FILE,
        on_success=on_oauth_success,
        on_error=on_oauth_error,
    )
    app.bot_data["oauth"] = oauth

    forward_conv = ConversationHandler(
        entry_points=[CommandHandler("forward", forward_start)],
        states={
            FWD_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to)],
            FWD_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, forward_subject)],
            FWD_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, forward_body)],
            FWD_FILES: [
                CommandHandler("done", forward_done),
                MessageHandler(filters.Document.ALL | filters.PHOTO, forward_receive_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, forward_receive_file),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="forward",
        persistent=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("login", login_start))
    app.add_handler(forward_conv)
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_error_handler(on_error)

    return app


def main() -> None:
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

