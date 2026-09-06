"""Export public knowledge only; never mutate the database."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from agent_api.db.session import session_factory


async def audit(output: Path) -> None:
    tables = (
        "knowledge_bases",
        "knowledge_documents",
        "knowledge_chunks",
        "knowledge_document_snapshots",
    )
    backup: dict[str, object] = {"exported_at": datetime.now(UTC).isoformat()}
    async with session_factory() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        for table in tables:
            result = await session.execute(text(f"SELECT * FROM {table}"))
            backup[table] = [dict(row) for row in result.mappings()]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(backup, handle, ensure_ascii=False, default=str, indent=2)
    output.chmod(0o600)
    print(f"Backup: {output}")
    for table in tables:
        print(table, len(backup[table]))  # type: ignore[arg-type]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    asyncio.run(audit(parser.parse_args().output))
