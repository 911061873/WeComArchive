from base64 import b64encode
from types import SimpleNamespace

import pytest
from conftest import TEST_KEY, FakeSDK, chat
from Crypto.Cipher import PKCS1_v1_5
from pyweworkfinance.models import EncryptChatData

from wecomarchive import WeComArchive
from wecomarchive.db import DecryptedMessage, EncryptedMessage


def test_sdk_parameters_raw_plaintext_and_shared_instance(tmp_path, monkeypatch):
    calls = []
    raw = ' { "msgtype": "text" } '
    encrypted = b64encode(PKCS1_v1_5.new(TEST_KEY.public_key()).encrypt(b"random-key")).decode()

    class SDK(FakeSDK):
        def get_chat_data(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(chatdata=[EncryptChatData(4, "id", 2, encrypted, "id")])

        def _destroy(self):
            calls.append("关闭 SDK")
            super()._destroy()

    sdk = SDK(data={"id": raw})

    def create(**kwargs):
        calls.append(kwargs)
        return sdk

    monkeypatch.setattr("wecomarchive.main.WeWorkFinance", create)
    app = WeComArchive(
        "企业",
        "密钥",
        database_url=f"sqlite:///{tmp_path / 'sdk.db'}",
        batch_size=12,
        timeout=7,
        proxy="proxy",
    )
    try:
        assert calls[0] == {"corpid": "企业", "secret": "密钥", "default_timeout": 7}
        assert all(service.sdk is sdk for service in app._services.values())
        assert all(service._sdk_lock is app._sdk_lock for service in app._services.values())
        app.set_private_key(TEST_KEY.export_key(), 2)
        monkeypatch.setattr(app._pull_service.store, "cursor", lambda: 3)
        monkeypatch.setattr(app._pull_service, "wait", lambda: None)
        app._pull_service.run_once()
        assert calls[1] == {"seq": 3, "limit": 12, "timeout": 7, "proxy": "proxy"}
        app._decrypt_service.process(app._decrypt_service.acquire())
        with app._db.session_factory() as session:
            assert session.get(DecryptedMessage, "id").raw_text == raw
        assert len(sdk.freed) == 1
    finally:
        app.close()
    app.close()
    assert calls.count("关闭 SDK") == 1


def test_keys_match_exact_version_without_fallback(tmp_path, archive_factory):
    app = archive_factory("企业", "密钥", database_url=f"sqlite:///{tmp_path / 'keys.db'}")
    try:
        app._keys.clear()
        app.set_private_key(TEST_KEY.export_key(), 0)
        assert set(app._keys) == {0}
        app._store.save_batch([chat()])
        app._decrypt_service.process(app._decrypt_service.acquire())
        with app._db.session_factory() as session:
            assert session.get(EncryptedMessage, "m1").status == "waiting_key"
        with pytest.raises(ValueError, match="私钥"):
            app.set_private_key(TEST_KEY.public_key().export_key(), 1)
        with pytest.raises(ValueError):
            app.set_private_key(TEST_KEY.export_key(), -1)
    finally:
        app.close()


@pytest.mark.parametrize("failure", ["allocate", "decrypt", "read"])
def test_decrypt_native_failure_releases_buffer(tmp_path, archive_factory, failure):
    sdk = FakeSDK()
    if failure == "allocate":
        sdk.NewSlice = lambda: None
    elif failure == "decrypt":
        sdk.DecryptData = lambda *_: 1
    else:

        def broken(_):
            raise UnicodeError("模拟明文读取失败")

        sdk.GetContentFromSlice = broken
    app = archive_factory(
        "企业", "密钥", database_url=f"sqlite:///{tmp_path / 'failure.db'}", sdk=sdk
    )
    try:
        app._store.save_batch([chat()])
        app._decrypt_service.process(app._decrypt_service.acquire())
        with app._db.session_factory() as session:
            assert session.get(EncryptedMessage, "m1").status == "failed"
        assert len(sdk.freed) == (0 if failure == "allocate" else 1)
    finally:
        app.close()
