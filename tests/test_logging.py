import logging
from logging.handlers import RotatingFileHandler

from wecomarchive.logging_setup import configure_logging


def test_persistent_logs_and_repeated_configuration(tmp_path):
    configure_logging()
    configure_logging()
    logger = logging.getLogger("wecomarchive.service")
    logger.info("普通消息")
    logger.warning("警告消息")
    logger.error("错误消息")
    logging.getLogger("pyweworkfinance").error("SDK 错误")
    directory = tmp_path / "wecomarchive"
    regular = (directory / "wecomarchive.log").read_text(encoding="utf-8")
    errors = (directory / "error.log").read_text(encoding="utf-8")
    assert regular.count("普通消息") == 1
    assert "警告消息" in regular
    assert "错误消息" not in regular
    assert errors.count("错误消息") == 1
    assert errors.count("SDK 错误") == 1
    assert "普通消息" not in errors


def test_rotation_preserves_unlimited_errors(tmp_path):
    configure_logging()
    logger = logging.getLogger("wecomarchive")
    regular = next(h for h in logger.handlers if isinstance(h, RotatingFileHandler))
    assert regular.maxBytes == 10 * 1024 * 1024
    assert regular.backupCount == 5
    regular.maxBytes = 200
    for index in range(30):
        logger.info("普通消息 %s %s", index, "x" * 80)
        logger.error("错误消息 %s", index)
    directory = tmp_path / "wecomarchive"
    assert len(list(directory.glob("wecomarchive.log*"))) == 6
    assert len(list(directory.glob("error.log*"))) == 1
    assert (directory / "error.log").read_text(encoding="utf-8").count("错误消息") == 30
