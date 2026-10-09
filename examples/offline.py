"""无凭据演示；运行 python examples/offline.py，按 Ctrl+C 退出。"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wecomarchive import WeComArchive
from wecomarchive.models import EncryptedChat


class OfflineClient:
    def initialize(self):
        pass

    def key_versions(self):
        return {1}

    def set_private_key(self, pem, version):
        pass

    def fetch(self, cursor):
        if cursor >= 1:
            return []
        return [
            EncryptedChat(
                seq=1,
                msgid="offline-1",
                publickey_ver=1,
                encrypt_random_key="演示",
                encrypt_chat_msg="演示",
            )
        ]

    def decrypt(self, message):
        return json.dumps(
            dict(
                msgid=message.msgid,
                msgtype="text",
                msgtime=int(time.time() * 1000),
                text={"content": "离线演示消息"},
                **{"from": "演示发送者", "tolist": ["演示接收者"]},
            ),
            ensure_ascii=False,
        )

    def close(self):
        pass


def main():
    with WeComArchive(
        "离线企业", "演示密钥", database_url="sqlite:///:memory:", client=OfflineClient()
    ) as archive:
        archive.add_consumer(
            "打印消息", "", False, lambda message: print(message.msgid, message.text)
        )
        archive.start()


if __name__ == "__main__":
    main()
