from __future__ import annotations

import logging
from typing import Any

from ..config import ServiceConfig
from ..sdk import ArchiveClient
from ..storage import ArchiveStore
from .base import Service

log = logging.getLogger(__name__)


class PullService(Service[int]):
    def __init__(
        self,
        config: ServiceConfig,
        store: ArchiveStore,
        client: ArchiveClient,
        **kwargs: Any,
    ) -> None:
        if config.threads != 1:
            raise ValueError("拉取服务固定单线程")
        super().__init__(config, **kwargs)
        self.store, self.client = store, client

    def acquire(self) -> int:
        return self.store.cursor()

    def process(self, task: int) -> None:
        cursor = task
        try:
            self.store.save_batch(self.client.fetch(cursor))
        except Exception as exc:
            log.error("拉取或保存失败，保留游标等待下一轮 error=%s", type(exc).__name__)

    def after_task(self) -> None:
        self.wait()
