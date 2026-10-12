from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from typing import Any

from ..config import PullServiceConfig
from ..models import EncryptedChat
from ..storage import ArchiveStore
from .base import Service

logger = logging.getLogger(__name__)


class PullService(Service):
    def __init__(
        self,
        config: PullServiceConfig,
        store: ArchiveStore,
        *,
        batch_size: int = 1000,
        timeout: int = 5,
        proxy: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self.store = store
        self.batch_size, self.timeout, self.proxy = batch_size, timeout, proxy

    def run_once(self) -> None:
        with self.work_scope() as started:
            if not started:
                return
            self._pull()
        self.wait()

    def _pull(self) -> None:
        try:
            if self.sdk is None:
                raise RuntimeError("SDK 未初始化")
            cursor = self.store.cursor()
            logger.info("开始拉取轮询，游标=%s", cursor)
            with self._sdk_lock:
                response = self.sdk.get_chat_data(
                    seq=cursor,
                    limit=self.batch_size,
                    timeout=self.timeout,
                    proxy=self.proxy,
                )
            messages = [
                EncryptedChat.model_validate(asdict(chat) if is_dataclass(chat) else chat)
                for chat in response.chatdata
            ]
            self.store.save_batch(messages)
            logger.info("拉取轮询完成，游标=%s，拉取消息数=%s", cursor, len(messages))
        except Exception as exc:
            logger.error("拉取或保存失败，保留游标等待下一轮 error=%s", type(exc).__name__)
