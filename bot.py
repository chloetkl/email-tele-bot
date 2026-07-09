from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from google.auth.exceptions import RefreshError
from telegram import Update
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

DEV_ERROR_FOOTER = (
    "\n\n[developer error detected: please forward this message to the developer. we'll get back to you shortly]"
)


def _dev_error_text(body: str) -> str:
    return body.rstrip() + DEV_ERROR_FOOTER


FILE_STAGING_KEY = "file_staging"

FWD_TO, FWD_SUBJECT, FWD_BODY, FWD_SEND = range(4)


@dataclass
class PendingForward:
    to_address: str
    subject: str
    body: str
    attachments: List[Attachment]


def _get_store(context: CallbackContext) -> CredentialStore:
    return context.application.bot_data["store"]


def _get_gmail(context: CallbackContext) -> Optional[GmailApiClient]:
    return context.application.bot_data.get("gmail")


def _get_oauth(context: CallbackContext) -> OAuthServer:
    return context.application.bot_data["oauth"]


def _staging(context: CallbackContext) -> List[Attachment]:
    return context.user_data.setdefault(FILE_STAGING_KEY, [])


async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "hello! send files/photos here. when all your files have been shared, use /forward to send them to your email.\n"
        "/clear — empty staged files\n"
        "/login — Gmail OAuth\n"
        "/cancel — cancel compose\n"
    )


async def clear_stash(update: Update, context: CallbackContext) -> None:
    context.user_data[FILE_STAGING_KEY] = []
    await update.message.reply_text("Staged files cleared.")


async def cancel(update: Update, context: CallbackContext) -> int:
    context.user_data.pop("pending_forward", None)
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


async def login_start(update: Update, context: CallbackContext) -> int:
    oauth = _get_oauth(context)
    setup_error = context.application.bot_data.get("oauth_setup_error")
    if setup_error:
        await update.message.reply_text(
            _dev_error_text(
                "OAuth is not configured yet.\n\n"
                f"{setup_error}\n\n"
                "Fix the issue, restart the bot, then run /login again."
            ),
            disable_web_page_preview=True,
        )
        return ConversationHandler.END

    await update.message.chat.send_action(action=ChatAction.TYPING)
    auth_url = await oauth.create_auth_url(telegram_user_id=update.effective_user.id)

    await update.message.reply_text(
        "Open this link to sign in with Google:\n"
        f"{auth_url}\n\n"
        f"Callback: {oauth.callback_url}\n"
        "After approval, return here and use /forward.",
        disable_web_page_preview=True,
    )
    return ConversationHandler.END


async def on_error(update: object, context: CallbackContext) -> None:
    logger.exception("Unhandled error while handling update", exc_info=context.error)
    err = context.error
    if update is None or err is None:
        return
    msg = getattr(update, "effective_message", None) or getattr(update, "message", None)
    if msg is None:
        return
    detail = f"{type(err).__name__}: {err}"
    try:
        await msg.reply_text(_dev_error_text(detail))
    except Exception:
        logger.exception("Failed to send dev error notice to user")


async def stage_file(update: Update, context: CallbackContext) -> None:
    if context.user_data.get("pending_forward"):
        return

    message = update.message
    if not message:
        return

    tg_file = None
    filename = None
    if message.document:
        tg_file = message.document
        filename = message.document.file_name or "document"
    elif message.photo:
        tg_file = message.photo[-1]
        filename = "photo.jpg"
    else:
        return

    await message.chat.send_action(action=ChatAction.UPLOAD_DOCUMENT)
    file_obj = await tg_file.get_file()
    data = await file_obj.download_as_bytearray()
    data_bytes = bytes(data)
    content_type = getattr(tg_file, "mime_type", None) or guess_content_type(filename)
    att = Attachment(filename=filename, content_type=content_type, data=data_bytes)
    _staging(context).append(att)

    n = len(_staging(context))
    await message.reply_text(f"Staged {n} file(s). /forward when ready, /clear to reset.")


async def forward_start(update: Update, context: CallbackContext) -> int:
    staging = list(_staging(context))
    if not staging:
        await update.message.reply_text("Send at least one file/photo first, then /forward.")
        return ConversationHandler.END

    if not _get_gmail(context):
        await update.message.reply_text(_dev_error_text("Gmail API not configured on server."))
        return ConversationHandler.END

    context.user_data["file_staging"] = []
    context.user_data["pending_forward"] = PendingForward(
        to_address="",
        subject="",
        body="",
        attachments=staging,
    )

    store = _get_store(context)
    past = store.list_recipient_history(telegram_user_id=update.effective_user.id)
    lines = ["Who should receive this email?"]
    if past:
        lines.append("Pick a number or type a new address:")
        for i, e in enumerate(past, start=1):
            lines.append(f"{i}. {e}")
    else:
        lines.append("Type the recipient email address:")
    await update.message.reply_text("\n".join(lines))
    return FWD_TO


