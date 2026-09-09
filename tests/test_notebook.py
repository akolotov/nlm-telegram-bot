from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
from notebooklm_tools.core.errors import ClientAuthenticationError
from notebooklm_tools.services.errors import ServiceError

from nlm_telegram_bot.notebook import (
    NotebookAuthenticationExpiredError,
    SUMMARY_QUERY,
    SummaryProgress,
    summarize_youtube,
)


def make_client() -> MagicMock:
    client = MagicMock()
    client.__enter__.return_value = client
    client.delete_chat_history.return_value = True
    return client


@patch("notebooklm_tools.services.sources.delete_source")
@patch("notebooklm_tools.services.sources.add_source")
@patch("notebooklm_tools.services.chat.query")
@patch("nlm_telegram_bot.notebook._find_notebook_id", return_value="notebook-id")
@patch("nlm_telegram_bot.notebook._make_client")
def test_summary_uses_only_temporary_source_and_cleans_up(
    make_client_mock,
    _find_notebook_id,
    query,
    add_source,
    delete_source,
) -> None:
    client = make_client()
    make_client_mock.return_value = client
    add_source.return_value = {"source_id": "source-id"}
    query.return_value = {
        "answer": "  # Summary\n\nUseful text  ",
        "conversation_id": "conversation-id",
    }
    progress_callback = MagicMock()

    result = summarize_youtube(
        "https://youtu.be/abc123", progress_callback=progress_callback
    )

    assert result == "# Summary\n\nUseful text"
    assert query.call_args.args[2] == SUMMARY_QUERY
    assert "Не используй Markdown-таблицы" in SUMMARY_QUERY
    assert query.call_args.kwargs["source_ids"] == ["source-id"]
    client.delete_chat_history.assert_called_once_with(
        "notebook-id", "conversation-id"
    )
    delete_source.assert_called_once_with(client, "source-id")
    assert progress_callback.call_args_list == [
        call(SummaryProgress.GENERATING_SUMMARY),
        call(SummaryProgress.SUMMARY_RECEIVED),
        call(SummaryProgress.CLEANING_UP),
    ]


@patch("notebooklm_tools.services.sources.delete_source")
@patch("notebooklm_tools.services.sources.add_source")
@patch("notebooklm_tools.services.chat.query", side_effect=RuntimeError("query failed"))
@patch("nlm_telegram_bot.notebook._find_notebook_id", return_value="notebook-id")
@patch("nlm_telegram_bot.notebook._make_client")
def test_query_failure_still_removes_temporary_source(
    make_client_mock,
    _find_notebook_id,
    _query,
    add_source,
    delete_source,
) -> None:
    client = make_client()
    make_client_mock.return_value = client
    add_source.return_value = {"source_id": "source-id"}
    progress_callback = MagicMock()

    with pytest.raises(RuntimeError, match="query failed"):
        summarize_youtube(
            "https://youtu.be/abc123", progress_callback=progress_callback
        )

    client.delete_chat_history.assert_not_called()
    delete_source.assert_called_once_with(client, "source-id")
    assert progress_callback.call_args_list == [
        call(SummaryProgress.GENERATING_SUMMARY),
        call(SummaryProgress.CLEANING_UP),
    ]


@patch("nlm_telegram_bot.notebook._summarize_youtube")
def test_wrapped_client_authentication_error_is_classified(_summarize) -> None:
    client_error = ClientAuthenticationError("Authentication expired")
    service_error = ServiceError("Failed to list notebooks")
    service_error.__cause__ = client_error
    _summarize.side_effect = service_error

    with pytest.raises(NotebookAuthenticationExpiredError):
        summarize_youtube("https://youtu.be/abc123", "operation")
