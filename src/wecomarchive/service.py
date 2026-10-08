from __future__ import annotations

import asyncio
import inspect
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import current_thread, main_thread

from .config import ArchiveConfig
from .db import Database
from .logging_setup import configure_logging
from .models import ConsumerMessage
from .rules import TextRule
from .sdk import ArchiveClient, FinanceClient
from .storage import ArchiveStore, PreparedMessage, prepare

log = logging.getLogger(__name__)
Consumer = Callable[[ConsumerMessage], Awaitable[None] | None]


class MessageArchiveService:
    """一个实例运行一次；一个数据库只运行一个采集实例。"""

    def __init__(self, config: ArchiveConfig, *, client: ArchiveClient | None = None):
        configure_logging()
        self.config = config
        self._client = client if client is not None else FinanceClient(config)
        self._db = Database(config.database_url)
        self._store = ArchiveStore(self._db)
        self._registrations: list[tuple[TextRule, Consumer]] = []
        self._queue: asyncio.Queue[tuple[Consumer, ConsumerMessage]] = asyncio.Queue(
            config.queue_capacity
        )
        self._stop = asyncio.Event()
        self._started = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._executor = None
        self._inflight = None
        self._closing = False

    def add_rule(self, rule: TextRule, consumer: Consumer) -> None:
        if self._started:
            raise RuntimeError("请在启动前注册规则")
        if not isinstance(rule, TextRule):
            raise TypeError("rule 必须为 TextRule")
        if not callable(consumer):
            raise TypeError("消费者必须是可调用函数")
        self._registrations.append((rule, consumer))

    def set_private_key(self, pem: str | bytes, version: int = 0) -> None:
        if self._started:
            raise RuntimeError("请在启动前设置私钥")
        if not isinstance(self._client, FinanceClient):
            raise RuntimeError("注入的 client 应自行管理密钥")
        self._client.set_private_key(pem, version)

    def stop(self) -> None:
        """请求停止，支持信号处理器或其他线程调用；等待 run() 返回完成关闭。"""
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop.set)
        else:
            self._stop.set()

    def _archive_batch(self) -> list[ConsumerMessage]:
        cursor = self._store.cursor()
        chats = self._client.fetch(cursor)
        if not chats:
            return []
        existing = self._store.existing_ids(chats)
        results = []
        for chat in chats:
            if chat.msgid in existing:
                continue
            existing.add(chat.msgid)
            try:
                data = self._client.decrypt(chat)
                if not isinstance(data, dict):
                    raise ValueError("解密结果必须是对象")
                results.append(prepare(chat, data))
            except Exception as exc:
                # 不记录正文或 SDK 异常文本，避免意外将密钥/明文写入日志。
                error = type(exc).__name__
                log.error("解密失败 msgid=%s error=%s", chat.msgid, error)
                results.append(PreparedMessage(chat, decrypt_error=error))
        return self._store.save_batch(results, max(cursor, max(chat.seq for chat in chats)))

    async def _blocking(self, callback):
        if self._loop is None:
            raise RuntimeError("服务尚未绑定事件循环")
        self._inflight = self._loop.run_in_executor(self._executor, callback)
        # asyncio 取消不能中断原生 SDK；保留 future，在释放资源前等调用结束。
        return await asyncio.shield(self._inflight)

    async def _dispatch(self, messages: list[ConsumerMessage]) -> None:
        for message in messages:
            window = self.config.consumption_window_seconds
            if window is not None:
                if message.msgtime is None:
                    continue
                age = time.time() - message.msgtime / 1000
                if age < 0 or age > window:
                    continue
            # 时间窗口只检查一次；多个规则的入队等待不改变本消息的资格。
            for rule, callback in self._registrations:
                if rule.matches(message):
                    await self._queue.put((callback, message.model_copy(deep=True)))

    async def _poll(self) -> None:
        while not self._stop.is_set():
            try:
                messages = await self._blocking(self._archive_batch)
            except Exception as exc:
                log.error("采集或存储失败，本轮结束，等待下次轮询 error=%s", type(exc).__name__)
            else:
                await self._dispatch(messages)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.config.poll_interval)
            except asyncio.TimeoutError:
                pass

    async def _consume(self) -> None:
        while True:
            callback, message = await self._queue.get()
            try:
                if inspect.iscoroutinefunction(callback):
                    result = callback(message)
                else:
                    result = await asyncio.to_thread(callback, message)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                # 消费者自行抛 CancelledError 也不能意外杀死工作任务。
                if self._closing:
                    raise
                log.warning("消费者取消当前消息 msgid=%s", message.msgid)
            except Exception as exc:
                log.error("消费失败 msgid=%s error=%s", message.msgid, type(exc).__name__)
            finally:
                self._queue.task_done()

    async def _drain(self, producer):
        await producer
        await self._queue.join()

    @contextmanager
    def _handle_signals(self, enabled: bool):
        # signal.signal 只允许主线程使用，也兼容 Windows 的事件循环。
        if not enabled or current_thread() is not main_thread():
            yield
            return
        previous = {}
        requested = False

        def request_stop(_signum, _frame):
            nonlocal requested
            if not requested:
                requested = True
                self.stop()

        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, request_stop)
            yield
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    async def run(self, *, handle_signals: bool = True) -> None:
        """运行至 stop()/Ctrl+C；嵌入其他应用时可关闭进程信号接管。"""
        with self._handle_signals(handle_signals):
            await self._run()

    async def _run(self) -> None:
        if self._started:
            raise RuntimeError("服务实例不能重复运行")
        self._started = True
        self._loop = asyncio.get_running_loop()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wecomarchive-sdk")
        workers = []
        producer = None
        stopper = None
        try:
            await self._blocking(self._db.initialize)
            workers = [
                asyncio.create_task(self._consume(), name=f"archive-consumer-{i}")
                for i in range(self.config.consumer_workers)
            ]
            producer = asyncio.create_task(self._poll(), name="archive-poll")
            stopper = asyncio.create_task(self._stop.wait())
            done, _ = await asyncio.wait({producer, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if producer in done:
                await producer
        finally:
            self._stop.set()
            if stopper is not None:
                stopper.cancel()
                await asyncio.gather(stopper, return_exceptions=True)
            try:
                if producer is not None:
                    await asyncio.wait_for(
                        self._drain(producer), timeout=self.config.shutdown_timeout
                    )
            except asyncio.TimeoutError:
                log.warning("关闭等待超时，放弃未完成消费，不在重启后恢复")
            finally:
                if producer is not None:
                    producer.cancel()
                self._closing = True
                for worker in workers:
                    worker.cancel()
                await asyncio.gather(
                    *workers, *([producer] if producer else []), return_exceptions=True
                )
                # 正在执行的 SDK/数据库线程不能安全强杀；必须先结束再销毁资源。
                if self._inflight is not None:
                    await asyncio.gather(self._inflight, return_exceptions=True)
                try:
                    await self._blocking(self._client.close)
                finally:
                    await self._blocking(self._db.dispose)
                    self._executor.shutdown(wait=True)
                    while not self._queue.empty():
                        self._queue.get_nowait()
                        self._queue.task_done()
