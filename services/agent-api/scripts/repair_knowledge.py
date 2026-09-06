"""Repair an explicitly backed-up legacy corpus; quarantine only known test fixtures."""

import argparse
import asyncio
import json
import re
from pathlib import Path
from uuid import UUID

import httpx

from agent_api.agent import create_background_http_client
from agent_api.api.ops_auth import get_ops_subject
from agent_api.config import get_settings
from agent_api.db.models import KnowledgeDocument
from agent_api.db.session import session_factory
from agent_api.main import app
from agent_api.runtime import AgentRuntime

SUMMARIES = {
    "mma-pa-core-v1",
    "isolated-mma-genereviews-2022",
    "pa-genereviews-2024",
    "mma-pa-guideline-2021",
}
TEST_SLUG = re.compile(
    r"(?:ops-(?:import-a|import-embed|json-a|image|pdf|conflict|dedup|ontology)|pubmed)-[0-9a-f]{32}"
)


async def repair(backup: Path, apply: bool) -> None:
    saved = json.loads(backup.read_text())
    rows = saved["knowledge_documents"]
    actions = [
        {
            "id": r["id"],
            "slug": r["slug"],
            "action": "rebuild_vectors"
            if r["slug"] in SUMMARIES
            else "withdraw_test"
            if TEST_SLUG.fullmatch(r["slug"])
            else "manual_review",
        }
        for r in rows
    ]
    print(json.dumps(actions, ensure_ascii=False, indent=2))
    if not apply:
        return
    async with session_factory() as session, session.begin():
        for action in actions:
            document = await session.get(KnowledgeDocument, UUID(action["id"]))
            if (
                document is None
                or document.slug != action["slug"]
                or document.import_status == "processing"
            ):
                raise RuntimeError("Inventory changed; re-audit before repair")
            if action["action"] == "withdraw_test":
                document.review_status = "withdrawn"
                document.ingestion = {
                    **document.ingestion,
                    "migration_note": "quarantined test fixture; retained in backup",
                }
    cfg = get_settings()
    async with create_background_http_client(cfg) as bg:
        app.state.runtime = AgentRuntime(
            agent=None, model_semaphore=asyncio.Semaphore(1), background_http_client=bg
        )
        app.dependency_overrides[get_ops_subject] = lambda: "knowledge-repair"
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://repair"
            ) as client:
                for action in actions:
                    if action["action"] != "rebuild_vectors":
                        continue
                    prefix = f"/v1/ops/knowledge/documents/{action['id']}"
                    submitted = await client.post(prefix + "/rebuild", json={"mode": "vectors"})
                    submitted.raise_for_status()
                    for _ in range(300):
                        detail = (await client.get(prefix)).json()
                        if detail["import_status"] != "processing":
                            if (
                                detail["import_status"] != "ready"
                                or detail["embedded_chunks"] != detail["chunk_count"]
                            ):
                                raise RuntimeError(f"Repair incomplete: {action['slug']}")
                            print(action["slug"], "valid vectors", detail["embedded_chunks"])
                            break
                        await asyncio.sleep(1)
                    else:
                        raise RuntimeError("Repair timed out")
        finally:
            from agent_api.knowledge.import_jobs import stop_import_jobs

            await stop_import_jobs()
            app.dependency_overrides.pop(get_ops_subject, None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(repair(args.backup, args.apply))
