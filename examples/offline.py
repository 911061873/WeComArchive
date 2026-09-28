"""无需企业微信凭据的完整演示：python examples/offline.py。"""

import asyncio
import tempfile
import time
from pathlib import Path

from wecomarchive import ArchiveConfig, MessageArchiveService, TextRule
from wecomarchive.models import EncryptedChat


class DemoClient:
    def fetch(self, cursor):
        if cursor:
            return []
        return [
            EncryptedChat(
                seq=1,
                msgid="demo-1",
                publickey_ver=1,
                encrypt_random_key="demo",
                encrypt_chat_msg="demo",
            )
        ]

    def decrypt(self, chat):
        return {
            "msgtype": "text",
            "msgtime": int(time.time() * 1000),
            "text": {"content": "新订单 123"},
        }

    def close(self):
        pass


async def main():
    with tempfile.TemporaryDirectory() as directory:
        service = MessageArchiveService(
            ArchiveConfig(
                corp_id="demo",
                archive_secret="demo",
                database_url=f"sqlite:///{Path(directory) / 'archive.db'}",
            ),
            client=DemoClient(),
        )

        async def consume(message):
            print(f"消费完成：{message.msgid} {message.text}")
            service.stop()

        service.add_rule(TextRule(contains="订单"), consume)
        await service.run()


if __name__ == "__main__":
    asyncio.run(main())
