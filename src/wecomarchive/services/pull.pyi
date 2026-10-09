from typing import Any

from _typeshed import Incomplete

from ..config import ServiceConfig as ServiceConfig
from ..sdk import ArchiveClient as ArchiveClient
from ..storage import ArchiveStore as ArchiveStore
from .base import Service as Service

log: Incomplete

class PullService(Service[int]):
    def __init__(
        self, config: ServiceConfig, store: ArchiveStore, client: ArchiveClient, **kwargs: Any
    ) -> None: ...
    def acquire(self) -> int: ...
    def process(self, task: int) -> None: ...
    def after_task(self) -> None: ...
