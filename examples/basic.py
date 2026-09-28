"""配置企业微信参数后：python examples/basic.py。Ctrl+C 正常排空消费队列。"""

import asyncio
import logging
import os
from pathlib import Path

from wecomarchive import ArchiveConfig, ConsumerMessage, MessageArchiveService, TextRule


class PrintConsumer:
    async def consume(self, message: ConsumerMessage) -> None:
        print(message.msgid, message.text)


async def main():
    config = ArchiveConfig(
        corp_id=os.environ["WECOM_CORP_ID"],
        archive_secret=os.environ["WECOM_ARCHIVE_SECRET"],
        database_url=os.environ.get("WECOM_DATABASE_URL", "sqlite:///./wecom_archive.db"),
    )
    service = MessageArchiveService(config)
    service.set_private_key(
        Path(os.environ["WECOM_PRIVATE_KEY_PATH"]).read_bytes(),
        version=int(os.environ.get("WECOM_PRIVATE_KEY_VERSION", "0")),
    )
    consumer = PrintConsumer()
    service.add_rule(TextRule(contains="订单"), consumer)
    service.add_rule(TextRule(regex=r"工单\s*\d+"), consumer)
    await service.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # 服务启动前（如读取配置/私钥时）的 Ctrl+C 也不输出堆栈。
        pass
