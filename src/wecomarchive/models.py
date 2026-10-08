from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EncryptedChat(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, strict=True, from_attributes=True)
    seq: int = Field(ge=0, lt=2**63)
    msgid: str = Field(min_length=1, max_length=256)
    publickey_ver: int
    encrypt_random_key: str
    encrypt_chat_msg: str


class ConsumerMessage(BaseModel):
    """每次规则命中的独立快照；data 是完整明文，msgtime 为毫秒时间戳。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    msgid: str
    seq: int
    msgtype: str
    msgtime: int | None
    data: dict[str, Any]

    @property
    def text(self) -> str | None:
        section = self.data.get("text")
        if self.msgtype == "text" and isinstance(section, dict):
            content = section.get("content")
            if isinstance(content, str):
                return content
        return None
