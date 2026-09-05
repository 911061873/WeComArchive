from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from wecom_archive.customer_sync import CustomerContactDirectory, SyncResult
from wecom_archive.customer_sync.exceptions import WeComArchiveError
from wecom_archive.customer_sync.schemas import (
    ExternalContactDetailItem,
    ExternalContactFailInfo,
    ExternalContactPageResponse,
    ExternalContactResponse,
)


def _customer_item(external_userid: str, userid: str) -> ExternalContactDetailItem:
    return ExternalContactDetailItem.model_validate(
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

        async def iter_customer_detail_pages(self, external_userid):
            yield ExternalContactResponse.model_validate(
                {
                    "external_contact": {"external_userid": external_userid},
                }
            )

        async def get_follow_user_ids(self) -> list[str]:
            return [f"user-{index}" for index in range(201)]

        async def iter_customer_batch_pages(
            self, userids: list[str]
        ) -> AsyncIterator[ExternalContactPageResponse]:
            self.chunk_sizes.append(len(userids))
            self.active_fetches += 1
            self.max_active_fetches = max(self.max_active_fetches, self.active_fetches)
            try:
                await asyncio.sleep(0.01)
                yield ExternalContactPageResponse(
                    external_contact_list=[_customer_item(f"customer-{userids[0]}", userids[0])]
                )
            finally:
                self.active_fetches -= 1

    class FakeRepository:
        def __init__(self) -> None:
            self.active_writes = 0
            self.max_active_writes = 0

        async def upsert_contact_users(self, run_id, userids):
            assert userids

        async def upsert_customer_detail_page(self, run_id, page):
            assert page.external_contact.external_userid

        async def start_run(self, domain: str) -> str:
            assert domain == "customers"
            return "run-id"

        async def upsert_customer_items(
            self, run_id: str, items: list[ExternalContactDetailItem]
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

        async def fail_run(self, run_id: str, error_summary: str, *, failure_details=None) -> None:
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


@pytest.mark.parametrize("has_items", [False, True])
async def test_partial_failure_marks_run_failed_without_finalizing(has_items: bool) -> None:
    written: list[str] = []
    failures: list[str] = []

    class FakeClient:
        async def iter_customer_detail_pages(self, external_userid):
            yield ExternalContactResponse.model_validate(
                {
                    "external_contact": {"external_userid": external_userid},
                }
            )

        async def get_follow_user_ids(self) -> list[str]:
            return ["alice"]

        async def iter_customer_batch_pages(
            self, userids: list[str]
        ) -> AsyncIterator[ExternalContactPageResponse]:
            yield ExternalContactPageResponse(
                external_contact_list=[_customer_item("previous-page", "alice")]
            )
            yield ExternalContactPageResponse(
                external_contact_list=[_customer_item("partial-page", "alice")]
                if has_items
                else [],
                fail_info=ExternalContactFailInfo(unlicensed_userid_list=["alice"]),
                next_cursor="more",
            )
            pytest.fail("同步层应在部分失败页停止消费")

    class FakeRepository:
        async def upsert_contact_users(self, run_id, userids):
            assert userids

        async def upsert_customer_detail_page(self, run_id, page):
            assert page.external_contact.external_userid

        async def start_run(self, domain: str) -> str:
            return "run-id"

        async def upsert_customer_items(
            self, run_id: str, items: list[ExternalContactDetailItem]
        ) -> set[str]:
            ids = {item.external_contact.external_userid for item in items}
            written.extend(ids)
            return ids

        async def finalize_customers(self, run_id: str, seen_count: int) -> SyncResult:
            pytest.fail("不完整扫描不得完成同步或清理旧数据")

        async def fail_run(self, run_id: str, error_summary: str, *, failure_details=None) -> None:
            assert run_id == "run-id"
            failures.append(error_summary)

    directory = object.__new__(CustomerContactDirectory)
    directory._client = FakeClient()
    directory._repository = FakeRepository()
    directory._request_concurrency = 2
    directory._initialized = True
    directory._customer_sync_lock = asyncio.Lock()
    directory._closed = False

    with pytest.raises(WeComArchiveError, match="同步结果不完整"):
        await directory.sync_customers_once()

    assert written == ["previous-page"]
    assert len(failures) == 1
    assert failures[0].startswith("WeComArchiveError:")
