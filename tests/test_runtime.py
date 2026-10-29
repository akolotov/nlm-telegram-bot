from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from telegram import Chat, InaccessibleMessage

from nlm_telegram_bot.summarizer import (
    SummaryRateLimitError,
    SummaryProgress,
    SummaryType,
)
from nlm_telegram_bot.runtime import (
    ACTIVE_REQUESTS_KEY,
    ALLOWED_USER_IDS_KEY,
    GEMINI_SETTINGS_KEY,
    CHOOSE_SUMMARY_TEXT,
    INVALID_SELECTION_TEXT,
    REQUEST_UNAVAILABLE_TEXT,
    SUMMARY_KEYBOARD,
    extract_youtube_url,
    handle_summary_choice,
    handle_text,
)


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


def make_text_request(text: str):
    message = Mock(
        text=text,
        chat_id=123,
        message_id=456,
        message_thread_id=None,
        reply_text=AsyncMock(),
    )
    update = Mock(effective_message=message)
    context = Mock()
    return update, context, message


def make_callback_request(
    summary_type: str = SummaryType.KNOWLEDGE,
    *,
    original_text: str | None = "https://youtu.be/abc123",
    bot_data: dict | None = None,
):
    original_message = None
    if original_text is not None:
        original_message = Mock(
            text=original_text,
            chat_id=123,
            message_id=456,
            message_thread_id=None,
            reply_text=AsyncMock(),
        )
    status_message = Mock(
        chat_id=123,
        message_id=789,
        is_accessible=True,
        reply_to_message=original_message,
        edit_text=AsyncMock(),
    )
    callback_query = Mock(
        data=f"summary:{summary_type}",
        message=status_message,
        answer=AsyncMock(),
    )
    update = Mock(
        callback_query=callback_query,
        effective_user=Mock(id=42),
    )
    bot = Mock(_post=AsyncMock())
    if bot_data is None:
        bot_data = {ALLOWED_USER_IDS_KEY: frozenset({42})}
    bot_data[GEMINI_SETTINGS_KEY] = Mock(
        gemini_api_key="test-key", gemini_model="custom-model"
    )
    context = Mock(
        bot=bot,
        bot_data=bot_data,
    )
    return update, context, original_message, status_message, callback_query, bot


@patch("nlm_telegram_bot.runtime.summarize_youtube")
def test_youtube_message_offers_two_summary_types(summarize_youtube) -> None:
    update, context, message = make_text_request("https://youtu.be/abc123")

    asyncio.run(handle_text(update, context))

    summarize_youtube.assert_not_called()
    message.reply_text.assert_awaited_once_with(
        CHOOSE_SUMMARY_TEXT,
        reply_markup=SUMMARY_KEYBOARD,
        reply_to_message_id=456,
    )
    buttons = [row[0] for row in SUMMARY_KEYBOARD.inline_keyboard]
    assert [button.text for button in buttons] == [
        "📚 Получить конспект",
        "💡 Разобрать доклад",
    ]
    assert [button.callback_data for button in buttons] == [
        "summary:knowledge",
        "summary:actionable",
    ]


@pytest.mark.parametrize("summary_type", list(SummaryType))
@patch(
    "nlm_telegram_bot.runtime.summarize_youtube",
    return_value="# Summary\n\n- First point",
)
def test_summary_choice_becomes_rich_reply(summarize_youtube, summary_type) -> None:
    update, context, _original, status, callback_query, bot = make_callback_request(
        summary_type
    )

    asyncio.run(handle_summary_choice(update, context))

    callback_query.answer.assert_awaited_once_with()
    assert status.edit_text.await_args_list[0] == call(
        "<i>Preparing the request…</i>",
        parse_mode="HTML",
        reply_markup=None,
    )
    summarize_youtube.assert_called_once()
    assert summarize_youtube.call_args.args[0] == "https://youtu.be/abc123"
    assert len(summarize_youtube.call_args.args[1]) == 8
    assert summarize_youtube.call_args.kwargs["summary_type"] == summary_type
    assert summarize_youtube.call_args.kwargs["api_key"] == "test-key"
    assert summarize_youtube.call_args.kwargs["model"] == "custom-model"
    bot._post.assert_awaited_once_with(
        "editMessageText",
        data={
            "chat_id": 123,
            "message_id": 789,
            "rich_message": {"markdown": "# Summary\n\n- First point"},
        },
    )


def test_progress_statuses_are_updated_in_order() -> None:
    def summarize(_url, _operation_id, *, api_key, model, summary_type, progress_callback):
        assert summary_type == SummaryType.KNOWLEDGE
        progress_callback(SummaryProgress.GENERATING_SUMMARY)
        progress_callback(SummaryProgress.SUMMARY_RECEIVED)
        return "# Summary"

    update, context, _original, status, _query, bot = make_callback_request()

    with patch("nlm_telegram_bot.runtime.summarize_youtube", side_effect=summarize):
        asyncio.run(handle_summary_choice(update, context))

    assert status.edit_text.await_args_list == [
        call("<i>Preparing the request…</i>", parse_mode="HTML", reply_markup=None),
        call(
            "<i>Generating the summary…</i>",
            parse_mode="HTML",
            reply_markup=None,
        ),
        call("<i>Summary received…</i>", parse_mode="HTML", reply_markup=None),
    ]
    bot._post.assert_awaited_once()


