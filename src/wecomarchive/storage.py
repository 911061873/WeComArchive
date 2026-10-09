from __future__ import annotations

import time
from collections.abc import Collection, Sequence
from typing import Literal, overload

from sqlalchemy import select, update

from .db import ConsumerTask, Database, DecryptedMessage, EncryptedMessage, ParsedMessage, Progress
from .models import ConsumerMessage, EncryptedChat


class ArchiveStore:
    """单实例内数据库操作串行；任务领取先提交，耗时工作在事务外执行。"""

    def __init__(self, database: Database) -> None:
        self.db = database

    def cursor(self) -> int:
        with self.db.lock, self.db.session() as session:
            progress = session.get(Progress, 1)
            return progress.cursor if progress else 0

    def save_batch(self, chats: Sequence[EncryptedChat]) -> None:
        if not chats:
            return
        with self.db.lock, self.db.session.begin() as session:
            for chat in chats:
                if session.get(EncryptedMessage, chat.msgid) is None:
                    session.add(
                        EncryptedMessage(
                            msgid=chat.msgid,
                            seq=chat.seq,
                            publickey_ver=chat.publickey_ver,
                            raw_data=chat.model_dump(mode="json"),
                        )
                    )
                    session.flush()
            cursor = max(chat.seq for chat in chats)
            progress = session.get(Progress, 1)
            if progress is None:
                session.add(Progress(id=1, cursor=cursor))
            else:
                progress.cursor = max(progress.cursor, cursor)

    def recover(self, versions: Collection[int]) -> None:
        with self.db.lock, self.db.session.begin() as session:
            for model in (EncryptedMessage, DecryptedMessage, ConsumerTask):
                session.execute(
                    update(model).where(model.status == "processing").values(status="pending")
                )
            session.execute(
                update(EncryptedMessage)
                .where(
                    EncryptedMessage.status == "waiting_key",
                    EncryptedMessage.publickey_ver.in_(versions),
                )
                .values(status="pending", decrypt_error=None)
            )

    @overload
    def claim(self, stage: Literal["decrypt"]) -> EncryptedChat | None: ...

    @overload
    def claim(self, stage: Literal["parse"]) -> tuple[str, str] | None: ...

    def claim(
        self,
        stage: Literal["decrypt", "parse"],
    ) -> EncryptedChat | tuple[str, str] | None:
        model = EncryptedMessage if stage == "decrypt" else DecryptedMessage
        with self.db.lock, self.db.session.begin() as session:
            row = session.scalars(
                select(model).where(model.status == "pending").order_by(model.msgid).limit(1)
            ).first()
            if row is None:
                return None
            row.status = "processing"
            if stage == "decrypt":
                return EncryptedChat.model_validate(row.raw_data)
            return row.msgid, row.raw_text

    def decrypted(self, chat: EncryptedChat, raw_text: str) -> None:
        if not isinstance(raw_text, str):
            raise TypeError("解密结果必须为原始明文字符串")
        with self.db.lock, self.db.session.begin() as session:
            session.add(
                DecryptedMessage(msgid=chat.msgid, msgtype="", raw_data={}, raw_text=raw_text)
            )
            session.get(EncryptedMessage, chat.msgid).status = "success"

    def parsed(self, message: ConsumerMessage) -> None:
        with self.db.lock, self.db.session.begin() as session:
            row = session.get(DecryptedMessage, message.msgid)
            row.status = "success"
            row.msgtype = message.msgtype
            row.msgtime = message.msgtime
            row.raw_data = message.data
            session.add(
                ParsedMessage(
                    msgid=message.msgid,
                    msgtime=message.msgtime,
                    data=message.model_dump(mode="json"),
                )
            )

    def fail(
        self,
        stage: Literal["decrypt", "parse"],
        msgid: str,
        status: str,
        error: str | None,
    ) -> None:
        model = EncryptedMessage if stage == "decrypt" else DecryptedMessage
        field = "decrypt_error" if stage == "decrypt" else "parse_error"
        with self.db.lock, self.db.session.begin() as session:
            row = session.get(model, msgid)
            row.status = status
            setattr(row, field, error)

    def sequence(self, msgid: str) -> int:
        with self.db.lock, self.db.session() as session:
            return session.get(EncryptedMessage, msgid).seq

    def claim_consumer(
        self,
        names: Sequence[str],
        window: float,
    ) -> tuple[str, ConsumerMessage] | None:
        now = int(time.time() * 1000)
        with self.db.lock, self.db.session.begin() as session:
            for name in names:
                # 每次只领取一个调用；不预先生成窗口外的消费者任务。
                task_exists = (
                    select(ConsumerTask.msgid)
                    .where(
                        ConsumerTask.msgid == ParsedMessage.msgid,
                        ConsumerTask.consumer == name,
                        ConsumerTask.status != "pending",
                    )
                    .exists()
                )
                row = session.scalars(
                    select(ParsedMessage)
                    .where(
                        ParsedMessage.msgtime >= now - window * 1000,
                        ParsedMessage.msgtime <= now,
                        ~task_exists,
                    )
                    .order_by(ParsedMessage.msgtime, ParsedMessage.msgid)
                    .limit(1)
                ).first()
                if row is None:
                    continue
                task = session.get(ConsumerTask, (row.msgid, name))
                if task is None:
                    task = ConsumerTask(msgid=row.msgid, consumer=name)
                    session.add(task)
                task.status = "processing"
                return name, ConsumerMessage.model_validate(row.data)
        return None

    def finish_consumer(
        self,
        name: str,
        msgid: str,
        status: str,
        error: str | None = None,
    ) -> None:
        with self.db.lock, self.db.session.begin() as session:
            task = session.get(ConsumerTask, (msgid, name))
            task.status = status
            task.error = error
