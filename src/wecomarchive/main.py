from __future__ import annotations

import signal
from collections.abc import Callable, Generator
from contextlib import contextmanager
from threading import Event, RLock, current_thread, main_thread
from time import sleep
from types import FrameType
from typing import Any

from pydantic import SecretStr

from .config import ServiceConfig, WeComArchiveConfig
from .db import Database
from .logging_setup import configure_logging
from .models import ConsumerMessage
from .sdk import ArchiveClient, FinanceClient
from .services import ConsumeService, DecryptService, ParseService, PullService, Service
from .storage import ArchiveStore


class WeComArchive:
    """第二版入口：构造初始化资源，start 阻塞直到 Ctrl+C 后完成退出。"""

    def __init__(
        self,
        corp_id: str,
        archive_secret: str | SecretStr,
        *,
        database_url: str = "sqlite:///./wecom_archive.db",
        proxy: str = "",
        timeout: int = 5,
        batch_size: int = 1000,
        consumption_window_seconds: float = 300,
        pull: ServiceConfig | None = None,
        decrypt: ServiceConfig | None = None,
        parse: ServiceConfig | None = None,
        consume: ServiceConfig | None = None,
        client: ArchiveClient | None = None,
    ) -> None:
        self.config = WeComArchiveConfig(
            corp_id=corp_id,
            archive_secret=archive_secret,
            database_url=database_url,
            proxy=proxy,
            api_timeout=timeout,
            batch_size=batch_size,
            consumption_window_seconds=consumption_window_seconds,
            pull=pull if pull is not None else ServiceConfig(),
            decrypt=decrypt if decrypt is not None else ServiceConfig(),
            parse=parse if parse is not None else ServiceConfig(),
            consume=consume if consume is not None else ServiceConfig(threads=4),
        )
        self._started = False
        self._closed = False
        self._running = False
        self._stop_event = Event()
        self._gate = RLock()
        self._client = client if client is not None else FinanceClient(self.config)
        self._db = None
        try:
            configure_logging()
            self._db = Database(database_url)
            self._db.acquire_instance()
            self._db.initialize()
            self._client.initialize()
            self._store = ArchiveStore(self._db)
            common = dict(stop_event=self._stop_event, gate=self._gate)
            self._pull_service = PullService(self.config.pull, self._store, self._client, **common)
            self._decrypt_service = DecryptService(
                self.config.decrypt, self._store, self._client, **common
            )
            self._parse_service = ParseService(self.config.parse, self._store, **common)
            self._consume_service = ConsumeService(
                self.config.consume,
                self._store,
                self.config.consumption_window_seconds,
                **common,
            )
            self._services: dict[str, Service[Any]] = {
                "拉取": self._pull_service,
                "解密": self._decrypt_service,
                "解析": self._parse_service,
                "消费": self._consume_service,
            }
        except BaseException:
            self.close()
            raise

    def _check_configurable(self) -> None:
        if self._started or self._closed:
            raise RuntimeError("请在启动前完成配置，关闭后不能再次使用")

    def set_private_key(self, pem: str | bytes, version: int = 1) -> None:
        self._check_configurable()
        self._client.set_private_key(pem, version)

    def add_consumer(
        self,
        name: str,
        match_text: str,
        is_regex: bool,
        callback: Callable[[ConsumerMessage], Any],
    ) -> None:
        self._check_configurable()
        self._consume_service.register(name, match_text, is_regex, callback)

    def close(self) -> None:
        """释放尚未运行的实例；运行期间资源由 start 统一释放。"""
        if self._running:
            raise RuntimeError("运行期间不能关闭资源，请使用 Ctrl+C")
        if self._closed:
            return
        self._closed = True
        try:
            self._client.close()
        finally:
            if self._db is not None:
                self._db.dispose()

    @contextmanager
    def _signals(self) -> Generator[Event]:
        previous = {}
        # 信号处理器只置请求标志；停止闸门在主循环获取，避免中断领取事务。
        requested = Event()

        def request_stop(_signum: int, _frame: FrameType | None) -> None:
            requested.set()

        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, request_stop)
            yield requested
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    def _shutdown(self) -> None:
        with self._gate:
            self._stop_event.set()
            total = sum(service.remaining for service in self._services.values())
        print("正在停止，等待剩余任务完成……", flush=True)
        while True:
            counts = {name: service.remaining for name, service in self._services.items()}
            remaining = sum(counts.values())
            print(
                "，".join(f"{name}：剩余 {count}" for name, count in counts.items())
                + f"；退出进度：{total - remaining}/{total}，剩余 {remaining}",
                flush=True,
            )
            if remaining == 0:
                break
            # 停止事件已置位，使用延时限制进度刷新频率。
            sleep(0.5)
        for service in self._services.values():
            service.join()
        self._running = False
        self.close()

    def start(self) -> None:
        self._check_configurable()
        if current_thread() is not main_thread():
            raise RuntimeError("主服务 start 必须在主线程运行以监听 Ctrl+C")
        self._started = True
        self._running = True
        try:
            with self._signals() as requested:
                try:
                    self._store.recover(self._client.key_versions())
                    for service in self._services.values():
                        service.start()
                    while not requested.wait(0.1):
                        if self._stop_event.is_set():
                            break
                finally:
                    self._shutdown()
        finally:
            if not self._closed:
                self._shutdown()
        for service in self._services.values():
            if service._fatal is not None:
                raise RuntimeError("子服务异常，主服务已退出") from service._fatal

    def __enter__(self) -> WeComArchive:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
