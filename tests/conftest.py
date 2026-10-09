import json
import logging
import os
from pathlib import Path

import pytest
from sqlalchemy import MetaData, inspect

from wecomarchive.db import Database
from wecomarchive.logging_setup import _ArchiveErrorFileHandler, _ArchiveRotatingFileHandler
from wecomarchive.models import EncryptedChat


@pytest.fixture(autouse=True)
def isolated_log_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    loggers = [logging.getLogger(name) for name in ("wecomarchive", "pyweworkfinance")]
    levels = [logger.level for logger in loggers]
    yield
    for logger, level in zip(loggers, levels):
        for handler in logger.handlers[:]:
            if isinstance(handler, (_ArchiveRotatingFileHandler, _ArchiveErrorFileHandler)):
                logger.removeHandler(handler)
                handler.close()
        logger.setLevel(level)


@pytest.fixture(scope="session")
def external_database_url():
    url = os.environ.get("WECOM_TEST_DATABASE_URL")
    if url:
        db = Database(url)
        try:
            if inspect(db.engine).get_table_names():
                pytest.fail("WECOM_TEST_DATABASE_URL 必须指向独立空测试库；拒绝删除已有数据")
        finally:
            db.dispose()
    return url


@pytest.fixture
def db(tmp_path, external_database_url):
    database = Database(external_database_url or f"sqlite:///{tmp_path / 'archive.db'}")
    database.initialize()
    yield database
    metadata = MetaData()
    metadata.reflect(bind=database.engine)
    metadata.drop_all(database.engine)
    database.dispose()


def chat(seq=1, msgid=None, **extra):
    return EncryptedChat(
        seq=seq,
        msgid=msgid or f"m{seq}",
        publickey_ver=1,
        encrypt_random_key="encrypted-key",
        encrypt_chat_msg="encrypted-body",
        **extra,
    )


def plaintext(timestamp=None, **changes):
    import time

    data = {
        "msgtype": "text",
        "msgtime": timestamp if timestamp is not None else int(time.time() * 1000),
        "text": {"content": "订单 123 你好 🌏"},
        "from": "sender",
        "tolist": ["receiver"],
    }
    data.update(changes)
    return data


class FakeClient:
    def __init__(self, batches=None, data=None):
        self.batches = list(batches or [])
        self.data = data or {}
        self.cursors = []
        self.decrypted = []
        self.closed = False

    def fetch(self, cursor):
        self.cursors.append(cursor)
        item = self.batches.pop(0) if self.batches else []
        if isinstance(item, Exception):
            raise item
        return item

    def initialize(self):
        pass

    def set_private_key(self, pem, version):
        pass

    def key_versions(self):
        return {1}

    def decrypt(self, message):
        self.decrypted.append(message.msgid)
        value = self.data.get(message.msgid, plaintext())
        if isinstance(value, Exception):
            raise value
        if isinstance(value, str):
            return value
        return json.dumps({"msgid": message.msgid, **value}, ensure_ascii=False)

    def close(self):
        self.closed = True
