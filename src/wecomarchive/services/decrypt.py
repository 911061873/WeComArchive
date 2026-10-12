from __future__ import annotations

import logging
from base64 import b64decode
from typing import Any

from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

from ..config import ServiceConfig
from ..models import EncryptedChat
from ..storage import ArchiveStore
from .base import Service

log = logging.getLogger(__name__)


class DecryptService(Service):
    def __init__(
        self,
        config: ServiceConfig,
        store: ArchiveStore,
        *,
        keys: dict[int, RSA.RsaKey] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self.store = store
        self.keys = keys if keys is not None else {}

    def run_once(self) -> None:
        with self.work_scope(self.acquire) as task:
            if task is not None:
                self.process(task)
        if task is None:
            self.wait()

    def acquire(self) -> EncryptedChat | None:
        return self.store.claim("decrypt")

    def process(self, task: EncryptedChat) -> None:
        chat = task
        key = self.keys.get(chat.publickey_ver)
        if key is None:
            self.store.fail("decrypt", chat.msgid, "waiting_key", None)
            return
        try:
            raw_text = self._decrypt(chat, key)
            self.store.decrypted(chat, raw_text)
        except Exception as exc:
            error = type(exc).__name__
            self.store.fail("decrypt", chat.msgid, "failed", error)
            log.error("解密失败 msgid=%s error=%s", chat.msgid, error)

    def _decrypt(self, chat: EncryptedChat, key: RSA.RsaKey) -> str:
        random_key = PKCS1_v1_5.new(key).decrypt(
            b64decode(chat.encrypt_random_key, validate=True), b""
        )
        if not random_key:
            raise ValueError("随机密钥解密失败")
        if self.sdk is None:
            raise RuntimeError("SDK 未初始化")
        with self._sdk_lock:
            # SDK 包装库会解析 JSON，这里直接获取原始明文供解析服务处理。
            lib = self.sdk._lib
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
