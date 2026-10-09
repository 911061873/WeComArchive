from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine


def configuration(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.attributes["connection"] = connection
    return config


def head_revision() -> str | None:
    return ScriptDirectory.from_config(configuration()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def upgrade(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    if tables - {"alembic_version"} and current_revision(engine) is None:
        raise RuntimeError("拒绝升级未受版本管理的已有数据库；开发阶段旧库请备份后另建空库")
    if current_revision(engine) in {"0001", "0002"}:
        raise RuntimeError("旧版数据库迁移已废弃；请备份后另建空库，不支持迁移旧版数据")
    with engine.begin() as connection:
        command.upgrade(configuration(connection), "head")
