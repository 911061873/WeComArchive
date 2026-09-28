"""配置企业微信参数后：python examples/basic.py。Ctrl+C 正常排空消费队列。"""

import asyncio
import logging
import os
import signal
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
    # Windows 不支持 loop.add_signal_handler，使用 Python 主线程信号处理器。
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        for signum in previous:
            signal.signal(signum, lambda *_: service.stop())
        await service.run()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
