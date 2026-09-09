from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from nlm_telegram_bot.notebook import (
    NotebookAuthenticationExpiredError,
    SummaryProgress,
)
from nlm_telegram_bot.runtime import extract_youtube_url, handle_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://youtu.be/abc123", "https://youtu.be/abc123"),
        (
            "Please summarize https://www.youtube.com/watch?v=abc123.",
            "https://www.youtube.com/watch?v=abc123",
        ),
        (
            "https://www.youtube-nocookie.com/embed/abc123",
            "https://www.youtube-nocookie.com/embed/abc123",
        ),
        ("https://youtube.com.evil.example/watch?v=abc123", None),
        ("https://example.com/video", None),
    ],
)
def test_extract_youtube_url(text: str, expected: str | None) -> None:
    assert extract_youtube_url(text) == expected


def make_request(text: str, *, status=None):
    if status is None:
        status = Mock(chat_id=123, message_id=789)
        status.edit_text = AsyncMock()
    message = Mock(
        text=text,
        chat_id=123,
        message_id=456,
        message_thread_id=None,
        reply_text=AsyncMock(return_value=status),
    )
    update = Mock(effective_message=message)
    bot = Mock(_post=AsyncMock())
    context = Mock(bot=bot)
    return update, context, message, status, bot


@patch(
    "nlm_telegram_bot.runtime.summarize_youtube",
    return_value="# Summary\n\n- First point",
)
def test_youtube_message_becomes_rich_reply(summarize_youtube) -> None:
    update, context, message, status, bot = make_request("https://youtu.be/abc123")

    asyncio.run(handle_text(update, context))

    summarize_youtube.assert_called_once()
    assert summarize_youtube.call_args.args[0] == "https://youtu.be/abc123"
    assert len(summarize_youtube.call_args.args[1]) == 8
    message.reply_text.assert_awaited_once_with(
        "<i>Adding the video…</i>",
        parse_mode="HTML",
        reply_to_message_id=456,
    )
    bot._post.assert_awaited_once_with(
        "editMessageText",
        data={
            "chat_id": 123,
            "message_id": 789,
            "rich_message": {"markdown": "# Summary\n\n- First point"},
        },
    )


def test_progress_statuses_are_updated_in_order() -> None:
    def summarize(_url, _operation_id, *, progress_callback):
        progress_callback(SummaryProgress.GENERATING_SUMMARY)
        progress_callback(SummaryProgress.SUMMARY_RECEIVED)
        progress_callback(SummaryProgress.CLEANING_UP)
        return "# Summary"

    update, context, _message, status, bot = make_request(
        "https://youtu.be/abc123"
    )

    with patch("nlm_telegram_bot.runtime.summarize_youtube", side_effect=summarize):
        asyncio.run(handle_text(update, context))

    assert status.edit_text.await_args_list == [
        call("<i>Generating the summary…</i>", parse_mode="HTML"),
        call("<i>Summary received…</i>", parse_mode="HTML"),
        call("<i>Cleaning up temporary data…</i>", parse_mode="HTML"),
    ]
    bot._post.assert_awaited_once()


def test_progress_update_failure_does_not_stop_summary() -> None:
    def summarize(_url, _operation_id, *, progress_callback):
        progress_callback(SummaryProgress.GENERATING_SUMMARY)
        return "# Summary"

    update, context, _message, status, bot = make_request(
        "https://youtu.be/abc123"
    )
    status.edit_text.side_effect = RuntimeError("Telegram unavailable")

    with patch("nlm_telegram_bot.runtime.summarize_youtube", side_effect=summarize):
        asyncio.run(handle_text(update, context))

    status.edit_text.assert_awaited_once_with(
        "<i>Generating the summary…</i>", parse_mode="HTML"
    )
    bot._post.assert_awaited_once()


@patch(
    "nlm_telegram_bot.runtime.summarize_youtube",
    return_value="# Summary",
)
def test_missing_status_still_sends_reply(summarize_youtube) -> None:
    update, context, message, _status, bot = make_request("https://youtu.be/abc123")
    message.reply_text.side_effect = RuntimeError("Telegram unavailable")

    asyncio.run(handle_text(update, context))

    summarize_youtube.assert_called_once()
    assert summarize_youtube.call_args.args[0] == "https://youtu.be/abc123"
    bot._post.assert_awaited_once_with(
        "sendRichMessage",
        data={
            "chat_id": 123,
            "rich_message": {"markdown": "# Summary"},
            "reply_parameters": {"message_id": 456},
        },
    )


@patch("nlm_telegram_bot.runtime.summarize_youtube")
def test_non_youtube_text_is_ignored(summarize_youtube) -> None:
    update, context, message, _status, bot = make_request("hello")

    asyncio.run(handle_text(update, context))

    summarize_youtube.assert_not_called()
    message.reply_text.assert_not_awaited()
    bot._post.assert_not_awaited()


@patch(
    "nlm_telegram_bot.runtime.summarize_youtube",
    side_effect=RuntimeError("private provider details"),
)
def test_processing_error_replaces_status_with_safe_message(_summarize_youtube) -> None:
    update, context, _message, status, bot = make_request("https://youtu.be/abc123")

    asyncio.run(handle_text(update, context))

    status.edit_text.assert_awaited_once_with(
        "<i>Could not summarize this video. Please try again later.</i>",
        parse_mode="HTML",
    )
    bot._post.assert_not_awaited()


@patch(
    "nlm_telegram_bot.runtime.summarize_youtube",
    side_effect=NotebookAuthenticationExpiredError(),
)
def test_expired_authentication_gets_specific_message(_summarize_youtube) -> None:
    update, context, _message, status, bot = make_request("https://youtu.be/abc123")

    asyncio.run(handle_text(update, context))

    status.edit_text.assert_awaited_once_with(
        "<i>NotebookLM authentication has expired. The bot needs to be "
        "authenticated again.</i>",
        parse_mode="HTML",
    )
    bot._post.assert_not_awaited()
