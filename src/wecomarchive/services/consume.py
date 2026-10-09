from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config import ServiceConfig
from ..models import ConsumerMessage
from ..storage import ArchiveStore
from .base import Service

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConsumerRegistration:
    callback: Callable[[ConsumerMessage], Any]
    asynchronous: bool
    match_text: str
    pattern: re.Pattern[str] | None


class ConsumeService(Service[tuple[str, ConsumerMessage]]):
    def __init__(
        self,
        config: ServiceConfig,
        store: ArchiveStore,
        window: float,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self.store, self.window = store, window
        self.registrations: dict[str, ConsumerRegistration] = {}
        self._next_consumer = 0

    def register(
        self,
        name: str,
        match_text: str,
        is_regex: bool,
        callback: Callable[[ConsumerMessage], Any],
    ) -> None:
        if self._started:
            raise RuntimeError("请在启动前注册消费者")
        if not isinstance(name, str) or not name.strip() or len(name) > 256:
            raise ValueError("消费者名称必须为 1 到 256 个字符的非空字符串")
        if name in self.registrations:
            raise ValueError("消费者名称重复")
        if not isinstance(match_text, str):
            raise TypeError("内容匹配文本必须为字符串")
        if type(is_regex) is not bool:
            raise TypeError("是否正则必须为布尔值")
        try:
            pattern = re.compile(match_text) if is_regex else None
        except re.error as exc:
            raise ValueError("内容匹配正则表达式无效") from exc
        if not callable(callback):
            raise TypeError("消费者必须可调用")
        asynchronous = inspect.iscoroutinefunction(callback) or inspect.iscoroutinefunction(
            getattr(callback, "__call__", None)
        )
        self.registrations[name] = ConsumerRegistration(callback, asynchronous, match_text, pattern)

    def acquire(self) -> tuple[str, ConsumerMessage] | None:
        names = list(self.registrations)
        if not names:
            return None
        offset = self._next_consumer % len(names)
        task = self.store.claim_consumer(names[offset:] + names[:offset], self.window)
        if task is not None:
            self._next_consumer = (names.index(task[0]) + 1) % len(names)
        return task

    def process(self, task: tuple[str, ConsumerMessage]) -> None:
        name, message = task
        # 执行业务前再次检查窗口；过期调用持久化跳过。
        age = time.time() - message.msgtime / 1000
        if age < 0 or age > self.window:
            self.store.finish_consumer(name, message.msgid, "expired")
            return
        registration = self.registrations[name]
        matched = (
            registration.pattern.search(message.text) is not None
            if registration.pattern is not None
            else registration.match_text in message.text
        )
        if not matched:
            self.store.finish_consumer(name, message.msgid, "skipped")
            return
        try:
            snapshot = message.model_copy(deep=True)
            if registration.asynchronous:
                asyncio.run(registration.callback(snapshot))
            else:
                registration.callback(snapshot)
        except BaseException as exc:
            error = type(exc).__name__
            self.store.finish_consumer(name, message.msgid, "failed", error)
            log.error("消费失败 msgid=%s consumer=%s error=%s", message.msgid, name, error)
        else:
            self.store.finish_consumer(name, message.msgid, "success")
