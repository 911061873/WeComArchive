import os
import sqlite3
from pathlib import Path
from threading import RLock

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry, StaticPool


class Database:
    def __init__(self, url: str) -> None:
        self.lock = RLock()
        self._instance_lock = None
        parsed = make_url(url)
        if parsed.get_backend_name() not in {"sqlite", "mysql", "postgresql"}:
            raise ValueError("仅支持 SQLite、MySQL 和 PostgreSQL")
        options = {"pool_pre_ping": True}
        if parsed.get_backend_name() == "sqlite":
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if parsed.database in {None, "", ":memory:"}:
                options["poolclass"] = StaticPool
        self.engine = create_engine(url, **options)
        if parsed.get_backend_name() == "sqlite":

            @event.listens_for(self.engine, "connect")
            def configure(connection: sqlite3.Connection, _record: ConnectionPoolEntry) -> None:
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        """空库自动安装；已有库只检查版本，绝不隐式升级。"""
        from ..migration import current_revision, head_revision, upgrade

        tables = set(inspect(self.engine).get_table_names())
        if not tables:
            upgrade(self.engine)
        elif current_revision(self.engine) != head_revision():
            raise RuntimeError("数据库未初始化或版本不匹配；旧版数据库请备份后另建空库")

    def dispose(self) -> None:
        try:
            self.release_instance()
        finally:
            self.engine.dispose()

    def acquire_instance(self) -> None:
        """锁在构造阶段获取，避免其他实例恢复本实例正在处理的任务。"""
        from sqlalchemy import text

        backend = self.engine.dialect.name
        if backend == "sqlite":
            database = self.engine.url.database
            if database in {None, "", ":memory:"}:
                return
            target = Path(database).resolve().with_suffix(Path(database).suffix + ".lock")
            handle = open(target, "a+b")
            try:
                handle.seek(0, 2)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                raise RuntimeError("同一数据库已有主服务实例") from exc
            self._instance_lock = handle
        else:
            connection = self.engine.connect()
            try:
                if backend == "postgresql":
                    acquired = connection.scalar(text("SELECT pg_try_advisory_lock(192837465)"))
                else:
                    acquired = connection.scalar(
                        text(
                            "SELECT GET_LOCK(CONCAT('wecomarchive:', LEFT(SHA2(DATABASE(), 256), 40)), 0)"
                        )
                    )
                if not acquired:
                    raise RuntimeError("同一数据库已有主服务实例")
            except BaseException:
                connection.close()
                raise
            self._instance_lock = connection

    def release_instance(self) -> None:
        from sqlalchemy import text

        handle, self._instance_lock = self._instance_lock, None
        if handle is None:
            return
        if self.engine.dialect.name == "sqlite":
            handle.close()
        else:
            try:
                if self.engine.dialect.name == "postgresql":
                    handle.execute(text("SELECT pg_advisory_unlock(192837465)"))
                else:
                    handle.execute(
                        text(
                            "SELECT RELEASE_LOCK(CONCAT('wecomarchive:', LEFT(SHA2(DATABASE(), 256), 40)))"
                        )
                    )
            finally:
                handle.close()
