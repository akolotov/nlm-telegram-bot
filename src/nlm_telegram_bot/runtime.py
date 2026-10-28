from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from nlm_telegram_bot.notebook import (
    NotebookAuthenticationExpiredError,
    SummaryProgress,
    SummaryType,
    summarize_youtube,
)


LOGGER = logging.getLogger(__name__)
YOUTUBE_URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
SUPPORTED_YOUTUBE_HOSTS = {"youtube.com", "youtu.be", "youtube-nocookie.com"}
SUMMARY_CALLBACK_PREFIX = "summary:"
ACTIVE_REQUESTS_KEY = "active_summary_requests"
ALLOWED_USER_IDS_KEY = "allowed_user_ids"
CHOOSE_SUMMARY_TEXT = "Choose the summary type:"
ADDING_VIDEO_STATUS = "Adding the video…"
PROGRESS_STATUS_TEXT = {
    SummaryProgress.GENERATING_SUMMARY: "Generating the summary…",
    SummaryProgress.SUMMARY_RECEIVED: "Summary received…",
    SummaryProgress.CLEANING_UP: "Cleaning up temporary data…",
}
FAILED_TEXT = "Could not summarize this video. Please try again later."
AUTH_EXPIRED_TEXT = (
    "NotebookLM authentication has expired. The bot needs to be authenticated again."
)
REQUEST_UNAVAILABLE_TEXT = "This summary request is no longer available."
INVALID_SELECTION_TEXT = "This summary type is not supported."

