from __future__ import annotations

import json
import logging
from typing import Any

from ..config import ServiceConfig
from ..models import SUPPORTED_MESSAGE_TYPES, ConsumerMessage
from ..storage import ArchiveStore
from .base import Service

log = logging.getLogger(__name__)


class ParseService(Service[tuple[str, str]]):
    def __init__(
        self,
        config: ServiceConfig,
        store: ArchiveStore,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self.store = store

    def acquire(self) -> tuple[str, str] | None:
        return self.store.claim("parse")

    def process(self, task: tuple[str, str]) -> None:
        msgid, raw_text = task
        try:
            data = json.loads(raw_text)
            if not isinstance(data, dict):
                raise ValueError("明文消息必须为 JSON 对象")
            model = SUPPORTED_MESSAGE_TYPES.get(data.get("msgtype"))
            if model is None:
                self.store.fail("parse", msgid, "unsupported", None)
                return
            parsed = model.model_validate(data)
            if parsed.msgid != msgid:
                raise ValueError("明文消息 ID 与密文消息 ID 不一致")
            message = ConsumerMessage(
                msgid=msgid,
                seq=self.store.sequence(msgid),
                msgtype=parsed.msgtype,
                msgtime=parsed.msgtime,
                sender=parsed.sender,
                tolist=parsed.tolist,
                roomid=parsed.roomid,
                action=parsed.action,
                data=data,
            )
            self.store.parsed(message)
        except Exception as exc:
            error = type(exc).__name__
            self.store.fail("parse", msgid, "failed", error)
            log.error("解析失败 msgid=%s error=%s", msgid, error)
