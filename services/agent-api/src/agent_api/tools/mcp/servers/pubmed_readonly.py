"""Read-only PubMed MCP server (NCBI E-utilities) for AgentOS.

Run: python -m agent_api.tools.mcp.servers.pubmed_readonly
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any, cast
from urllib.parse import urlencode

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("agentos-pubmed-readonly")

_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_TOOL = "agentos"
_EMAIL = "agentos@localhost"


def _get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
    query = {
        **params,
        "tool": _TOOL,
        "email": _EMAIL,
        "retmode": "json",
    }
    url = f"{_EUTILS}/{path}?{urlencode(query)}"
    with httpx.Client(timeout=30.0, trust_env=False) as client:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("unexpected NCBI JSON shape")
    return cast(dict[str, Any], payload)


def _get_text(path: str, params: dict[str, Any]) -> str:
    query = {**params, "tool": _TOOL, "email": _EMAIL}
    url = f"{_EUTILS}/{path}?{urlencode(query)}"
    with httpx.Client(timeout=30.0, trust_env=False) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in cast(list[object], value):
        if item is not None:
            out.append(str(item))
    return out


@mcp.tool()
def pubmed_search(query: str, max_results: int = 5) -> str:
    """Search PubMed (read-only). Returns PMIDs, titles, and journal info as JSON."""

    normalized = query.strip()
    if not normalized:
        return json.dumps({"error": "query must not be blank"}, ensure_ascii=False)
    limit = max(1, min(10, int(max_results)))
    search = _get_json(
        "esearch.fcgi",
        {"db": "pubmed", "term": normalized, "retmax": str(limit)},
    )
    esearch_raw = search.get("esearchresult")
    esearch = cast(dict[str, Any], esearch_raw) if isinstance(esearch_raw, dict) else {}
    id_list = _as_str_list(esearch.get("idlist"))
    if not id_list:
        return json.dumps(
            {"query": normalized, "count": 0, "results": []},
            ensure_ascii=False,
        )

    ids = ",".join(id_list[:limit])
    summary = _get_json(
        "esummary.fcgi",
        {"db": "pubmed", "id": ids},
    )
    result_map_raw = summary.get("result", {})
    result_map: dict[str, Any] = (
        cast(dict[str, Any], result_map_raw) if isinstance(result_map_raw, dict) else {}
    )

    results: list[dict[str, Any]] = []
    for pmid in id_list[:limit]:
        row_raw = result_map.get(pmid)
        if not isinstance(row_raw, dict):
            continue
        row = cast(dict[str, Any], row_raw)
        results.append(
            {
                "pmid": pmid,
                "title": row.get("title"),
                "source": row.get("source"),
                "pubdate": row.get("pubdate"),
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            },
        )
    return json.dumps(
        {"query": normalized, "count": len(results), "results": results},
        ensure_ascii=False,
    )


@mcp.tool()
def pubmed_get_abstract(pmid: str) -> str:
    """Fetch one PubMed abstract by PMID (read-only). Returns JSON with title/abstract/url."""

    normalized = pmid.strip()
    if not normalized.isdigit():
        return json.dumps({"error": "pmid must be numeric"}, ensure_ascii=False)

    xml_text = _get_text(
        "efetch.fcgi",
        {"db": "pubmed", "id": normalized, "retmode": "xml"},
    )
    root = ET.fromstring(xml_text)
    article = root.find(".//PubmedArticle")
    if article is None:
        return json.dumps({"error": "article not found", "pmid": normalized}, ensure_ascii=False)

    title_el = article.find(".//ArticleTitle")
    title = "".join(title_el.itertext()).strip() if title_el is not None else ""
    abstract_parts = [
        "".join(node.itertext()).strip()
        for node in article.findall(".//Abstract/AbstractText")
        if "".join(node.itertext()).strip()
    ]
    abstract = "\n\n".join(abstract_parts)
    return json.dumps(
        {
            "pmid": normalized,
            "title": title,
            "abstract": abstract,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{normalized}/",
        },
        ensure_ascii=False,
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
