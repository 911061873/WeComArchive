from collections.abc import Callable as Callable
from typing import Any

from pydantic import SecretStr as SecretStr

from .config import ServiceConfig as ServiceConfig
from .config import WeComArchiveConfig as WeComArchiveConfig
from .db import Database as Database
from .logging_setup import configure_logging as configure_logging
from .models import ConsumerMessage as ConsumerMessage
from .sdk import ArchiveClient as ArchiveClient
from .sdk import FinanceClient as FinanceClient
from .services import ConsumeService as ConsumeService
from .services import DecryptService as DecryptService
from .services import ParseService as ParseService
from .services import PullService as PullService
from .services import Service as Service
from .storage import ArchiveStore as ArchiveStore

class WeComArchive:
    """第二版入口：构造初始化资源，start 阻塞直到 Ctrl+C 后完成退出。"""

    config: WeComArchiveConfig
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
    ) -> None: ...
    def set_private_key(self, pem: str | bytes, version: int = 1) -> None: ...
    def add_consumer(
        self, name: str, match_text: str, is_regex: bool, callback: Callable[[ConsumerMessage], Any]
    ) -> None: ...
    def close(self) -> None:
        """释放尚未运行的实例；运行期间资源由 start 统一释放。"""
    def start(self) -> None: ...
    def __enter__(self) -> WeComArchive: ...
    def __exit__(self, *_args: object) -> None: ...
