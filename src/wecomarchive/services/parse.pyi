from typing import Any

from _typeshed import Incomplete

from ..config import ServiceConfig as ServiceConfig
from ..models import SUPPORTED_MESSAGE_TYPES as SUPPORTED_MESSAGE_TYPES
from ..models import ConsumerMessage as ConsumerMessage
from ..storage import ArchiveStore as ArchiveStore
from .base import Service as Service

log: Incomplete

class ParseService(Service[tuple[str, str]]):
    store: Incomplete
    def __init__(self, config: ServiceConfig, store: ArchiveStore, **kwargs: Any) -> None: ...
    def acquire(self) -> tuple[str, str] | None: ...
    def process(self, task: tuple[str, str]) -> None: ...
