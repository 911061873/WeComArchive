"""在源码目录外验证已安装的 Nuitka wheel 及迁移资源。"""

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import inspect

import wecomarchive
from wecomarchive import ServiceConfig
from wecomarchive.db import Base, Database
from wecomarchive.migration import current_revision, head_revision, upgrade
from wecomarchive.models import EncryptedChat
from wecomarchive.services import ConsumeService, ParseService
from wecomarchive.storage import ArchiveStore


def main():
    package_path = Path(wecomarchive.__file__).resolve()
    if not hasattr(wecomarchive, "__compiled__"):
        raise RuntimeError(f"导入的不是编译扩展模块：{package_path}")
    with TemporaryDirectory(prefix="wecomarchive-wheel-") as directory:
        database_path = Path(directory) / "archive.db"
        db = Database(f"sqlite:///{database_path.as_posix()}")
        try:
            upgrade(db.engine)
            upgrade(db.engine)
            if current_revision(db.engine) != head_revision():
                raise RuntimeError("wheel 数据库迁移版本不正确")
            if not set(Base.metadata.tables).issubset(inspect(db.engine).get_table_names()):
                raise RuntimeError("wheel 数据库迁移缺少业务表")
            store = ArchiveStore(db)
            chat = EncryptedChat(
                seq=1,
                msgid="wheel-1",
                publickey_ver=1,
                encrypt_random_key="演示",
                encrypt_chat_msg="演示",
            )
            store.save_batch([chat])
            store.claim("decrypt")
            store.decrypted(
                chat,
                json.dumps(
                    {
                        "msgid": chat.msgid,
                        "msgtype": "text",
                        "msgtime": int(time.time() * 1000),
                        "from": "发送者",
                        "tolist": [],
                        "text": {"content": "编译包验证"},
                    }
                ),
            )
            parser = ParseService(ServiceConfig(), store)
            parser.process(parser.acquire())
            consumer = ConsumeService(ServiceConfig(), store, 300)
            called = []
            consumer.register("验证消费者", "", False, lambda message: called.append(message.text))
            consumer.process(consumer.acquire())
            if called != ["编译包验证"] or consumer.acquire() is not None:
                raise RuntimeError("编译包流水线或消费者持久化验证失败")
        finally:
            db.dispose()
    print("编译模块、数据库迁移及第二版流水线验证通过")


if __name__ == "__main__":
    main()
