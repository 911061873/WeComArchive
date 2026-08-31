from __future__ import annotations

import pytest

from wecom_archive._database import normalize_async_database_url, upgrade_database
from wecom_archive._repository import CustomerDirectoryRepository


def test_normalize_async_database_urls() -> None:
    assert normalize_async_database_url("sqlite:///archive.db").startswith("sqlite+aiosqlite:///")
    assert normalize_async_database_url("mysql://u:p@localhost/archive").startswith(
        "mysql+asyncmy://"
    )
    assert normalize_async_database_url("postgresql://u:p@localhost/archive").startswith(
        "postgresql+asyncpg://"
    )


@pytest.mark.asyncio
async def test_async_sqlite_migration_and_repository(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'archive.db').as_posix()}"
    await upgrade_database(database_url)
    repository = CustomerDirectoryRepository(database_url)
    try:
        run_id = await repository.start_run("customers")
        result = await repository.finalize_customers(run_id, 0)
        assert result.domain == "customers"
        assert result.seen_count == 0
    finally:
        await repository.close()
