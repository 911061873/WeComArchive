from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..parser import MEDIA_TYPES, TYPE_SECTIONS

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
    parse_error: Mapped[str | None] = mapped_column(Text)


# 每种类型独立表；payload 保存完整类型字段，文本和媒体另提取常用列。
TYPE_MODELS = {}
for _name in TYPE_SECTIONS:
    _attrs = {
        "__tablename__": f"archive_type_{_name}",
        "msgid": mapped_column(MESSAGE_ID, ForeignKey("archive_decrypted.msgid"), primary_key=True),
        "payload": mapped_column(JSON, nullable=False),
    }
    if _name == "text":
        _attrs["content"] = mapped_column(Text, nullable=False)
    if _name in MEDIA_TYPES:
        _attrs.update(
            sdkfileid=mapped_column(Text, nullable=False),
            filename=mapped_column(Text),
            md5sum=mapped_column(Text),
            filesize=mapped_column(BigInteger),
        )
    TYPE_MODELS[_name] = type(f"{_name.title().replace('_', '')}Message", (Base,), _attrs)
