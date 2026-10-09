from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

class EncryptedChat(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, strict=True, from_attributes=True)
    seq: int = Field(ge=0, lt=2**63)
    msgid: str = Field(min_length=1, max_length=256)
    publickey_ver: int = Field(ge=0, strict=True)
    encrypt_random_key: str
    encrypt_chat_msg: str

class TextContent(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, strict=True)
    content: str

class TextMessage(BaseModel):
    """文本明文模型；保留 SDK 未知字段，发送时间为毫秒。"""

    model_config = ConfigDict(extra="allow", frozen=True, strict=True, populate_by_name=True)
    msgid: str = Field(min_length=1, max_length=256)
    msgtype: Literal["text"]
    msgtime: int = Field(ge=0, lt=2**63)
    sender: str = Field(alias="from")
    tolist: list[str]
    roomid: str = ""
    action: str = "send"
    text: TextContent

SUPPORTED_MESSAGE_TYPES: dict[str, type[TextMessage]]

class ConsumerMessage(BaseModel):
    """统一消费对象；每个消费者收到独立快照。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    msgid: str
    seq: int
    msgtype: Literal["text"]
    msgtime: int
    sender: str
    tolist: list[str]
    roomid: str
    action: str
    data: dict[str, Any]

    @property
    def text(self) -> str: ...
