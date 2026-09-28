from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class Database:
    def __init__(self, url: str):
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
            def configure(connection, _record):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self):
        """空库自动安装；已有库只检查版本，绝不隐式升级。"""
        from ..migrations import current_revision, head_revision, upgrade

        tables = set(inspect(self.engine).get_table_names())
        if not tables:
            upgrade(self.engine)
        elif current_revision(self.engine) != head_revision():
            raise RuntimeError(
                "数据库未初始化或版本不匹配；请使用新空库，或执行 wecomarchive-db upgrade"
            )

    def dispose(self):
        self.engine.dispose()
