"""Embedding helpers for note-memory hybrid recall (OpenAI-compatible / Ollama)."""

from __future__ import annotations

import logging
import math
from typing import Any, cast

import httpx

from agent_api.config import Settings, get_settings

logger = logging.getLogger(__name__)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return cosine similarity in ``[-1, 1]``; 0 when either vector is empty."""

    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right, strict=True):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


async def embed_text(
    text: str,
    http_client: httpx.AsyncClient,
    *,
    settings: Settings | None = None,
    enabled: bool | None = None,
) -> list[float] | None:
    """Embed one string via the configured OpenAI-compatible embeddings endpoint.

    ``enabled`` overrides the default ``memory_embedding_enabled`` gate so knowledge
    search can use the same helper under ``knowledge_embedding_enabled``.
    """

    cfg = settings or get_settings()
    if enabled is None:
        enabled = cfg.memory_embedding_enabled
    if not enabled:
        return None
    model = cfg.memory_embedding_model.strip()
    if not model:
        return None
    normalized = text.strip()
    if not normalized:
        return None

    try:
        response = await http_client.post(
            cfg.ollama_base_url.rstrip("/") + "/embeddings",
            json={"model": model, "input": normalized[:4_000]},
            timeout=cfg.memory_extract_timeout_seconds,
        )
        response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            # Native Ollama shape: {"embedding": [...]}
            raw = payload.get("embedding")
            if isinstance(raw, list) and raw and isinstance(raw[0], (int, float)):
                return [float(cast(int | float, value)) for value in cast(list[object], raw)]
            logger.warning("embedding response missing data")
            return None
        first = cast(dict[str, object], data[0])
        embedding = first.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            return None
        return [
            float(cast(int | float, value)) for value in cast(list[object], embedding)
        ]
    except Exception:
        logger.exception("embedding request failed")
        return None
