from __future__ import annotations

import logging
from dataclasses import dataclass

from .db import TYPE_MODELS, Database, DecryptedMessage, EncryptedMessage, Progress
from .models import ConsumerMessage, EncryptedChat
from .parser import parse_type

log = logging.getLogger(__name__)


@dataclass
class PreparedMessage:
    encrypted: EncryptedChat
    message: ConsumerMessage | None = None
    decrypt_error: str | None = None
    type_fields: dict | None = None
    parse_error: str | None = None


def prepare(chat: EncryptedChat, data: dict) -> PreparedMessage:
    msgtype = data.get("msgtype")
    msgtype = msgtype if isinstance(msgtype, str) and msgtype else "unknown"
    timestamp = data.get("msgtime")
    if type(timestamp) is not int or not 0 <= timestamp < 2**63:
        timestamp = None
    result = PreparedMessage(
        chat,
        ConsumerMessage(
            msgid=chat.msgid, seq=chat.seq, msgtype=msgtype, msgtime=timestamp, data=data
        ),
    )
    try:
        result.type_fields = parse_type(msgtype, data)
    except (ValueError, TypeError) as exc:
        result.parse_error = str(exc)
        log.error("类型解析失败 msgid=%s type=%s", chat.msgid, msgtype)
    return result


class ArchiveStore:
    def __init__(self, database: Database):
        self.db = database

    def cursor(self) -> int:
        with self.db.session() as session:
            progress = session.get(Progress, 1)
            return progress.cursor if progress else 0

    def existing_ids(self, chats: list[EncryptedChat]) -> set[str]:
        # 分块避免 SQLite 参数上限；同一数据库仅允许一个采集实例。
        from sqlalchemy import select

        ids = [chat.msgid for chat in chats]
        found = set()
        with self.db.session() as session:
            for offset in range(0, len(ids), 500):
                found.update(
                    session.scalars(
                        select(EncryptedMessage.msgid).where(
                            EncryptedMessage.msgid.in_(ids[offset : offset + 500])
                        )
                    )
                )
        return found

    def save_batch(self, results: list[PreparedMessage], cursor: int) -> list[ConsumerMessage]:
        messages = []
        with self.db.session.begin() as session:
            for result in results:
                chat = result.encrypted
                if session.get(EncryptedMessage, chat.msgid) is not None:
                    continue
                session.add(
                    EncryptedMessage(
                        msgid=chat.msgid,
                        seq=chat.seq,
                        raw_data=chat.model_dump(mode="json"),
                        decrypt_error=result.decrypt_error,
                    )
                )
                # 明确父表先插入，兼容开启外键检查的三个数据库。
                session.flush()
                message = result.message
                if message is not None:
                    session.add(
                        DecryptedMessage(
                            msgid=message.msgid,
                            msgtype=message.msgtype,
                            msgtime=message.msgtime,
                            raw_data=message.data,
                            parse_error=result.parse_error,
                        )
                    )
                    session.flush()
                    if result.type_fields is not None:
                        session.add(
                            TYPE_MODELS[message.msgtype](msgid=message.msgid, **result.type_fields)
                        )
                    messages.append(message)
            progress = session.get(Progress, 1)
            if progress is None:
                session.add(Progress(id=1, cursor=cursor))
            else:
                progress.cursor = max(progress.cursor, cursor)
        # 返回值只会在事务成功提交后交给分发器。
        return messages
