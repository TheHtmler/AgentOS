"""Embedding helpers for memory/knowledge vectors via the background endpoint.

The endpoint is fixed by ``background_*`` settings (falling back to local Ollama)
and never follows an Agent's chat provider — see config.py.
"""

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
    model = cfg.resolved_background_embedding_model.strip()
    if not model:
        return None
    normalized = text.strip()
    if not normalized:
        return None

    try:
        response = await http_client.post(
            cfg.resolved_background_base_url + "/embeddings",
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
        return [float(cast(int | float, value)) for value in cast(list[object], embedding)]
    except Exception:
        logger.exception("embedding request failed")
        return None


_EMBED_BATCH_SIZE = 32


def _parse_batch_embeddings(payload: object, expected: int) -> list[list[float] | None] | None:
    """Map an OpenAI-shaped batch response back to input order; None when unusable."""

    if not isinstance(payload, dict):
        return None
    data = cast(dict[str, object], payload).get("data")
    if not isinstance(data, list) or len(cast(list[object], data)) != expected:
        return None
    ordered: list[list[float] | None] = [None] * expected
    for position, item in enumerate(cast(list[object], data)):
        if not isinstance(item, dict):
            return None
        entry = cast(dict[str, object], item)
        index = entry.get("index", position)
        if not isinstance(index, int) or not 0 <= index < expected:
            return None
        embedding = entry.get("embedding")
        if not isinstance(embedding, list) or not embedding:
            return None
        ordered[index] = [
            float(cast(int | float, value)) for value in cast(list[object], embedding)
        ]
    return ordered


async def embed_texts(
    texts: list[str],
    http_client: httpx.AsyncClient,
    *,
    settings: Settings | None = None,
    enabled: bool | None = None,
) -> list[list[float] | None]:
    """Embed many strings in batched calls; per-item failure degrades to None.

    One HTTP request per ``_EMBED_BATCH_SIZE`` inputs instead of one per input —
    knowledge imports embed dozens of chunks, and single calls kept the import
    transaction open for tens of seconds (the window behind the duplicate-key
    collisions). A failed batch falls back to per-item calls so one bad chunk
    does not sink the rest.
    """

    cfg = settings or get_settings()
    if enabled is None:
        enabled = cfg.knowledge_embedding_enabled
    model = cfg.resolved_background_embedding_model.strip()
    results: list[list[float] | None] = [None] * len(texts)
    if not enabled or not model:
        return results

    for start in range(0, len(texts), _EMBED_BATCH_SIZE):
        batch = [text.strip()[:4_000] for text in texts[start : start + _EMBED_BATCH_SIZE]]
        indexed = [(offset, text) for offset, text in enumerate(batch) if text]
        if not indexed:
            continue
        try:
            response = await http_client.post(
                cfg.resolved_background_base_url + "/embeddings",
                json={"model": model, "input": [text for _, text in indexed]},
                timeout=cfg.memory_extract_timeout_seconds,
            )
            response.raise_for_status()
            parsed = _parse_batch_embeddings(response.json(), len(indexed))
        except Exception:
            logger.exception("batch embedding request failed")
            parsed = None
        if parsed is None:
            # Fall back to per-item calls so a single bad chunk loses only itself.
            for offset, text in indexed:
                results[start + offset] = await embed_text(
                    text,
                    http_client,
                    settings=cfg,
                    enabled=True,
                )
        else:
            for position, embedding in enumerate(parsed):
                results[start + indexed[position][0]] = embedding
    return results
