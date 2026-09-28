import asyncio
import os
from pathlib import Path

from wecomarchive import ArchiveConfig, ConsumerMessage, MessageArchiveService, TextRule


async def consume(message: ConsumerMessage):
    print(message.msgid, message.text, message.data)


async def test(message: ConsumerMessage):
    print("测试")
    print(message.msgid, message.text)


async def main():
    corp_id = os.getenv("WECOM_CORP_ID")
    if not corp_id or not corp_id.strip():
        raise ValueError("请设置非空的环境变量 WECOM_CORP_ID")
    archive_secret = os.getenv("WECOM_ARCHIVE_SECRET")
    if not archive_secret or not archive_secret.strip():
        raise ValueError("请设置非空的环境变量 WECOM_ARCHIVE_SECRET")
    proxy = os.getenv("WECOM_PROXY") or ""
    service = MessageArchiveService(
        ArchiveConfig(
            corp_id=corp_id,
            archive_secret=archive_secret,
            proxy=proxy,
        )
    )
    service.set_private_key(Path("private_key.pem").read_bytes(), version=1)
    service.add_rule(TextRule(contains="订单"), consume)
    service.add_rule(TextRule(regex=r"工单\s*\d+"), consume)
    service.add_rule(TextRule(regex=".*"), test)
    await service.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
