"""Reciprocal Rank Fusion — combine multiple best-first ranked lists.

Used by memory/recall.py and tools/knowledge/tool.py to fuse keyword and
vector rankings without the arbitrary fixed-weight linear combination
(``keyword + vector * MAGIC_CONSTANT``) those two mixed different scales.
RRF only cares about rank position within each list, so no cross-signal
weight needs to be tuned.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence

DEFAULT_RRF_K = 60


def reciprocal_rank_fusion[T](
    *ranked_lists: Sequence[T],
    key: Callable[[T], Hashable],
    k: int = DEFAULT_RRF_K,
) -> list[T]:
    """Fuse best-first ranked lists into one order via RRF.

    Each item's fused score is the sum of ``1 / (k + rank)`` across every
    list it appears in (1-indexed rank); items absent from a list simply
    contribute 0 for that list. Returns items sorted by fused score,
    best first, deduplicated by ``key`` (first-seen instance is kept).
    """

    scores: dict[Hashable, float] = {}
    representative: dict[Hashable, T] = {}
    for ranked in ranked_lists:
        for index, item in enumerate(ranked):
            item_key = key(item)
            scores[item_key] = scores.get(item_key, 0.0) + 1.0 / (k + index + 1)
            representative.setdefault(item_key, item)

    ordered_keys = sorted(scores, key=lambda item_key: scores[item_key], reverse=True)
    return [representative[item_key] for item_key in ordered_keys]
