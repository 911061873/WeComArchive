from datetime import datetime

from _typeshed import Incomplete
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped as Mapped

MESSAGE_ID: Incomplete

class Base(DeclarativeBase): ...

class Progress(Base):
    __tablename__: str
    id: Mapped[int]
    cursor: Mapped[int]

class EncryptedMessage(Base):
    __tablename__: str
    msgid: Mapped[str]
    seq: Mapped[int]
    raw_data: Mapped[dict]
    publickey_ver: Mapped[int]
    status: Mapped[str]
    decrypt_error: Mapped[str | None]
    created_at: Mapped[datetime]

class DecryptedMessage(Base):
    __tablename__: str
    msgid: Mapped[str]
    msgtype: Mapped[str]
    msgtime: Mapped[int | None]
    raw_data: Mapped[dict]
    raw_text: Mapped[str]
    status: Mapped[str]
    parse_error: Mapped[str | None]

class ParsedMessage(Base):
    __tablename__: str
    msgid: Mapped[str]
    msgtime: Mapped[int]
    data: Mapped[dict]

class ConsumerTask(Base):
    __tablename__: str
    msgid: Mapped[str]
    consumer: Mapped[str]
    status: Mapped[str]
    error: Mapped[str | None]
