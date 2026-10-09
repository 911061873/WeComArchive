"""配置实际 SDK，主线程阻塞运行，Ctrl+C 后等待当前任务结束。"""

import os
from pathlib import Path

from wecomarchive import ConsumerMessage, ServiceConfig, WeComArchive


def consume(message: ConsumerMessage):
    if "订单" in message.text:
        print(message.msgid, message.sender, message.text)


def main():
    with WeComArchive(
        os.environ["WECOM_CORP_ID"],
        os.environ["WECOM_ARCHIVE_SECRET"],
        decrypt=ServiceConfig(threads=2),
        consume=ServiceConfig(threads=4),
    ) as archive:
        archive.set_private_key(
            Path(os.environ["WECOM_PRIVATE_KEY_PATH"]).read_bytes(),
            version=int(os.environ.get("WECOM_PRIVATE_KEY_VERSION", "1")),
        )
        archive.add_consumer("订单处理", "订单", False, consume)
        archive.start()


if __name__ == "__main__":
    main()
