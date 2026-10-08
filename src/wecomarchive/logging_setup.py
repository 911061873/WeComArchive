"""服务日志持久化，保留调用方已有的控制台日志配置。"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock

_lock = Lock()


class _ArchiveRotatingFileHandler(RotatingFileHandler):
    """标识框架创建的普通日志处理器。"""


class _ArchiveErrorFileHandler(logging.FileHandler):
    """标识框架创建的错误日志处理器。"""


class _BelowError(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.ERROR


def configure_logging() -> None:
    """进程内只配置一次；普通日志轮转，错误日志无限追加。"""
    with _lock:
        logger = logging.getLogger("wecomarchive")
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
            errors = _ArchiveErrorFileHandler(directory / "error.log", encoding="utf-8")
        except Exception:
            regular.close()
            raise
        regular.setLevel(logging.INFO)
        regular.addFilter(_BelowError())
        errors.setLevel(logging.ERROR)
        for handler in (regular, errors):
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logging.getLogger("pyweworkfinance").addHandler(handler)
        logger.setLevel(logging.INFO)
        logging.getLogger("pyweworkfinance").setLevel(logging.WARNING)
