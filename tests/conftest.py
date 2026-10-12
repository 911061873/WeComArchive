import json
import logging
import os
from base64 import b64encode
from pathlib import Path
from types import SimpleNamespace

import pytest
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA
from sqlalchemy import MetaData, inspect

from wecomarchive.db import Database
from wecomarchive.logging_setup import (
    _ArchiveConsoleHandler,
    _ArchiveErrorFileHandler,
    _ArchiveRotatingFileHandler,
)
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
            if isinstance(
                handler,
                (_ArchiveRotatingFileHandler, _ArchiveErrorFileHandler, _ArchiveConsoleHandler),
            ):
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


TEST_KEY = RSA.generate(1024)


def chat(seq=1, msgid=None, **extra):
    return EncryptedChat(
        seq=seq,
        msgid=msgid or f"m{seq}",
        publickey_ver=1,
        encrypt_random_key=b64encode(
            PKCS1_v1_5.new(TEST_KEY.public_key()).encrypt(b"random-key")
        ).decode(),
        encrypt_chat_msg=msgid or f"m{seq}",
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


class FakeSDK:
    def __init__(self, batches=None, data=None):
        self.batches = list(batches or [])
        self.data = data or {}
        self.cursors = []
        self.decrypted = []
        self.closed = False
        self.freed = []
        self._lib = self

    def get_chat_data(self, seq, **kwargs):
        cursor = seq
        self.cursors.append(cursor)
        item = self.batches.pop(0) if self.batches else []
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(chatdata=item)

    def initialize(self):
        pass

    def NewSlice(self):
        return SimpleNamespace(content=b"")

    def DecryptData(self, random_key, body, buffer):
        msgid = body.decode()
        self.decrypted.append(msgid)
        value = self.data.get(msgid, plaintext())
        if isinstance(value, Exception):
            raise value
        if not isinstance(value, str):
            value = json.dumps({"msgid": msgid, **value}, ensure_ascii=False)
        buffer.content = value.encode()
        return 0

    def GetContentFromSlice(self, buffer):
        return buffer.content

    def FreeSlice(self, buffer):
        self.freed.append(buffer)

    def _destroy(self):
        self.closed = True


@pytest.fixture
def archive_factory(monkeypatch):
    from wecomarchive import WeComArchive

    def create(*args, sdk=None, **kwargs):
        configs = {
            name: kwargs.pop(name)
            for name in ("pull", "decrypt", "parse", "consume")
            if name in kwargs
        }

        def make_sdk():
            instance = sdk if sdk is not None else FakeSDK()
            instance.initialize()
            return instance

        monkeypatch.setattr(
            "wecomarchive.main.WeWorkFinance",
            lambda **_: make_sdk(),
        )
        archive = WeComArchive(*args, **kwargs)
        archive.set_private_key(TEST_KEY.export_key(), 1)
        for name, config in configs.items():
            getattr(archive, f"_{name}_service").config = config
        return archive

    return create
