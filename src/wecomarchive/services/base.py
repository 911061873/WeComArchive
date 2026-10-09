from __future__ import annotations

import logging
from _thread import RLock
from abc import ABC, abstractmethod
from threading import Event, Thread
from typing import Generic, TypeVar

from ..config import ServiceConfig

log = logging.getLogger(__name__)


TaskT = TypeVar("TaskT")


class Service(ABC, Generic[TaskT]):
    """领取与停止共用闸门，停止后的进度快照不再增加。"""

    def __init__(
        self,
        config: ServiceConfig,
        stop_event: Event | None = None,
        gate: RLock | None = None,
    ) -> None:
        self.config = config
        self._stop = stop_event if stop_event is not None else Event()
        self._gate = gate if gate is not None else RLock()
        self._threads: list[Thread] = []
        self._started = False
        self._active = 0
        self._fatal: BaseException | None = None

    @property
    def remaining(self) -> int:
        with self._gate:
            return self._active

    def is_stopping(self) -> bool:
        return self._stop.is_set()

    def wait(self) -> None:
        self._stop.wait(self.config.poll_interval)

    def start(self) -> None:
        with self._gate:
            if self._started:
                raise RuntimeError("子服务不能重复启动")
            self._started = True
            for index in range(self.config.threads):
                thread = Thread(target=self._run, name=f"archive-{type(self).__name__}-{index}")
                thread.start()
                self._threads.append(thread)

    def stop(self) -> None:
        with self._gate:
            self._stop.set()

    def join(self) -> None:
        for thread in self._threads:
            while thread.is_alive():
                thread.join(timeout=0.1)

    def _run(self) -> None:
        try:
            while True:
                with self._gate:
                    if self.is_stopping():
                        return
                    task = self.acquire()
                    if task is not None:
                        self._active += 1
                if task is None:
                    self.wait()
                    continue
                try:
                    self.process(task)
                finally:
                    with self._gate:
                        self._active -= 1
                self.after_task()
        except BaseException as exc:
            # 数据库状态写入失败时停止实例，保留处理中状态供重启恢复。
            self._fatal = exc
            log.error("子服务异常退出 service=%s error=%s", type(self).__name__, type(exc).__name__)
            self.stop()

    def after_task(self) -> None:
        pass

    @abstractmethod
    def acquire(self) -> TaskT | None: ...

    @abstractmethod
    def process(self, task: TaskT) -> None: ...
