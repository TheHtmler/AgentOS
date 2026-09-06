"""Small, bounded OLS client for curator-selected disease and phenotype terms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import httpx

_OLS_SEARCH_URL = "https://www.ebi.ac.uk/ols4/api/search"
_SUPPORTED = {"mondo", "hp", "ordo"}


@dataclass(frozen=True)
class OntologyCandidate:
    curie: str
    label: str
    ontology: str


async def resolve_ontology_terms(
    query: str,
    ontology: str,
    client: httpx.AsyncClient,
) -> list[OntologyCandidate]:
    """Resolve an Ops query through OLS, returning only current exact-ish candidates."""

    if ontology not in _SUPPORTED:
        raise ValueError(f"unsupported ontology: {ontology}")
    response = await client.get(
        _OLS_SEARCH_URL,
        params={
            "q": query,
            "ontology": ontology,
            "queryFields": "label,synonym",
            "rows": "8",
        },
    )
    response.raise_for_status()
    payload = cast(dict[str, Any], response.json())
    raw_docs = cast(dict[str, Any], payload.get("response") or {}).get("docs")
    if not isinstance(raw_docs, list):
        return []
    candidates: list[OntologyCandidate] = []
    for value in cast(list[object], raw_docs):
        if not isinstance(value, dict):
            continue
        row = cast(dict[str, object], value)
        curie = row.get("obo_id")
        label = row.get("label")
        row_ontology = row.get("ontology_name")
        if (
            not isinstance(curie, str)
            or not isinstance(label, str)
            or not isinstance(row_ontology, str)
            or row.get("is_obsolete") is True
        ):
            continue
        candidates.append(OntologyCandidate(curie=curie, label=label, ontology=row_ontology))
    return candidates
