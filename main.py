import os
from pathlib import Path

from wecomarchive import ConsumerMessage, WeComArchive


async def consume(message: ConsumerMessage):
    print(message.msgid, message.text, message.data)


async def test(message: ConsumerMessage):
    print("测试")
    print(message.msgid, message.text)


def main():
    corp_id = os.getenv("WECOM_CORP_ID")
    if not corp_id or not corp_id.strip():
        raise ValueError("请设置非空的环境变量 WECOM_CORP_ID")
    archive_secret = os.getenv("WECOM_ARCHIVE_SECRET")
    if not archive_secret or not archive_secret.strip():
        raise ValueError("请设置非空的环境变量 WECOM_ARCHIVE_SECRET")
    proxy = os.getenv("WECOM_PROXY") or ""
    database_url = os.getenv("WECOM_DATABASE_URL") or "sqlite:///./wecom_archive.db"
    service = WeComArchive(
        corp_id=corp_id, archive_secret=archive_secret, proxy=proxy, database_url=database_url
    )
    service.set_private_key(Path("private_key.pem").read_bytes(), version=1)
    service.add_consumer("本地规则消费者1", "订单", False, consume)
    service.add_consumer("本地规则消费者2", "工单\\s*\\d+", True, consume)
    service.add_consumer("本地规则消费者3", ".*", True, test)
    service.start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
