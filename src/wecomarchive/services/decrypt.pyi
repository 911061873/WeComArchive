from typing import Any

from _typeshed import Incomplete

from ..config import ServiceConfig as ServiceConfig
from ..models import EncryptedChat as EncryptedChat
from ..sdk import ArchiveClient as ArchiveClient
from ..sdk import MissingKeyError as MissingKeyError
from ..storage import ArchiveStore as ArchiveStore
from .base import Service as Service

log: Incomplete

class DecryptService(Service[EncryptedChat]):
    def __init__(
        self, config: ServiceConfig, store: ArchiveStore, client: ArchiveClient, **kwargs: Any
    ) -> None: ...
    def acquire(self) -> EncryptedChat | None: ...
    def process(self, task: EncryptedChat) -> None: ...
