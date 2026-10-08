from __future__ import annotations

from base64 import b64decode
from dataclasses import asdict, is_dataclass
from typing import Protocol

from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

from .config import ArchiveConfig
from .models import EncryptedChat


class ArchiveClient(Protocol):
    """同步 SDK 边界；框架会在专用线程调用，测试可注入替身。"""

    def fetch(self, cursor: int) -> list[EncryptedChat]: ...
    def decrypt(self, chat: EncryptedChat) -> dict: ...
    def close(self) -> None: ...


class FinanceClient:
    def __init__(self, config: ArchiveConfig):
        self.config = config
        self._sdk = None
        self._keys = {}

    def set_private_key(self, pem: str | bytes, version: int = 0):
        if type(version) is not int or version < 0:
            raise ValueError("密钥版本必须为非负整数")
        key = RSA.import_key(pem)
        if not key.has_private():
            raise ValueError("需要 RSA 私钥")
        self._keys[version] = key

    def _get_sdk(self):
        if self._sdk is None:
            from pyweworkfinance import WeWorkFinance

            self._sdk = WeWorkFinance(
                corpid=self.config.corp_id,
                secret=self.config.archive_secret.get_secret_value(),
                default_timeout=self.config.api_timeout,
            )
        return self._sdk

    def fetch(self, cursor: int) -> list[EncryptedChat]:
        response = self._get_sdk().get_chat_data(
            seq=cursor,
            limit=self.config.batch_size,
            timeout=self.config.api_timeout,
            proxy=self.config.proxy,
        )
        return [
            EncryptedChat.model_validate(asdict(chat) if is_dataclass(chat) else chat)
            for chat in response.chatdata
        ]

    def decrypt(self, chat: EncryptedChat) -> dict:
        key = self._keys.get(chat.publickey_ver, self._keys.get(0))
        if key is None:
            raise ValueError(f"未配置私钥版本 {chat.publickey_ver}")
        random_key = PKCS1_v1_5.new(key).decrypt(
            b64decode(chat.encrypt_random_key, validate=True), b""
        )
        if not random_key:
            raise ValueError("随机密钥解密失败")
        data = self._get_sdk().decrypt_data(random_key.decode("utf-8"), chat.encrypt_chat_msg)
        if not isinstance(data, dict):
            raise ValueError("解密结果必须为 JSON 对象")
        return data

    def close(self):
        if self._sdk is not None:
            self._sdk._destroy()
            self._sdk = None