def test_progress_update_failure_does_not_stop_summary() -> None:
    def summarize(_url, _operation_id, *, api_key, model, summary_type, progress_callback):
        progress_callback(SummaryProgress.GENERATING_SUMMARY)
        return "# Summary"

    update, context, _original, status, _query, bot = make_callback_request()
    status.edit_text.side_effect = [None, RuntimeError("Telegram unavailable")]

    with patch("nlm_telegram_bot.runtime.summarize_youtube", side_effect=summarize):
        asyncio.run(handle_summary_choice(update, context))

    assert status.edit_text.await_count == 2
    bot._post.assert_awaited_once()


@patch(
    "nlm_telegram_bot.runtime.summarize_youtube",
    return_value="# Summary",
)
def test_failed_rich_edit_falls_back_to_new_reply(_summarize) -> None:
    update, context, original, _status, _query, bot = make_callback_request()
    bot._post.side_effect = [RuntimeError("Telegram unavailable"), None]

    asyncio.run(handle_summary_choice(update, context))

    assert bot._post.await_args_list == [
        call(
            "editMessageText",
            data={
                "chat_id": 123,
                "message_id": 789,
                "rich_message": {"markdown": "# Summary"},
            },
        ),
        call(
            "sendRichMessage",
            data={
                "chat_id": 123,
                "rich_message": {"markdown": "# Summary"},
                "reply_parameters": {"message_id": original.message_id},
            },
        ),
    ]


@patch(
    "nlm_telegram_bot.runtime.summarize_youtube",
    side_effect=RuntimeError("private provider details"),
)
def test_processing_error_replaces_status_with_safe_message(_summarize) -> None:
    update, context, _original, status, _query, bot = make_callback_request()

    asyncio.run(handle_summary_choice(update, context))

    assert status.edit_text.await_args_list[-1] == call(
        "<i>Could not summarize this video. Please try again later.</i>",
        parse_mode="HTML",
        reply_markup=None,
    )
    bot._post.assert_not_awaited()


@patch(
    "nlm_telegram_bot.runtime.summarize_youtube",
    side_effect=SummaryRateLimitError(),
)
def test_rate_limit_gets_specific_message(_summarize) -> None:
    update, context, _original, status, _query, bot = make_callback_request()

    asyncio.run(handle_summary_choice(update, context))

    assert status.edit_text.await_args_list[-1] == call(
        "<i>The summary service rate limit was reached. Please try again later.</i>",
        parse_mode="HTML",
        reply_markup=None,
    )
    bot._post.assert_not_awaited()


def test_unknown_summary_type_is_rejected() -> None:
    update, context, _original, status, callback_query, bot = make_callback_request(
        "unknown"
    )

    asyncio.run(handle_summary_choice(update, context))

    callback_query.answer.assert_awaited_once_with()
    status.edit_text.assert_awaited_once_with(
        INVALID_SELECTION_TEXT,
        reply_markup=None,
    )
    bot._post.assert_not_awaited()


def test_request_without_original_video_is_rejected() -> None:
    update, context, _original, status, _query, bot = make_callback_request(
        original_text=None
    )

    asyncio.run(handle_summary_choice(update, context))

    status.edit_text.assert_awaited_once_with(
        REQUEST_UNAVAILABLE_TEXT,
        reply_markup=None,
    )
    bot._post.assert_not_awaited()


@pytest.mark.parametrize(
    "callback_message",
    [None, InaccessibleMessage(chat=Chat(id=123, type="private"), message_id=789)],
)
@patch("nlm_telegram_bot.runtime.summarize_youtube")
def test_unavailable_callback_message_is_rejected(
    summarize_youtube, callback_message
) -> None:
    update, context, _original, status, callback_query, bot = make_callback_request()
    callback_query.message = callback_message

    asyncio.run(handle_summary_choice(update, context))

    callback_query.answer.assert_awaited_once_with(REQUEST_UNAVAILABLE_TEXT)
    status.edit_text.assert_not_awaited()
    summarize_youtube.assert_not_called()
    bot._post.assert_not_awaited()


def test_disallowed_user_cannot_use_callback() -> None:
    update, context, _original, status, callback_query, bot = make_callback_request(
        bot_data={ALLOWED_USER_IDS_KEY: frozenset({99})}
    )

    asyncio.run(handle_summary_choice(update, context))

    callback_query.answer.assert_awaited_once_with(
        "You are not allowed to use this bot.",
        show_alert=True,
    )
    status.edit_text.assert_not_awaited()
    bot._post.assert_not_awaited()


def test_repeated_callback_does_not_start_duplicate_processing() -> None:
    bot_data = {ALLOWED_USER_IDS_KEY: frozenset({42})}
    first = make_callback_request(bot_data=bot_data)
    second = make_callback_request(bot_data=bot_data)
    started = asyncio.Event()
    release = asyncio.Event()

    async def process(*_args):
        started.set()
        await release.wait()

    async def run_callbacks():
        with patch(
            "nlm_telegram_bot.runtime._process_summary",
            side_effect=process,
        ) as fn:
            first_task = asyncio.create_task(handle_summary_choice(first[0], first[1]))
            await started.wait()
            await handle_summary_choice(second[0], second[1])
            release.set()
            await first_task
            return fn

    process_summary = asyncio.run(run_callbacks())

    process_summary.assert_awaited_once()
    assert bot_data[ACTIVE_REQUESTS_KEY] == set()


@patch("nlm_telegram_bot.runtime.summarize_youtube")
def test_non_youtube_text_is_ignored(summarize_youtube) -> None:
    update, context, message = make_text_request("hello")

    asyncio.run(handle_text(update, context))

    summarize_youtube.assert_not_called()
    message.reply_text.assert_not_awaited()
