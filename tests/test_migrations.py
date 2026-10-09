import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from wecomarchive.db import Base, Database
from wecomarchive.migration import configuration, current_revision, upgrade


def test_empty_database_upgrade_is_idempotent(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'migration.db'}")
    try:
        upgrade(db.engine)
        assert current_revision(db.engine) == "0001_v2"
        upgrade(db.engine)
        assert set(inspect(db.engine).get_table_names()) == set(Base.metadata.tables) | {
            "alembic_version"
        }
        with db.engine.connect() as connection:
            context = MigrationContext.configure(connection)
            assert compare_metadata(context, Base.metadata) == []
    finally:
        db.dispose()


@pytest.mark.parametrize("revision", ["0001", "0002"])
def test_obsolete_database_is_not_modified(tmp_path, revision):
    db = Database(f"sqlite:///{tmp_path / 'obsolete.db'}")
    try:
        with db.engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
            )
            connection.execute(
                text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": revision}
            )
            connection.execute(
                text("CREATE TABLE archive_progress (id INTEGER PRIMARY KEY, cursor BIGINT)")
            )
            connection.execute(text("INSERT INTO archive_progress VALUES (1, 42)"))
        with pytest.raises(RuntimeError, match="版本不匹配"):
            db.initialize()
        with pytest.raises(RuntimeError, match="已废弃"):
            upgrade(db.engine)
        assert current_revision(db.engine) == revision
        with db.engine.connect() as connection:
            assert connection.scalar(text("SELECT cursor FROM archive_progress")) == 42
        assert set(inspect(db.engine).get_table_names()) == {"alembic_version", "archive_progress"}
    finally:
        db.dispose()


def test_initial_schema_can_be_recreated(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'recreate.db'}")
    try:
        upgrade(db.engine)
        with db.engine.begin() as connection:
            command.downgrade(configuration(connection), "base")
        assert set(inspect(db.engine).get_table_names()) == {"alembic_version"}
        upgrade(db.engine)
        assert current_revision(db.engine) == "0001_v2"
        assert set(inspect(db.engine).get_table_names()) == set(Base.metadata.tables) | {
            "alembic_version"
        }
    finally:
        db.dispose()


def test_unmanaged_database_is_not_modified(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'old.db'}")
    try:
        with db.engine.begin() as connection:
            connection.execute(text("CREATE TABLE progress (cursor BIGINT)"))
            connection.execute(text("INSERT INTO progress VALUES (42)"))
        with pytest.raises(RuntimeError):
            db.initialize()
        with pytest.raises(RuntimeError):
            upgrade(db.engine)
        with db.engine.connect() as connection:
            assert connection.scalar(text("SELECT cursor FROM progress")) == 42
    finally:
        db.dispose()
