from __future__ import annotations

import logging
import os
from pathlib import Path


NOTEBOOK_TITLE = "NLM-bot-placeholder"
SUMMARY_QUERY = (
    "Сделай качественное summary стрима: какие темы поднимались, какие практические "
    "примеры рассматривались, что было гипотезами, а что реально подтвердилось на "
    "практике. Оформи ответ в Markdown с понятными заголовками и списками."
)

LOGGER = logging.getLogger(__name__)


class NotebookAuthenticationExpiredError(RuntimeError):
    """NotebookLM rejected the saved authentication session."""


def _is_authentication_error(error: BaseException) -> bool:
    from notebooklm_tools.core.errors import ClientAuthenticationError

    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, ClientAuthenticationError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _make_client():
    # notebooklm-tools passes this value directly to Path(). Expand it before
    # importing the package's configuration modules.
    if storage_path := os.environ.get("NOTEBOOKLM_MCP_CLI_PATH"):
        os.environ["NOTEBOOKLM_MCP_CLI_PATH"] = str(Path(storage_path).expanduser())

    from notebooklm_tools.core.auth import get_auth_manager
    from notebooklm_tools.core.client import NotebookLMClient

    profile = get_auth_manager().load_profile()
    return NotebookLMClient(
        cookies=profile.cookies,
        csrf_token=profile.csrf_token or "",
        session_id=profile.session_id or "",
        build_label=profile.build_label or "",
        base_host=profile.base_host or "",
        profile_name=profile.name,
    )


def _find_notebook_id(client) -> str:
    from notebooklm_tools.services.notebooks import list_notebooks

    matches = [
        notebook
        for notebook in list_notebooks(client, max_results=10_000)["notebooks"]
        if notebook["title"] == NOTEBOOK_TITLE
    ]
    if not matches:
        raise RuntimeError(f'Notebook "{NOTEBOOK_TITLE}" not found.')
    if len(matches) > 1:
        raise RuntimeError(f'Multiple notebooks named "{NOTEBOOK_TITLE}" found.')
    return matches[0]["id"]


def _summarize_youtube(video_url: str, operation_id: str) -> str:
    """Temporarily add a YouTube source and return its Markdown summary."""
    from notebooklm_tools.services.chat import query
    from notebooklm_tools.services.sources import add_source, delete_source

    source_id: str | None = None
    conversation_id: str | None = None

    LOGGER.info("[%s] Loading the NotebookLM authentication profile.", operation_id)
    with _make_client() as client:
        LOGGER.info("[%s] NotebookLM client is ready.", operation_id)
        LOGGER.info("[%s] Looking up the summary notebook.", operation_id)
        notebook_id = _find_notebook_id(client)
        LOGGER.info("[%s] Summary notebook found.", operation_id)
        try:
            LOGGER.info(
                "[%s] Adding the YouTube video as a temporary source.", operation_id
            )
            source = add_source(
                client,
                notebook_id,
                "url",
                url=video_url,
                wait=True,
                wait_timeout=180.0,
            )
            source_id = source["source_id"]
            LOGGER.info(
                "[%s] Temporary source is ready; requesting the summary.",
                operation_id,
            )
            result = query(
                client,
                notebook_id,
                SUMMARY_QUERY,
                source_ids=[source_id],
                timeout=180.0,
                new_conversation=True,
            )
            conversation_id = result.get("conversation_id")
            answer = result["answer"].strip()
            if not answer:
                raise RuntimeError("NotebookLM returned an empty summary.")
            LOGGER.info(
                "[%s] Summary received from NotebookLM (%d characters).",
                operation_id,
                len(answer),
            )
            return answer
        finally:
            if conversation_id:
                try:
                    if not client.delete_chat_history(notebook_id, conversation_id):
                        LOGGER.warning(
                            "[%s] Conversation cleanup was not confirmed.", operation_id
                        )
                    else:
                        LOGGER.info("[%s] Conversation removed.", operation_id)
                except Exception as error:
                    LOGGER.warning(
                        "[%s] Conversation cleanup failed (%s).",
                        operation_id,
                        error.__class__.__name__,
                    )

            if source_id:
                try:
                    delete_source(client, source_id)
                    LOGGER.info("[%s] Temporary source removed.", operation_id)
                except Exception as error:
                    LOGGER.warning(
                        "[%s] Temporary source cleanup failed (%s).",
                        operation_id,
                        error.__class__.__name__,
                    )


def summarize_youtube(video_url: str, operation_id: str = "standalone") -> str:
    """Return a summary while exposing authentication expiry explicitly."""
    try:
        return _summarize_youtube(video_url, operation_id)
    except Exception as error:
        if _is_authentication_error(error):
            LOGGER.warning(
                "[%s] NotebookLM authentication has expired.", operation_id
            )
            raise NotebookAuthenticationExpiredError from error
        raise
