"""Create and migrate an explicitly isolated test database on the existing server."""

import asyncio
import os
import subprocess

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from agent_api.config import get_settings


async def prepare() -> None:
    business = make_url(get_settings().database_url)
    target = make_url(
        os.environ.get("TEST_DATABASE_URL")
        or business.set(database=f"{business.database}_test").render_as_string(hide_password=False)
    )
    if not target.database or not target.database.endswith("_test") or target == business:
        raise RuntimeError("Refusing non-test database")
    engine = create_async_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        exists = await connection.scalar(
            text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": target.database}
        )
        if not exists:
            name = connection.dialect.identifier_preparer.quote(target.database)
            await connection.execute(text(f"CREATE DATABASE {name}"))
    await engine.dispose()
    env = {**os.environ, "DATABASE_URL": target.render_as_string(hide_password=False)}
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], env=env, check=True)
    print("Isolated test database migrated:", target.database)


if __name__ == "__main__":
    asyncio.run(prepare())
