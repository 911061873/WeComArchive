from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from wecom_archive.customer_sync import CustomerContactDirectory, SyncResult
from wecom_archive.customer_sync.schemas import CustomerDetailItem


def _customer_item(external_userid: str, userid: str) -> CustomerDetailItem:
    return CustomerDetailItem.model_validate(
        {
            "external_contact": {"external_userid": external_userid},
            "follow_info": {"userid": userid},
        }
    )


@pytest.mark.asyncio
async def test_customer_sync_fetches_chunks_concurrently_and_writes_sequentially() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.active_fetches = 0
            self.max_active_fetches = 0
            self.chunk_sizes: list[int] = []

        async def get_follow_users(self) -> list[str]:
            return [f"user-{index}" for index in range(201)]

        async def iter_customer_details(
            self, userids: list[str]
        ) -> AsyncIterator[list[CustomerDetailItem]]:
            self.chunk_sizes.append(len(userids))
            self.active_fetches += 1
            self.max_active_fetches = max(self.max_active_fetches, self.active_fetches)
            try:
                await asyncio.sleep(0.01)
                yield [_customer_item(f"customer-{userids[0]}", userids[0])]
            finally:
                self.active_fetches -= 1

    class FakeRepository:
        def __init__(self) -> None:
            self.active_writes = 0
            self.max_active_writes = 0

        async def start_run(self, domain: str) -> str:
            assert domain == "customers"
            return "run-id"

        async def upsert_customer_items(
            self, run_id: str, items: list[CustomerDetailItem]
        ) -> set[str]:
            assert run_id == "run-id"
            self.active_writes += 1
            self.max_active_writes = max(self.max_active_writes, self.active_writes)
            try:
                await asyncio.sleep(0.01)
                return {item.external_contact.external_userid for item in items}
            finally:
                self.active_writes -= 1

        async def finalize_customers(self, run_id: str, seen_count: int) -> SyncResult:
            return SyncResult(domain="customers", run_id=run_id, seen_count=seen_count)

        async def fail_run(self, run_id: str, error_summary: str) -> None:
            raise AssertionError((run_id, error_summary))

    client = FakeClient()
    repository = FakeRepository()
    directory = object.__new__(CustomerContactDirectory)
    directory._client = client
    directory._repository = repository
    directory._request_concurrency = 2
    directory._initialized = True
    directory._initialize_lock = asyncio.Lock()
    directory._customer_sync_lock = asyncio.Lock()
    directory._closed = False

    result = await directory.sync_customers_once()

    assert sorted(client.chunk_sizes) == [1, 100, 100]
    assert client.max_active_fetches == 2
    assert repository.max_active_writes == 1
    assert result.seen_count == 3
