from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 消息 ID 区分大小写；不能沿用 MySQL 常见的大小写不敏感排序规则。
MESSAGE_ID = String(256).with_variant(String(256, collation="utf8mb4_bin"), "mysql")


class Base(DeclarativeBase):
    pass


class Progress(Base):
    __tablename__ = "archive_progress"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cursor: Mapped[int] = mapped_column(BigInteger, nullable=False)


class EncryptedMessage(Base):
    __tablename__ = "archive_encrypted"
    msgid: Mapped[str] = mapped_column(MESSAGE_ID, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    publickey_ver: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    decrypt_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class DecryptedMessage(Base):
    __tablename__ = "archive_decrypted"
    msgid: Mapped[str] = mapped_column(
        MESSAGE_ID, ForeignKey("archive_encrypted.msgid"), primary_key=True
    )
    msgtype: Mapped[str] = mapped_column(Text, nullable=False)
    msgtime: Mapped[int | None] = mapped_column(BigInteger)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    parse_error: Mapped[str | None] = mapped_column(Text)


class ParsedMessage(Base):
    __tablename__ = "archive_parsed"
    msgid: Mapped[str] = mapped_column(
        MESSAGE_ID, ForeignKey("archive_decrypted.msgid"), primary_key=True
    )
    msgtime: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)


class ConsumerTask(Base):
    __tablename__ = "archive_consumer_task"
    msgid: Mapped[str] = mapped_column(
        MESSAGE_ID, ForeignKey("archive_parsed.msgid"), primary_key=True
    )
    consumer: Mapped[str] = mapped_column(MESSAGE_ID, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text)
