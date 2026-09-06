"""Read-only retrieval evaluation against an explicit corpus and expected source passages."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from agent_api.agent import create_background_http_client
from agent_api.config import get_settings
from agent_api.db.session import session_factory
from agent_api.memory.embed import embed_text
from agent_api.tools.knowledge.tool import search_knowledge_chunks


async def evaluate(suite: Path, output: Path) -> None:
    cases = json.loads(suite.read_text())["cases"]
    results: list[dict[str, Any]] = []
    cfg = get_settings()
    async with create_background_http_client(cfg) as client:
        for case in cases:
            vector = await embed_text(
                case["query"], client, settings=cfg, enabled=cfg.knowledge_embedding_enabled
            )
            diagnostics: dict[str, Any] = {}
            async with session_factory() as session:
                hits = await search_knowledge_chunks(
                    session,
                    query=case["query"],
                    disease_tags=case.get("disease_tags", []),
                    max_results=5,
                    knowledge_base_slugs=case.get("bases", ["mma-pa"]),
                    query_embedding=vector,
                    current_embedding_model=cfg.resolved_background_embedding_model,
                    diagnostics=diagnostics,
                )
            expected = case.get("expected", [])
            ranks = [
                index + 1
                for index, hit in enumerate(hits)
                if any(term in hit["content"] or term in hit["title"] for term in expected)
            ]
            ok = bool(ranks) if expected else not hits
            results.append(
                {
                    "id": case["id"],
                    "passed": ok,
                    "first_relevant_rank": min(ranks) if ranks else None,
                    "diagnostics": diagnostics,
                    "results": hits,
                }
            )
    report = {
        "suite": str(suite),
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Retrieval evaluation: {report['passed']}/{report['total']}; {output}")
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    asyncio.run(evaluate(args.suite, args.output))
