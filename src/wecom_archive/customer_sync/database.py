from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine


def normalize_async_database_url(database_url: str) -> str:
    url = make_url(database_url)
    replacements = {
        "sqlite": "sqlite+aiosqlite",
        "sqlite+pysqlite": "sqlite+aiosqlite",
        "mysql": "mysql+asyncmy",
        "mysql+pymysql": "mysql+asyncmy",
        "postgres": "postgresql+asyncpg",
        "postgresql": "postgresql+asyncpg",
        "postgresql+psycopg": "postgresql+asyncpg",
        "postgresql+psycopg2": "postgresql+asyncpg",
    }
    return url.set(drivername=replacements.get(url.drivername, url.drivername)).render_as_string(
        hide_password=False
    )


async def upgrade_database(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_run_upgrade)
    finally:
        await engine.dispose()


def _run_upgrade(connection: Connection) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations").resolve()))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
