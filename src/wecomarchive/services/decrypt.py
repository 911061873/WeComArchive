from __future__ import annotations

import logging
from typing import Any

from ..config import ServiceConfig
from ..models import EncryptedChat
from ..sdk import ArchiveClient, MissingKeyError
from ..storage import ArchiveStore
from .base import Service

log = logging.getLogger(__name__)


class DecryptService(Service[EncryptedChat]):
    def __init__(
        self,
        config: ServiceConfig,
        store: ArchiveStore,
        client: ArchiveClient,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self.store, self.client = store, client

    def acquire(self) -> EncryptedChat | None:
        return self.store.claim("decrypt")

    def process(self, task: EncryptedChat) -> None:
        chat = task
        try:
            raw_text = self.client.decrypt(chat)
            self.store.decrypted(chat, raw_text)
        except MissingKeyError:
            self.store.fail("decrypt", chat.msgid, "waiting_key", None)
        except Exception as exc:
            error = type(exc).__name__
            self.store.fail("decrypt", chat.msgid, "failed", error)
            log.error("解密失败 msgid=%s error=%s", chat.msgid, error)
