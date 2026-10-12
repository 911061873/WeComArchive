import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

_lock = threading.RLock()

LOGGER_NAME = "wecomarchive"
SERVICE_LOGGER_PREFIX = f"{LOGGER_NAME}.services"

SERVICE_NAMES = frozenset(
    {
        "pull",
        "decrypt",
        "process",
        "consume",
    }
)


class _ArchiveRotatingFileHandler(RotatingFileHandler):
    """标识框架创建的普通日志处理器。"""


class _ArchiveErrorFileHandler(logging.FileHandler):
    """标识框架创建的错误日志处理器。"""


class _ArchiveConsoleHandler(logging.StreamHandler):
    """标识框架创建的控制台日志处理器。"""


class _BelowError(logging.Filter):
    """只允许 ERROR 以下级别的日志通过。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.ERROR


def configure_logging() -> None:
    """进程内只配置一次；日志输出到控制台，并按级别写入文件。"""

    with _lock:
        logger = logging.getLogger(LOGGER_NAME)

        if any(isinstance(handler, _ArchiveRotatingFileHandler) for handler in logger.handlers):
            return

        directory = Path.home() / "wecomarchive"
        directory.mkdir(parents=True, exist_ok=True)

        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

        regular = _ArchiveRotatingFileHandler(
            directory / "wecomarchive.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )

        try:
            errors = _ArchiveErrorFileHandler(
                directory / "error.log",
                encoding="utf-8",
            )
        except Exception:
            regular.close()
            raise

        # 普通日志：DEBUG ~ WARNING
        regular.setLevel(logging.DEBUG)
        regular.addFilter(_BelowError())

        # 错误日志：ERROR 及以上
        errors.setLevel(logging.ERROR)

        console = _ArchiveConsoleHandler()
        console.setLevel(logging.DEBUG)

        for handler in (regular, errors, console):
            handler.setFormatter(formatter)

        # 主 Logger
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(regular)
        logger.addHandler(errors)
        logger.addHandler(console)

        # 第三方库 Logger
        finance_logger = logging.getLogger("pyweworkfinance")
        finance_logger.setLevel(logging.WARNING)
        finance_logger.propagate = False
        finance_logger.addHandler(regular)
        finance_logger.addHandler(errors)
        finance_logger.addHandler(console)


def get_service_logger(service_name: str) -> logging.Logger:
    """获取指定服务的 Logger。"""
    if service_name not in SERVICE_NAMES:
        raise ValueError(f"未知服务: {service_name}")
    logger = logging.getLogger(f"{SERVICE_LOGGER_PREFIX}.{service_name}")
    print(logger.name)
    return logger


def set_service_log_level(
    service_name: str,
    level: int | None = None,
) -> None:
    """
    设置指定服务的日志级别。

    level=None 时恢复继承主 Logger 的级别。
    """

    with _lock:
        logger = get_service_logger(service_name)

        logger.setLevel(logging.NOTSET if level is None else level)
