"""One validity contract for ingestion, retrieval, and Ops health counts."""

import math
from typing import cast

from agent_api.config import get_settings

EMBEDDING_VERSION = "context-v2"


def valid_vector(value: object, dimensions: int | None = None) -> bool:
    if not isinstance(value, list) or not value:
        return False
    values = cast(list[object], value)
    if dimensions is not None and len(values) != dimensions:
        return False
    return all(
        isinstance(x, (float, int)) and not isinstance(x, bool) and math.isfinite(x) for x in values
    ) and any(x != 0 for x in values)


def usable_vector(value: object, model: str | None, version: str | None) -> bool:
    cfg = get_settings()
    return (
        model == cfg.resolved_background_embedding_model
        and version == EMBEDDING_VERSION
        and valid_vector(value, cfg.knowledge_embedding_dimensions)
    )


def retrieval_text(document_title: str, section: str | None, title: str, content: str) -> str:
    return "\n".join(
        dict.fromkeys(
            x.strip() for x in (document_title, section or "", title, content) if x.strip()
        )
    )
