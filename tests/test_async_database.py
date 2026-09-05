from __future__ import annotations

import pytest

from wecom_archive.customer_sync.database import normalize_async_database_url, upgrade_database
from wecom_archive.customer_sync.repository import CustomerDirectoryRepository
from wecom_archive.customer_sync.schemas import ExternalContactDetailItem


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


@pytest.mark.asyncio
async def test_repository_upserts_customer_items_in_a_batch(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'archive.db').as_posix()}"
    await upgrade_database(database_url)
    repository = CustomerDirectoryRepository(database_url)
    items = [
        ExternalContactDetailItem.model_validate(
            {
                "external_contact": {"external_userid": "customer-1", "name": "客户"},
                "follow_info": {"userid": userid, "remark": remark},
            }
        )
        for userid, remark in (("alice", "销售一"), ("bob", "销售二"))
    ]
    try:
        run_id = await repository.start_run("customers")
        seen = await repository.upsert_customer_items(run_id, items)
        result = await repository.finalize_customers(run_id, len(seen))
        customer = await repository.get_customer("customer-1")

        assert seen == {"customer-1"}
        assert result.seen_count == 1
        assert customer is not None
        assert [follow.userid for follow in customer.follows] == ["alice", "bob"]
    finally:
        await repository.close()
