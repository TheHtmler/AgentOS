"""Live PDF -> Ops import -> vectors -> retrieval -> rebuild acceptance.

Creates and removes only its own temporary knowledge base. Uses configured
vision/embedding endpoints; does not start background schedulers or touch chats.
"""

import asyncio
import json
from uuid import uuid4

import httpx
import pymupdf
from sqlalchemy import select

from agent_api.agent import create_background_http_client, create_background_vision_http_client
from agent_api.api.ops_auth import get_ops_subject
from agent_api.config import get_settings
from agent_api.db.models import KnowledgeBase, KnowledgeDocument
from agent_api.db.session import session_factory
from agent_api.main import app
from agent_api.runtime import AgentRuntime
from agent_api.tools.knowledge.tool import run_knowledge_search
from agent_api.tools.search.tool import AgentDeps


async def main() -> None:
    cfg = get_settings()
    slug = f"acceptance-{uuid4().hex}"
    async with session_factory() as session, session.begin():
        base = KnowledgeBase(slug=slug, name="Temporary acceptance", status="active")
        session.add(base)
        await session.flush()
        base_id = base.id
    source = pymupdf.open()
    page = source.new_page()
    page.insert_text(  # pyright: ignore[reportUnknownMemberType]
        (60, 80),
        "Calibration manual\nStation ZXQ-491\nThe calibration interval is 1234 hours.\n"
        "Record each inspection in the station log.",
    )
    pdf = source.tobytes()  # pyright: ignore[reportUnknownMemberType]
    source.close()
    bg = create_background_http_client(cfg)
    vision = create_background_vision_http_client(cfg)
    app.state.runtime = AgentRuntime(
        agent=None,
        model_semaphore=asyncio.Semaphore(1),
        background_http_client=bg,
        background_vision_http_client=vision,
    )
    app.dependency_overrides[get_ops_subject] = lambda: "knowledge-acceptance"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://acceptance"
        ) as client:
            response = await client.post(
                "/v1/ops/knowledge/import",
                data={"mode": "pdf", "base": slug, "slug": slug, "title": "Calibration manual"},
                files={"file": ("manual.pdf", pdf, "application/pdf")},
            )
            response.raise_for_status()
            doc_id = response.json()["documents"][0]["id"]

            async def settled():
                for _ in range(600):
                    detail = await client.get(f"/v1/ops/knowledge/documents/{doc_id}")
                    detail.raise_for_status()
                    data = detail.json()
                    if data["import_status"] != "processing":
                        assert data["import_status"] == "ready", data.get("import_error")
                        assert data["embedded_chunks"] == data["chunk_count"] > 0, data
                        return data
                    await asyncio.sleep(1)
                raise RuntimeError("Acceptance import timed out")

            detail = await settled()
            assert "1234" in " ".join(c["content"] for c in detail["chunks"])
            downloaded = await client.get(f"/v1/ops/knowledge/documents/{doc_id}/source")
            assert downloaded.content == pdf
            hits = json.loads(
                await run_knowledge_search(
                    AgentDeps(
                        http_client=bg, knowledge_base_slugs=[slug], persist_tool_events=False
                    ),
                    query="ZXQ-491 calibration interval",
                )
            )
            assert hits["count"] and "1234" in hits["results"][0]["content"]
            assert hits["diagnostics"]["usable_vector_chunks"] > 0
            rebuilt = await client.post(
                f"/v1/ops/knowledge/documents/{doc_id}/rebuild", json={"mode": "vectors"}
            )
            rebuilt.raise_for_status()
            await settled()
            print(
                json.dumps(
                    {
                        "pdf_import": "passed",
                        "source_download": "passed",
                        "retrieval": "passed",
                        "rebuild": "passed",
                        "chunks": detail["chunk_count"],
                        "quality": detail["quality"],
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        app.dependency_overrides.pop(get_ops_subject, None)
        from agent_api.knowledge.import_jobs import stop_import_jobs

        await stop_import_jobs()
        await bg.aclose()
        if vision:
            await vision.aclose()
        async with session_factory() as session, session.begin():
            documents = list(
                await session.scalars(
                    select(KnowledgeDocument).where(KnowledgeDocument.knowledge_base_id == base_id)
                )
            )
            for document in documents:
                await session.delete(document)
            base = await session.get(KnowledgeBase, base_id)
            if base:
                await session.delete(base)


if __name__ == "__main__":
    asyncio.run(main())
