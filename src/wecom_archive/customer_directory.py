from __future__ import annotations

from pathlib import Path
from typing import Any

from platformdirs import user_data_path

from ._database import upgrade_database
from ._repository import CustomerDirectoryRepository
from ._wecom_client import WeComCustomerClient
from .types import CustomerRecord, GroupChatRecord, SyncResult


class CustomerContactDirectory:
    """Synchronize and query the local WeCom customer-contact directory."""

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
    ) -> None:
        self._client = WeComCustomerClient(
            corp_id=corp_id,
            secret=secret,
            proxy=proxy,
            timeout=timeout,
            base_url=base_url,
        )
        resolved_url = database_url or self._default_database_url(data_dir)
        try:
            upgrade_database(resolved_url)
            self._repository = CustomerDirectoryRepository(resolved_url)
        except Exception:
            self._client.close()
            raise
        self._closed = False

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def __enter__(self) -> CustomerContactDirectory:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._client.close()
        self._repository.close()
        self._closed = True

    def sync_all_once(self) -> tuple[SyncResult, SyncResult]:
        customers = self.sync_customers_once()
        groups = self.sync_group_chats_once()
        return customers, groups

    def sync_customers_once(self) -> SyncResult:
        run_id = self._repository.start_run("customers")
        seen: set[str] = set()
        try:
            follow_users = self._client.get_follow_users()
            for item in self._client.iter_customer_details(follow_users):
                seen.add(self._repository.upsert_customer_item(run_id, item))
            return self._repository.finalize_customers(run_id, len(seen))
        except Exception as exc:
            self._record_failed_run(run_id, exc)
            raise

    def sync_group_chats_once(self) -> SyncResult:
        run_id = self._repository.start_run("group_chats")
        seen: set[str] = set()
        try:
            follow_users = self._client.get_follow_users()
            summaries: dict[str, dict[str, Any]] = {}
            for summary in self._client.iter_group_chat_summaries(follow_users):
                chat_id = summary.get("chat_id")
                if not isinstance(chat_id, str) or not chat_id:
                    from .exceptions import WeComTransportError

                    raise WeComTransportError("Group-chat summary is missing chat_id")
                summaries[chat_id] = summary
            for chat_id, summary in summaries.items():
                detail = self._client.get_group_chat(chat_id)
                seen.add(self._repository.upsert_group_chat(run_id, summary, detail))
            return self._repository.finalize_group_chats(run_id, len(seen))
        except Exception as exc:
            self._record_failed_run(run_id, exc)
            raise

    def get_customer(self, external_userid: str) -> CustomerRecord | None:
        return self._repository.get_customer(external_userid)

    def get_group_chat(self, chat_id: str) -> GroupChatRecord | None:
        return self._repository.get_group_chat(chat_id)

    @staticmethod
    def _default_database_url(data_dir: str | Path | None) -> str:
        root = Path(data_dir) if data_dir is not None else user_data_path("WeComArchive")
        database_dir = root.expanduser().resolve() / "database"
        database_dir.mkdir(parents=True, exist_ok=True)
        database_path = database_dir / "archive.sqlite3"
        return f"sqlite+pysqlite:///{database_path.as_posix()}"

    @staticmethod
    def _safe_error_summary(exc: Exception) -> str:
        from .exceptions import WeComArchiveError

        if isinstance(exc, WeComArchiveError):
            return f"{type(exc).__name__}: {exc}"
        return type(exc).__name__

    def _record_failed_run(self, run_id: str, exc: Exception) -> None:
        try:
            self._repository.fail_run(run_id, self._safe_error_summary(exc))
        except Exception:
            # Preserve the original synchronization failure if the database is
            # also unavailable while recording the failed run.
            pass
