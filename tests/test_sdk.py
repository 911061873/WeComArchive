from base64 import b64encode
from types import SimpleNamespace

import pytest
from conftest import chat
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA
from pyweworkfinance.models import EncryptChatData

from wecomarchive import ArchiveConfig
from wecomarchive.sdk import FinanceClient


def test_sdk_parameters_dataclass_normalization_and_rsa():
    config = ArchiveConfig(
        corp_id="corp", archive_secret="secret", batch_size=12, api_timeout=7, proxy="proxy"
    )
    client = FinanceClient(config)
    key = RSA.generate(1024)
    random_key = "random-key"
    encrypted_key = b64encode(
        PKCS1_v1_5.new(key.public_key()).encrypt(random_key.encode())
    ).decode()
    calls = []

    class SDK:
        def get_chat_data(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                chatdata=[EncryptChatData(4, "id", 2, encrypted_key, "ciphertext")]
            )

        def decrypt_data(self, key, body):
            assert (key, body) == (random_key, "ciphertext")
            return {"action": "switch"}

        def _destroy(self):
            calls.append("closed")

    client._sdk = SDK()
    client.set_private_key(key.export_key(), version=0)
    messages = client.fetch(3)
    assert calls[0] == {"seq": 3, "limit": 12, "timeout": 7, "proxy": "proxy"}
    assert messages[0].seq == 4
    assert client.decrypt(messages[0]) == {"action": "switch"}
    client.close()
    client.close()
    assert calls[-1] == "closed"


def test_missing_private_key_and_public_key_are_rejected():
    client = FinanceClient(ArchiveConfig(corp_id="corp", archive_secret="secret"))
    with pytest.raises(ValueError, match="未配置私钥"):
        client.decrypt(chat())
    key = RSA.generate(1024)
    with pytest.raises(ValueError, match="私钥"):
        client.set_private_key(key.public_key().export_key())
    with pytest.raises(ValueError):
        client.set_private_key(key.export_key(), version=-1)
