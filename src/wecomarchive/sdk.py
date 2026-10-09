from __future__ import annotations

from base64 import b64decode
from dataclasses import asdict, is_dataclass
from threading import RLock
from typing import TYPE_CHECKING, Protocol

from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

from .config import WeComArchiveConfig
from .models import EncryptedChat

if TYPE_CHECKING:
    from pyweworkfinance import WeWorkFinance


class MissingKeyError(ValueError):
    """消息需要的密钥版本未配置。"""


class ArchiveClient(Protocol):
    def initialize(self) -> None: ...
    def set_private_key(self, pem: str | bytes, version: int) -> None: ...
    def key_versions(self) -> set[int]: ...
    def fetch(self, cursor: int) -> list[EncryptedChat]: ...
    def decrypt(self, chat: EncryptedChat) -> str: ...
    def close(self) -> None: ...


class FinanceClient:
    def __init__(self, config: WeComArchiveConfig) -> None:
        self.config = config
        self._sdk: WeWorkFinance | None = None
        self._keys: dict[int, RSA.RsaKey] = {}
        # 原生 SDK 的共享句柄不依赖 GIL 保证并发安全。
        self._lock = RLock()

    def set_private_key(self, pem: str | bytes, version: int = 1) -> None:
        if type(version) is not int or version < 0:
            raise ValueError("密钥版本必须为非负整数")
        key = RSA.import_key(pem)
        if not key.has_private():
            raise ValueError("需要 RSA 私钥")
        self._keys[version] = key

    def key_versions(self) -> set[int]:
        return set(self._keys)

    def initialize(self) -> None:
        with self._lock:
            self._get_sdk()

    def _get_sdk(self) -> WeWorkFinance:
        if self._sdk is None:
            from pyweworkfinance import WeWorkFinance

            self._sdk = WeWorkFinance(
                corpid=self.config.corp_id,
                secret=self.config.archive_secret.get_secret_value(),
                default_timeout=self.config.api_timeout,
            )
        return self._sdk

    def fetch(self, cursor: int) -> list[EncryptedChat]:
        with self._lock:
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

    def decrypt(self, chat: EncryptedChat) -> str:
        key = self._keys.get(chat.publickey_ver)
        if key is None:
            raise MissingKeyError(f"未配置私钥版本 {chat.publickey_ver}")
        random_key = PKCS1_v1_5.new(key).decrypt(
            b64decode(chat.encrypt_random_key, validate=True), b""
        )
        if not random_key:
            raise ValueError("随机密钥解密失败")
        with self._lock:
            # 包装库 decrypt_data 会直接解析 JSON；使用同一原生接口保留原始明文。
            lib = self._get_sdk()._lib
            slice_obj = lib.NewSlice()
            if not slice_obj:
                raise RuntimeError("SDK 明文缓冲区创建失败")
            try:
                ret = lib.DecryptData(random_key, chat.encrypt_chat_msg.encode("utf-8"), slice_obj)
                if ret != 0:
                    raise RuntimeError(f"SDK 解密失败，错误码 {ret}")
                return lib.GetContentFromSlice(slice_obj).decode("utf-8")
            finally:
                lib.FreeSlice(slice_obj)

    def close(self) -> None:
        with self._lock:
            if self._sdk is not None:
                self._sdk._destroy()
                self._sdk = None