SUMMARY_KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(
                "📚 Получить конспект",
                callback_data=f"{SUMMARY_CALLBACK_PREFIX}{SummaryType.KNOWLEDGE}",
            )
        ],
        [
            InlineKeyboardButton(
                "💡 Разобрать доклад",
                callback_data=f"{SUMMARY_CALLBACK_PREFIX}{SummaryType.ACTIONABLE}",
            )
        ],
    ]
)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_user_ids: frozenset[int]
    public_base_url: str
    webhook_path: str
    webhook_secret_token: str
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        def required(name: str) -> str:
            value = os.environ.get(name, "").strip()
            if not value:
                raise RuntimeError(f"{name} is required")
            return value

        bot_token = required("TELEGRAM_BOT_TOKEN")
        allowed_user_ids_value = required("ALLOWED_USER_IDS")
        try:
            allowed_user_ids = frozenset(
                int(item.strip())
                for item in allowed_user_ids_value.split(",")
                if item.strip()
            )
        except ValueError as error:
            raise RuntimeError(
                "ALLOWED_USER_IDS must contain comma-separated integer IDs"
            ) from error
        if not allowed_user_ids:
            raise RuntimeError("ALLOWED_USER_IDS must contain at least one user ID")

        return cls(
            bot_token=bot_token,
            allowed_user_ids=allowed_user_ids,
            public_base_url=required("WEBHOOK_PUBLIC_BASE_URL").rstrip("/"),
            webhook_path="/" + required("WEBHOOK_PATH").strip("/"),
            webhook_secret_token=required("WEBHOOK_SECRET_TOKEN"),
            listen_host=os.environ.get("WEBHOOK_LISTEN_HOST", "0.0.0.0"),
            listen_port=int(os.environ.get("WEBHOOK_LISTEN_PORT", "8080")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.effective_message is not None:
        await update.effective_message.reply_text(
            "Send me a YouTube link, then choose the summary type."
        )


def extract_youtube_url(text: str) -> str | None:
    """Return the first supported YouTube URL in a message."""
    for match in YOUTUBE_URL_RE.finditer(text):
        candidate = match.group().rstrip(".,!?;:]}\"'")
        hostname = (urlparse(candidate).hostname or "").lower().rstrip(".")
        if any(
            hostname == host or hostname.endswith(f".{host}")
            for host in SUPPORTED_YOUTUBE_HOSTS
        ):
            return candidate
    return None


async def _send_rich_reply(message, bot, markdown: str) -> None:
    data = {
        "chat_id": message.chat_id,
        "rich_message": {"markdown": markdown},
        "reply_parameters": {"message_id": message.message_id},
    }
    if message.message_thread_id is not None:
        data["message_thread_id"] = message.message_thread_id
    await bot._post("sendRichMessage", data=data)


async def _replace_with_rich_summary(status_message, bot, markdown: str) -> None:
    await bot._post(
        "editMessageText",
        data={
            "chat_id": status_message.chat_id,
            "message_id": status_message.message_id,
            "rich_message": {"markdown": markdown},
        },
    )


async def _update_progress_status(
    status_message,
    stage: SummaryProgress,
    operation_id: str,
) -> None:
    try:
        await status_message.edit_text(
            f"<i>{html.escape(PROGRESS_STATUS_TEXT[stage])}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=None,
        )
        LOGGER.info("[%s] Progress status updated to %s.", operation_id, stage.value)
    except Exception as error:
        LOGGER.warning(
            "[%s] Could not update progress status to %s (%s).",
            operation_id,
            stage.value,
            error.__class__.__name__,
        )


async def _process_summary(
    message,
    status_message,
    context: ContextTypes.DEFAULT_TYPE,
    video_url: str,
    summary_type: SummaryType,
) -> None:
    operation_id = uuid.uuid4().hex[:8]
    LOGGER.info(
        "[%s] Processing a YouTube summary request (%s).",
        operation_id,
        summary_type.value,
    )

    loop = asyncio.get_running_loop()

    def report_progress(stage: SummaryProgress) -> None:
        if status_message is None:
            return
        future = asyncio.run_coroutine_threadsafe(
            _update_progress_status(status_message, stage, operation_id),
            loop,
        )
        future.result()

    try:
        summary = await asyncio.to_thread(
            summarize_youtube,
            video_url,
            operation_id,
            summary_type=summary_type,
            progress_callback=report_progress,
        )
    except Exception as error:
        if isinstance(error, NotebookAuthenticationExpiredError):
            failure_text = AUTH_EXPIRED_TEXT
            LOGGER.warning(
                "[%s] Summary stopped because NotebookLM authentication expired.",
                operation_id,
            )
        else:
            failure_text = FAILED_TEXT
            LOGGER.error(
                "[%s] YouTube summary processing failed (%s).",
                operation_id,
                error.__class__.__name__,
            )
        if status_message is not None:
            try:
                await status_message.edit_text(
                    f"<i>{html.escape(failure_text)}</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=None,
                )
                LOGGER.info("[%s] Failure status delivered.", operation_id)
                return
            except Exception as status_error:
                LOGGER.warning(
                    "[%s] Could not update the failure status (%s).",
                    operation_id,
                    status_error.__class__.__name__,
                )
        try:
            await message.reply_text(
                f"<i>{html.escape(failure_text)}</i>",
                parse_mode=ParseMode.HTML,
                reply_to_message_id=message.message_id,
            )
            LOGGER.info("[%s] Failure reply delivered.", operation_id)
        except Exception as reply_error:
            LOGGER.warning(
                "[%s] Could not deliver the failure reply (%s).",
                operation_id,
                reply_error.__class__.__name__,
            )
        return

    if status_message is not None:
        try:
            LOGGER.info("[%s] Delivering the Rich Message summary.", operation_id)
            await _replace_with_rich_summary(status_message, context.bot, summary)
            LOGGER.info("[%s] Rich Message summary delivered.", operation_id)
            return
        except Exception as error:
            LOGGER.warning(
                "[%s] Could not replace the status with rich text (%s); "
                "trying a new reply.",
                operation_id,
                error.__class__.__name__,
            )

    LOGGER.info("[%s] Sending the Rich Message as a new reply.", operation_id)
    await _send_rich_reply(message, context.bot, summary)
    LOGGER.info("[%s] Rich Message summary delivered.", operation_id)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    message = update.effective_message
    if message is None or not message.text:
        return

    video_url = extract_youtube_url(message.text)
    if video_url is None:
        return

    try:
        await message.reply_text(
            CHOOSE_SUMMARY_TEXT,
            reply_markup=SUMMARY_KEYBOARD,
            reply_to_message_id=message.message_id,
        )
        LOGGER.info("Accepted a YouTube link and requested a summary type.")
    except Exception as error:
        LOGGER.warning(
            "Could not create the summary type selector (%s).",
            error.__class__.__name__,
        )


async def handle_summary_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    callback_query = update.callback_query
    if callback_query is None:
        return

    allowed_user_ids = context.bot_data.get(ALLOWED_USER_IDS_KEY, frozenset())
    user = update.effective_user
    if user is None or user.id not in allowed_user_ids:
        await callback_query.answer(
            "You are not allowed to use this bot.",
            show_alert=True,
        )
        return

    await callback_query.answer()
    callback_message = callback_query.message
    if callback_message is None:
        return

    callback_data = callback_query.data or ""
    try:
        summary_type = SummaryType(callback_data.removeprefix(SUMMARY_CALLBACK_PREFIX))
    except ValueError:
        await callback_message.edit_text(INVALID_SELECTION_TEXT, reply_markup=None)
        return

    original_message = callback_message.reply_to_message
    video_url = (
        extract_youtube_url(original_message.text)
        if original_message is not None and original_message.text
        else None
    )
    if video_url is None:
        await callback_message.edit_text(REQUEST_UNAVAILABLE_TEXT, reply_markup=None)
        return

    request_key = (callback_message.chat_id, callback_message.message_id)
    active_requests = context.bot_data.setdefault(ACTIVE_REQUESTS_KEY, set())
    if request_key in active_requests:
        return
    active_requests.add(request_key)

    try:
        try:
            await callback_message.edit_text(
                f"<i>{ADDING_VIDEO_STATUS}</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
        except Exception as error:
            LOGGER.warning(
                "Could not replace the summary selector with a status (%s).",
                error.__class__.__name__,
            )
        await _process_summary(
            original_message,
            callback_message,
            context,
            video_url,
            summary_type,
        )
    finally:
        active_requests.discard(request_key)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(Path(".env"))
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # HTTP client URLs can contain the Telegram bot token. Keep all request-level
    # logging in external network libraries disabled and emit sanitized stage
    # messages from this application instead.
    for logger_name in ("httpx", "httpcore", "notebooklm_tools"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    LOGGER.info("Starting the Telegram webhook bot; configuration is loaded.")
    application = (
        ApplicationBuilder().token(settings.bot_token).concurrent_updates(True).build()
    )
    application.bot_data[ALLOWED_USER_IDS_KEY] = settings.allowed_user_ids
    allowed_users = filters.User(user_id=settings.allowed_user_ids)
    application.add_handler(CommandHandler("start", start, filters=allowed_users))
    application.add_handler(
        CallbackQueryHandler(handle_summary_choice, pattern=r"^summary:")
    )
    application.add_handler(
        MessageHandler(allowed_users & filters.TEXT & ~filters.COMMAND, handle_text)
    )
    application.run_webhook(
        listen=settings.listen_host,
        port=settings.listen_port,
        url_path=settings.webhook_path.lstrip("/"),
        webhook_url=f"{settings.public_base_url}{settings.webhook_path}",
        secret_token=settings.webhook_secret_token,
    )


if __name__ == "__main__":
    main()
