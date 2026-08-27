"""Idempotently upsert the curated MMA/PA knowledge base from JSON."""

from __future__ import annotations

import asyncio

from agent_api.agent import create_background_http_client
from agent_api.config import get_settings
from agent_api.db.knowledge_store import upsert_mma_pa_knowledge
from agent_api.db.session import close_database, session_factory


async def seed_knowledge() -> int:
    settings = get_settings()
    if settings.knowledge_embedding_enabled:
        # Embeddings go to the fixed background endpoint (may require auth) —
        # never the chat-provider client.
        async with (
            create_background_http_client(settings) as http_client,
            session_factory() as session,
            session.begin(),
        ):
            return await upsert_mma_pa_knowledge(session, http_client=http_client)
    async with session_factory() as session, session.begin():
        return await upsert_mma_pa_knowledge(session)


async def main() -> int:
    count = await seed_knowledge()
    print(f"Seeded MMA/PA knowledge chunks: {count}")
    await close_database()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
