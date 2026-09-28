import pytest
from alembic import command
from sqlalchemy import inspect, text

from wecomarchive.db import Base, Database
from wecomarchive.migrations import configuration, current_revision, upgrade


def test_empty_database_explicit_upgrade_and_downgrade(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'migration.db'}")
    try:
        assert current_revision(db.engine) is None
        upgrade(db.engine)
        assert current_revision(db.engine) == "0001"
        upgrade(db.engine)
        assert set(Base.metadata.tables).issubset(inspect(db.engine).get_table_names())
        with db.engine.begin() as connection:
            command.downgrade(configuration(connection), "base")
        assert inspect(db.engine).get_table_names() == ["alembic_version"]
        upgrade(db.engine)
        assert current_revision(db.engine) == "0001"
    finally:
        db.dispose()


def test_old_development_database_is_not_modified(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'old.db'}")
    try:
        with db.engine.begin() as connection:
            connection.execute(text("CREATE TABLE progress (cursor BIGINT)"))
            connection.execute(text("INSERT INTO progress VALUES (42)"))
        with pytest.raises(RuntimeError):
            db.initialize()
        with pytest.raises(RuntimeError):
            upgrade(db.engine)
        assert inspect(db.engine).get_table_names() == ["progress"]
        with db.engine.connect() as connection:
            assert connection.scalar(text("SELECT cursor FROM progress")) == 42
    finally:
        db.dispose()


def test_older_version_requires_explicit_upgrade(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'version.db'}")
    try:
        db.initialize()
        with db.engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num='future_version'"))
        with pytest.raises(RuntimeError):
            db.initialize()
        assert current_revision(db.engine) == "future_version"
    finally:
        db.dispose()
