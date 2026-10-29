from __future__ import annotations

import logging
import time
from collections.abc import Callable
from enum import StrEnum

from google import genai
from google.genai import errors, types


DEFAULT_MODEL = "gemini-3.8-flash"
SUMMARY_QUERY = (
    "Сделай качественное summary стрима: какие темы поднимались, какие практические "
    "примеры рассматривались, что было гипотезами, а что реально подтвердилось на "
    "практике. Оформи ответ в Markdown с понятными заголовками и списками. Не "
    "используй Markdown-таблицы: вместо них представляй данные в виде маркированных "
    "или нумерованных списков."
)
ACTIONABLE_SUMMARY_QUERY = (
    "Сделай качественное summary доклада: какие темы поднимались, какие практические "
    "примеры рассматривались, какие можно извлечь actionable items. Оформи ответ в "
    "Markdown с понятными заголовками и списками. Не используй Markdown-таблицы: "
    "вместо них представляй данные в виде маркированных или нумерованных списков."
)

LOGGER = logging.getLogger(__name__)


class SummaryRateLimitError(RuntimeError):
    """Gemini rejected the request because a rate limit was reached."""


class SummaryType(StrEnum):
    """Supported summary prompts."""

    KNOWLEDGE = "knowledge"
    ACTIONABLE = "actionable"


SUMMARY_QUERIES = {
    SummaryType.KNOWLEDGE: SUMMARY_QUERY,
    SummaryType.ACTIONABLE: ACTIONABLE_SUMMARY_QUERY,
}


class SummaryProgress(StrEnum):
    """Observable stages of a YouTube summary request."""

    GENERATING_SUMMARY = "generating_summary"
    SUMMARY_RECEIVED = "summary_received"


ProgressCallback = Callable[[SummaryProgress], None]


def _report_progress(
    callback: ProgressCallback | None,
    stage: SummaryProgress,
    operation_id: str,
) -> None:
    if callback is None:
        return
    try:
        callback(stage)
    except Exception as error:
        LOGGER.warning(
            "[%s] Could not report progress %s (%s).",
            operation_id,
            stage.value,
            error.__class__.__name__,
        )


def summarize_youtube(
    video_url: str,
    operation_id: str = "standalone",
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    summary_type: SummaryType = SummaryType.KNOWLEDGE,
    progress_callback: ProgressCallback | None = None,
) -> str:
    """Generate a complete Markdown summary with one Gemini API request."""
    prompt = SUMMARY_QUERIES[summary_type]
    started = time.monotonic()
    _report_progress(progress_callback, SummaryProgress.GENERATING_SUMMARY, operation_id)
    LOGGER.info("[%s] Requesting summary (model=%s).", operation_id, model)
    try:
        with genai.Client(
            api_key=api_key,
            vertexai=False,
            http_options=types.HttpOptions(
                api_version="v1beta",
                timeout=600_000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ) as client:
            response = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_uri(file_uri=video_url, mime_type="video/mp4"),
                    prompt,
                ],
                config=types.GenerateContentConfig(max_output_tokens=8192),
            )
    except errors.APIError as error:
        if error.code == 429:
            raise SummaryRateLimitError("Gemini rate limit reached.") from None
        raise
    finally:
        LOGGER.info(
            "[%s] Gemini request finished (model=%s, elapsed_seconds=%.2f).",
            operation_id,
            model,
            time.monotonic() - started,
        )

    usage = response.usage_metadata
    if usage is not None:
        LOGGER.info(
            "[%s] Gemini usage: input_tokens=%s output_tokens=%s "
            "thought_tokens=%s total_tokens=%s.",
            operation_id,
            usage.prompt_token_count,
            usage.candidates_token_count,
            usage.thoughts_token_count,
            usage.total_token_count,
        )
    if response.prompt_feedback and response.prompt_feedback.block_reason:
        raise RuntimeError("Gemini blocked the summary request.")
    if (
        not response.candidates
        or response.candidates[0].finish_reason != types.FinishReason.STOP
    ):
        raise RuntimeError("Gemini did not return a complete summary.")
    answer = (response.text or "").strip()
    if not answer:
        raise RuntimeError("Gemini returned an empty summary.")
    _report_progress(progress_callback, SummaryProgress.SUMMARY_RECEIVED, operation_id)
    return answer
