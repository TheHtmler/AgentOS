"""Background auto-title generation after the first successful run."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from uuid import UUID

import httpx

from agent_api.config import get_settings
from agent_api.db.chat_store import try_set_thread_title_if_empty
from agent_api.db.session import session_factory

logger = logging.getLogger(__name__)

# Prevent duplicate in-flight title jobs for the same thread.
_inflight: set[UUID] = set()

MAX_TITLE_CHARS = 80
_USER_SNIPPET = 500
_ASSISTANT_SNIPPET = 500

GenerateTitleFn = Callable[[str, str, httpx.AsyncClient], Awaitable[str | None]]


async def generate_title_via_ollama(
    user_message: str,
    assistant_content: str,
    http_client: httpx.AsyncClient,
) -> str | None:
    """Ask the chat model for a short title; returns None on failure."""

    settings = get_settings()
    user_snip = user_message.strip()[:_USER_SNIPPET]
    asst_snip = assistant_content.strip()[:_ASSISTANT_SNIPPET]
    if not user_snip:
        return None

    prompt = (
        "Generate a short conversation title that captures the topic.\n"
        "Rules: reply with ONLY the title text; no quotes; no explanation; "
        f"at most {MAX_TITLE_CHARS} characters; prefer the user's language.\n\n"
        f"User:\n{user_snip}\n\n"
        f"Assistant:\n{asst_snip}"
    )

    url = settings.ollama_base_url.rstrip("/") + "/chat/completions"
    try:
        response = await http_client.post(
            url,
            json={
                "model": settings.ollama_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You name chat threads. Output only a short title.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 64,
                "temperature": 0.2,
            },
            timeout=settings.auto_thread_title_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        logger.exception("auto_thread_title model request failed")
        return None

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("auto_thread_title unexpected response shape")
        return None

    if not isinstance(content, str):
        return None
    return normalize_title(content)


def normalize_title(raw: str) -> str | None:
    """Strip model chatter into a single-line title or None if unusable."""

    text = raw.strip()
    if not text:
        return None
    # Take the first line only; models sometimes add a second explanatory line.
    text = text.splitlines()[0].strip()
    text = text.strip("\"'“”‘’「」《》")
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    if len(text) > MAX_TITLE_CHARS:
        text = text[:MAX_TITLE_CHARS].rstrip()
    return text or None


def schedule_auto_thread_title(
    *,
    thread_id: UUID,
    user_message: str,
    assistant_content: str,
    model_semaphore: asyncio.Semaphore,
    http_client: httpx.AsyncClient,
    generate_title: GenerateTitleFn | None = None,
) -> None:
    """Fire-and-forget title generation; never blocks the chat SSE/AG-UI response."""

    settings = get_settings()
    if not settings.auto_thread_title_enabled:
        return

    # Skip empty exchanges; need at least a user turn worth of text.
    if not user_message.strip() or not assistant_content.strip():
        return

    if thread_id in _inflight:
        return

    _inflight.add(thread_id)
    generator = generate_title or generate_title_via_ollama

    async def _run() -> None:
        try:
            # Bail out early if the user already renamed (or a prior job won).
            async with session_factory() as session:
                from sqlalchemy import select

                from agent_api.db.models import Thread

                existing = await session.scalar(
                    select(Thread.title).where(Thread.id == thread_id),
                )
                if existing is not None:
                    return

            # Share the model concurrency budget so titles do not starve live chats.
            try:
                await asyncio.wait_for(
                    model_semaphore.acquire(),
                    timeout=settings.auto_thread_title_timeout_seconds,
                )
            except TimeoutError:
                logger.info("auto_thread_title skipped; model semaphore busy thread=%s", thread_id)
                return

            try:
                title = await generator(user_message, assistant_content, http_client)
            finally:
                model_semaphore.release()

            if not title:
                return

            async with session_factory() as session, session.begin():
                # Conditional write: manual rename during generation must win.
                updated = await try_set_thread_title_if_empty(
                    session,
                    thread_id=thread_id,
                    title=title,
                )
            if updated:
                logger.info("auto_thread_title set thread=%s title=%r", thread_id, title)
        except Exception:
            logger.exception("auto_thread_title failed thread=%s", thread_id)
        finally:
            _inflight.discard(thread_id)

    asyncio.create_task(_run(), name=f"auto-thread-title-{thread_id}")
