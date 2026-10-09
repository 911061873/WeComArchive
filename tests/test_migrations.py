import json
from datetime import datetime, timezone

import pytest
from alembic import command
from sqlalchemy import MetaData, Table, inspect, select, text

from wecomarchive.db import Base, Database, DecryptedMessage, EncryptedMessage
from wecomarchive.migration import configuration, current_revision, upgrade


def test_empty_database_upgrade_is_idempotent(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'migration.db'}")
    try:
        upgrade(db.engine)
        assert current_revision(db.engine) == "0002"
        upgrade(db.engine)
        assert set(Base.metadata.tables).issubset(inspect(db.engine).get_table_names())
    finally:
        db.dispose()


def test_v1_data_and_failure_states_survive_upgrade(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'v1.db'}")
    try:
        with db.engine.begin() as connection:
            command.upgrade(configuration(connection), "0001")
        metadata = MetaData()
        encrypted = Table("archive_encrypted", metadata, autoload_with=db.engine)
        decrypted = Table("archive_decrypted", metadata, autoload_with=db.engine)
        progress = Table("archive_progress", metadata, autoload_with=db.engine)
        with db.engine.begin() as connection:
            connection.execute(
                encrypted.insert(),
                [
                    dict(
                        msgid=f"m{i}",
                        seq=i,
                        raw_data={"publickey_ver": i},
                        decrypt_error="失败" if i == 2 else None,
                        created_at=datetime.now(timezone.utc),
                    )
                    for i in range(1, 4)
                ],
            )
            connection.execute(
                decrypted.insert(),
                dict(
                    msgid="m1",
                    msgtype="text",
                    msgtime=10,
                    raw_data={"msgtype": "text", "text": {"content": "旧消息"}},
                    parse_error=None,
                ),
            )
            connection.execute(progress.insert(), dict(id=1, cursor=3))
        with pytest.raises(RuntimeError, match="版本不匹配"):
            db.initialize()
        upgrade(db.engine)
        with db.session() as session:
            rows = session.scalars(select(EncryptedMessage).order_by(EncryptedMessage.seq)).all()
            assert [row.status for row in rows] == ["success", "failed", "pending"]
            assert [row.publickey_ver for row in rows] == [1, 2, 3]
            raw = session.get(DecryptedMessage, "m1").raw_text
            assert json.loads(raw)["text"]["content"] == "旧消息"
        with db.engine.connect() as connection:
            assert connection.scalar(text("SELECT cursor FROM archive_progress")) == 3
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
