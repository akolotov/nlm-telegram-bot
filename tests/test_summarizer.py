from unittest.mock import MagicMock, call, patch

import httpx
import pytest
from google import genai
from google.genai import errors, types

from nlm_telegram_bot.summarizer import (
    DEFAULT_MODEL,
    SUMMARY_QUERIES,
    SummaryProgress,
    SummaryRateLimitError,
    SummaryType,
    summarize_youtube,
)


def response(text="# Summary", finish_reason="STOP", **kwargs):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(
            content=types.Content(parts=[types.Part(text=text)]),
            finish_reason=finish_reason,
        )],
        **kwargs,
    )


@pytest.fixture
def client_factory():
    with patch("nlm_telegram_bot.summarizer.genai.Client") as factory:
        factory.return_value.__enter__.return_value.models.generate_content.return_value = response()
        yield factory


@pytest.mark.parametrize("summary_type", list(SummaryType))
def test_summary_request_and_progress(client_factory, summary_type, caplog):
    client = client_factory.return_value.__enter__.return_value
    client.models.generate_content.return_value = response(
        "  # Summary\n\nUseful text  ",
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100, candidates_token_count=20,
            thoughts_token_count=5, total_token_count=125,
        ),
    )
    callback = MagicMock()
    with caplog.at_level("INFO"):
        result = summarize_youtube(
            "https://youtu.be/abc123", "operation", api_key="private-key",
            model="custom-model", summary_type=summary_type, progress_callback=callback,
        )
    assert result == "# Summary\n\nUseful text"
    options = client_factory.call_args.kwargs
    assert options["api_key"] == "private-key"
    assert options["vertexai"] is False
    assert options["http_options"].timeout == 600_000
    assert options["http_options"].retry_options.attempts == 1
    request = client.models.generate_content.call_args.kwargs
    assert request["model"] == "custom-model"
    assert request["contents"][0].file_data.file_uri == "https://youtu.be/abc123"
    assert request["contents"][0].file_data.mime_type == "video/mp4"
    assert request["contents"][1] == SUMMARY_QUERIES[summary_type]
    assert "Не используй Markdown-таблицы" in request["contents"][1]
    assert request["config"].max_output_tokens == 8192
    client.models.generate_content.assert_called_once()
    client_factory.return_value.__exit__.assert_called_once()
    assert callback.call_args_list == [
        call(SummaryProgress.GENERATING_SUMMARY), call(SummaryProgress.SUMMARY_RECEIVED),
    ]
    assert "input_tokens=100" in caplog.text
    assert "elapsed_seconds=" in caplog.text
    assert "private-key" not in caplog.text
    assert "Useful text" not in caplog.text


@pytest.mark.parametrize("invalid_response", [
    response("  "), response("partial", "MAX_TOKENS"),
    response("unsafe", "SAFETY"), response("partial", None),
    types.GenerateContentResponse(),
    types.GenerateContentResponse(prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason="SAFETY")),
])
def test_invalid_summary_is_rejected(client_factory, invalid_response):
    client_factory.return_value.__enter__.return_value.models.generate_content.return_value = invalid_response
    callback = MagicMock()
    with pytest.raises(RuntimeError):
        summarize_youtube("https://youtu.be/abc123", api_key="key", progress_callback=callback)
    assert callback.call_args_list == [call(SummaryProgress.GENERATING_SUMMARY)]
    client_factory.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("error, expected", [
    (errors.ClientError(429, {"error": {"message": "private provider details"}}), SummaryRateLimitError),
    (errors.ClientError(403, {"error": {"message": "private provider details"}}), errors.ClientError),
    (errors.ServerError(503, {"error": {"message": "private provider details"}}), errors.ServerError),
    (httpx.ReadTimeout("private provider details"), httpx.ReadTimeout),
])
def test_request_failure_closes_client_without_retry(client_factory, error, expected, caplog):
    client = client_factory.return_value.__enter__.return_value
    client.models.generate_content.side_effect = error
    with pytest.raises(expected):
        summarize_youtube("https://youtu.be/abc123", api_key="key")
    client.models.generate_content.assert_called_once()
    client_factory.return_value.__exit__.assert_called_once()
    assert "private provider details" not in caplog.text


def test_progress_callback_failure_does_not_stop_generation(client_factory):
    assert summarize_youtube(
        "https://youtu.be/abc123", api_key="key",
        progress_callback=MagicMock(side_effect=RuntimeError("private details")),
    ) == "# Summary"
    assert client_factory.return_value.__enter__.return_value.models.generate_content.call_args.kwargs["model"] == DEFAULT_MODEL


@pytest.mark.parametrize("status, expected", [(429, SummaryRateLimitError), (503, errors.ServerError)])
def test_sdk_sends_only_one_http_request_on_provider_error(status, expected):
    requests = []

    def reject(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"code": status, "message": "Provider unavailable"}})

    http_client = httpx.Client(transport=httpx.MockTransport(reject))
    real_client = genai.Client(
        api_key="test-key", vertexai=False,
        http_options=types.HttpOptions(
            httpx_client=http_client, retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    with patch("nlm_telegram_bot.summarizer.genai.Client", return_value=real_client):
        with pytest.raises(expected):
            summarize_youtube("https://youtu.be/abc123", api_key="test-key")
    assert len(requests) == 1
    # The SDK does not own an HTTP client supplied by its caller.
    http_client.close()