async def forward_to(update: Update, context: CallbackContext) -> int:
    pf: PendingForward = context.user_data["pending_forward"]
    text = (update.message.text or "").strip()
    store = _get_store(context)
    past = store.list_recipient_history(telegram_user_id=update.effective_user.id)

    chosen: Optional[str] = None
    if text.isdigit():
        idx = int(text)
        if 1 <= idx <= len(past):
            chosen = past[idx - 1]
    if chosen is None and is_valid_email(text):
        chosen = text

    if not chosen:
        await update.message.reply_text("Invalid choice. Send a list number or a valid email.")
        return FWD_TO

    pf.to_address = chosen
    await update.message.reply_text("Subject? (optional) Send text, or '-' to skip.")
    return FWD_SUBJECT


async def forward_subject(update: Update, context: CallbackContext) -> int:
    pf: PendingForward = context.user_data["pending_forward"]
    text = (update.message.text or "").strip()
    pf.subject = "" if text == "-" else text
    await update.message.reply_text("Body? (optional) Send text, or '-' to skip.")
    return FWD_BODY


async def forward_body(update: Update, context: CallbackContext) -> int:
    pf: PendingForward = context.user_data["pending_forward"]
    text = (update.message.text or "").strip()
    pf.body = "" if text == "-" else text
    await update.message.reply_text("Send /send to mail, or /cancel.")
    return FWD_SEND


async def forward_send(update: Update, context: CallbackContext) -> int:
    pf: PendingForward = context.user_data.get("pending_forward")
    if not pf:
        await update.message.reply_text("Nothing to send. Stage files, then /forward.")
        return ConversationHandler.END

    store = _get_store(context)
    creds = store.get_gmail_oauth_credentials(telegram_user_id=update.effective_user.id)
    if not creds:
        await update.message.reply_text("Not logged in. Use /login, then /send again.")
        return FWD_SEND

    gmail = _get_gmail(context)
    if not gmail:
        await update.message.reply_text(_dev_error_text("Gmail API not configured."))
        return ConversationHandler.END

    await update.message.chat.send_action(action=ChatAction.TYPING)
    try:
        gmail.verify_refresh_token(refresh_token=creds.refresh_token)
    except RefreshError as e:
        logger.warning("Refresh token invalid: %s", e)
        await update.message.reply_text("Session expired. Use /login, then /send again.")
        return FWD_SEND
    except Exception as e:
        logger.exception("Token check failed")
        await update.message.reply_text(
            _dev_error_text(f"Login check failed: {type(e).__name__}: {e}\nTry /login then /send again.")
        )
        return FWD_SEND

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
        err = f"{type(e).__name__}: {e}"
        if "401" in err or "invalid_grant" in err.lower():
            await update.message.reply_text("Gmail rejected the session. Use /login, then /send again.")
            return FWD_SEND
        await update.message.reply_text(_dev_error_text(f"Send failed: {err}"))
        return ConversationHandler.END
    finally:
        context.user_data.pop("pending_forward", None)

    store.touch_recipient(telegram_user_id=update.effective_user.id, email=pf.to_address)
    await update.message.reply_text(f"Sent. Gmail id: {message_id or '(unknown)'}")
    return ConversationHandler.END


def build_app() -> Application:
    logging.basicConfig(level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))

    store = CredentialStore(db_path=config.DATABASE_PATH, encryption_key=config.ENCRYPTION_KEY)

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.bot_data["store"] = store

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
            raise RuntimeError("client_secret.json missing client_id/client_secret.")
        gmail = GmailApiClient(client_id=client_id, client_secret=client_secret, token_uri=token_uri)
    except FileNotFoundError:
        oauth_setup_error = f"Missing OAuth client file: `{config.GOOGLE_CLIENT_SECRETS_FILE}`."
    except Exception as e:
        oauth_setup_error = f"OAuth setup error: {type(e).__name__}: {e}"

    app.bot_data["gmail"] = gmail
    app.bot_data["oauth_setup_error"] = oauth_setup_error

    async def on_oauth_success(telegram_user_id: int, refresh_token: str) -> None:
        try:
            if not gmail:
                await app.bot.send_message(
                    chat_id=telegram_user_id,
                    text=_dev_error_text("Login ok but Gmail API not configured."),
                )
                return
            store.set_gmail_oauth_credentials(
                telegram_user_id=telegram_user_id,
                gmail_address="me",
                refresh_token=refresh_token,
            )
            await app.bot.send_message(chat_id=telegram_user_id, text="Login OK. Stage files, then /forward.")
        except Exception as e:
            logger.exception("OAuth success handler failed")
            await app.bot.send_message(
                chat_id=telegram_user_id,
                text=_dev_error_text(f"Save failed: {type(e).__name__}: {e}"),
            )

    async def on_oauth_error(telegram_user_id: int, message: str) -> None:
        await app.bot.send_message(
            chat_id=telegram_user_id,
            text=_dev_error_text(f"Login failed: {message}"),
        )

    oauth = OAuthServer(
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
            FWD_SEND: [
                CommandHandler("send", forward_send),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="forward",
        persistent=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("login", login_start))
    app.add_handler(CommandHandler("clear", clear_stash))
    app.add_handler(forward_conv)
    app.add_handler(MessageHandler((filters.Document.ALL | filters.PHOTO) & filters.ChatType.PRIVATE, stage_file))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_error_handler(on_error)

    return app


def main() -> None:
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
