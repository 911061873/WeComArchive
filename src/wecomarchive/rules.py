from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .models import ConsumerMessage


class TextRule(BaseModel):
    """包含文本与正则二选一；发送者条件留给未来规则扩展。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    contains: str | None = Field(default=None, min_length=1)
    regex: str | None = Field(default=None, min_length=1)
    _pattern: re.Pattern[str] | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def validate_matcher(self):
        if (self.contains is None) == (self.regex is None):
            raise ValueError("contains 和 regex 必须且只能设置一个")
        if self.regex is not None:
            try:
                self._pattern = re.compile(self.regex)
            except re.error as exc:
                raise ValueError("无效正则表达式") from exc
        return self

    def matches(self, message: ConsumerMessage) -> bool:
        text = message.text
        if text is None:
            return False
        if self.contains is not None:
            return self.contains in text
        return self._pattern.search(text) is not None
