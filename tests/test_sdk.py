from base64 import b64encode
from types import SimpleNamespace

import pytest
from conftest import chat
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA
from pyweworkfinance.models import EncryptChatData

from wecomarchive import WeComArchiveConfig
from wecomarchive.sdk import FinanceClient, MissingKeyError


def test_sdk_parameters_raw_plaintext_and_rsa():
    client = FinanceClient(
        WeComArchiveConfig(
            corp_id="企业", archive_secret="密钥", batch_size=12, api_timeout=7, proxy="proxy"
        )
    )
    key = RSA.generate(1024)
    encrypted = b64encode(PKCS1_v1_5.new(key.public_key()).encrypt(b"random-key")).decode()
    calls = []
    raw = ' { "msgtype": "text" } '

    class Lib:
        def NewSlice(self):
            return 1

        def DecryptData(self, random_key, body, buffer):
            assert (random_key, body, buffer) == (b"random-key", b"ciphertext", 1)
            return 0

        def GetContentFromSlice(self, buffer):
            return raw.encode()

        def FreeSlice(self, buffer):
            calls.append("释放明文缓冲区")

    class SDK:
        _lib = Lib()

        def get_chat_data(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(chatdata=[EncryptChatData(4, "id", 2, encrypted, "ciphertext")])

        def _destroy(self):
            calls.append("关闭 SDK")

    client._sdk = SDK()
    client.set_private_key(key.export_key(), 2)
    client.initialize()
    message = client.fetch(3)[0]
    assert calls[0] == {"seq": 3, "limit": 12, "timeout": 7, "proxy": "proxy"}
    assert client.decrypt(message) == raw
    assert "释放明文缓冲区" in calls
    client.close()
    client.close()
    assert calls.count("关闭 SDK") == 1


def test_keys_match_exact_version_without_fallback():
    client = FinanceClient(WeComArchiveConfig(corp_id="企业", archive_secret="密钥"))
    key = RSA.generate(1024)
    client.set_private_key(key.export_key(), 0)
    assert client.key_versions() == {0}
    with pytest.raises(MissingKeyError):
        client.decrypt(chat())
    with pytest.raises(ValueError, match="私钥"):
        client.set_private_key(key.public_key().export_key(), 1)
    with pytest.raises(ValueError):
        client.set_private_key(key.export_key(), -1)
