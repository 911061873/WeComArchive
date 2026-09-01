from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from platformdirs import user_data_path
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, SecretStr, field_validator

from .client import WeComCustomerClient
from .database import normalize_async_database_url, upgrade_database
from .exceptions import WeComArchiveError
from .repository import CustomerDirectoryRepository
from .schemas import GroupChatSummary
from .types import CustomerRecord, GroupChatRecord, SyncResult


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[offset : offset + size] for offset in range(0, len(values), size)]


class _DirectoryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    corp_id: str
    secret: SecretStr
    database_url: str
    proxy: str | None = None
    timeout: PositiveFloat = 20.0
    base_url: str = "https://qyapi.weixin.qq.com"
    qps: PositiveFloat = 50.0
    request_concurrency: int = Field(default=8, ge=1)
    max_retries: int = Field(default=2, ge=0)
    retry_backoff: float = Field(default=0.5, ge=0)

    @field_validator("corp_id", "database_url", "base_url")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能为空白字符串")
        return value

    @field_validator("secret")
    @classmethod
    def secret_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("不能为空白字符串")
        return value


class CustomerContactDirectory:
    """异步同步并查询本地客户联系资料目录。"""

    def __init__(
        self,
        *,
        corp_id: str,
        secret: str,
        database_url: str | None = None,
        data_dir: str | Path | None = None,
        proxy: str | None = None,
        timeout: float = 20.0,
        base_url: str = "https://qyapi.weixin.qq.com",
        qps: float = 50.0,
        request_concurrency: int = 8,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
    ) -> None:
        resolved_url = normalize_async_database_url(
            database_url or self._default_database_url(data_dir)
        )
        config = _DirectoryConfig(
            corp_id=corp_id,
            secret=SecretStr(secret),
            database_url=resolved_url,
            proxy=proxy,
            timeout=timeout,
            base_url=base_url,
            qps=qps,
            request_concurrency=request_concurrency,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
        )
        self._client = WeComCustomerClient(
            corp_id=config.corp_id,
            secret=config.secret.get_secret_value(),
            proxy=config.proxy,
            timeout=config.timeout,
            base_url=config.base_url,
            qps=config.qps,
            request_concurrency=config.request_concurrency,
            max_retries=config.max_retries,
            retry_backoff=config.retry_backoff,
        )
        self._database_url = config.database_url
        self._repository = CustomerDirectoryRepository(config.database_url)
        self._request_concurrency = config.request_concurrency
        self._initialized = False
        self._initialize_lock = asyncio.Lock()
        self._customer_sync_lock = asyncio.Lock()
        self._closed = False

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    async def __aenter__(self) -> CustomerContactDirectory:
        await self._ensure_initialized()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        await self._client.aclose()
        await self._repository.close()
        self._closed = True

    async def sync_all_once(self) -> tuple[SyncResult, SyncResult]:
        customers = await self.sync_customers_once()
        groups = await self.sync_group_chats_once()
        return customers, groups

    async def sync_customers_once(self) -> SyncResult:
        await self._ensure_initialized()
        async with self._customer_sync_lock:
            return await self._sync_customers_once()

    async def _sync_customers_once(self) -> SyncResult:
        run_id = await self._repository.start_run("customers")
        seen: set[str] = set()
        write_lock = asyncio.Lock()
        try:
            follow_users = await self._client.get_follow_users()

            async def sync_chunk(userids: list[str]) -> None:
                async for items in self._client.iter_customer_details(userids):
                    if not items:
                        continue
                    async with write_lock:
                        seen.update(await self._repository.upsert_customer_items(run_id, items))

            semaphore = asyncio.Semaphore(self._request_concurrency)

            async def run_chunk(userids: list[str]) -> None:
                async with semaphore:
                    await sync_chunk(userids)

            tasks = [
                asyncio.create_task(run_chunk(userids)) for userids in _chunks(follow_users, 100)
            ]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

            return await self._repository.finalize_customers(run_id, len(seen))
        except Exception as exc:
            await self._record_failed_run(run_id, exc)
            raise

    async def sync_group_chats_once(self) -> SyncResult:
        await self._ensure_initialized()
        run_id = await self._repository.start_run("group_chats")
        seen: set[str] = set()
        try:
            follow_users = await self._client.get_follow_users()
            summaries: dict[str, GroupChatSummary] = {}
            async for summary in self._client.iter_group_chat_summaries(follow_users):
                summaries[summary.chat_id] = summary
            for chat_id, summary in summaries.items():
                detail = await self._client.get_group_chat(chat_id)
                seen.add(await self._repository.upsert_group_chat(run_id, summary, detail))
            return await self._repository.finalize_group_chats(run_id, len(seen))
        except Exception as exc:
            await self._record_failed_run(run_id, exc)
            raise

    async def get_customer(self, external_userid: str) -> CustomerRecord | None:
        await self._ensure_initialized()
        return await self._repository.get_customer(external_userid)

    async def get_group_chat(self, chat_id: str) -> GroupChatRecord | None:
        await self._ensure_initialized()
        return await self._repository.get_group_chat(chat_id)

    async def _ensure_initialized(self) -> None:
        if self._closed:
            raise RuntimeError("CustomerContactDirectory 已关闭")
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._closed:
                raise RuntimeError("CustomerContactDirectory 已关闭")
            if not self._initialized:
                try:
                    await upgrade_database(self._database_url)
                    self._initialized = True
                except Exception:
                    await self.aclose()
                    raise

    @staticmethod
    def _default_database_url(data_dir: str | Path | None) -> str:
        root = Path(data_dir) if data_dir is not None else user_data_path("WeComArchive")
        database_dir = root.expanduser().resolve() / "database"
        database_dir.mkdir(parents=True, exist_ok=True)
        database_path = database_dir / "archive.sqlite3"
        return f"sqlite+aiosqlite:///{database_path.as_posix()}"

    @staticmethod
    def _safe_error_summary(exc: Exception) -> str:
        if isinstance(exc, WeComArchiveError):
            return f"{type(exc).__name__}: {exc}"
        return type(exc).__name__

    async def _record_failed_run(self, run_id: str, exc: Exception) -> None:
        try:
            await self._repository.fail_run(run_id, self._safe_error_summary(exc))
        except Exception:
            pass
