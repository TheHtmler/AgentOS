"""Idempotently upsert the curated MMA/PA knowledge base from JSON."""

from __future__ import annotations

import asyncio

from agent_api.db.knowledge_store import upsert_mma_pa_knowledge
from agent_api.db.session import close_database, session_factory


async def seed_knowledge() -> int:
    async with session_factory() as session, session.begin():
        return await upsert_mma_pa_knowledge(session)


async def main() -> int:
    count = await seed_knowledge()
    print(f"Seeded MMA/PA knowledge chunks: {count}")
    await close_database()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
