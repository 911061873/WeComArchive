import logging
from logging.handlers import RotatingFileHandler

class _ArchiveRotatingFileHandler(RotatingFileHandler):
    """标识框架创建的普通日志处理器。"""

class _ArchiveErrorFileHandler(logging.FileHandler):
    """标识框架创建的错误日志处理器。"""

class _BelowError(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool: ...

def configure_logging() -> None:
    """进程内只配置一次；普通日志轮转，错误日志无限追加。"""
