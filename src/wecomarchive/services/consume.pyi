import re
from collections.abc import Callable as Callable
from dataclasses import dataclass
from typing import Any

from _typeshed import Incomplete

from ..config import ServiceConfig as ServiceConfig
from ..models import ConsumerMessage as ConsumerMessage
from ..storage import ArchiveStore as ArchiveStore
from .base import Service as Service

log: Incomplete

@dataclass(frozen=True)
class ConsumerRegistration:
    callback: Callable[[ConsumerMessage], Any]
    asynchronous: bool
    match_text: str
    pattern: re.Pattern[str] | None

class ConsumeService(Service[tuple[str, ConsumerMessage]]):
    registrations: dict[str, ConsumerRegistration]
    def __init__(
        self, config: ServiceConfig, store: ArchiveStore, window: float, **kwargs: Any
    ) -> None: ...
    def register(
        self, name: str, match_text: str, is_regex: bool, callback: Callable[[ConsumerMessage], Any]
    ) -> None: ...
    def acquire(self) -> tuple[str, ConsumerMessage] | None: ...
    def process(self, task: tuple[str, ConsumerMessage]) -> None: ...
