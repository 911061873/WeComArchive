import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from conftest import FakeClient, chat, plaintext
from sqlalchemy import event, func, select

from wecomarchive import ArchiveConfig, MessageArchiveService
from wecomarchive.db import TYPE_MODELS, Base, DecryptedMessage, EncryptedMessage
from wecomarchive.storage import ArchiveStore, PreparedMessage, prepare


def count(db, model):
    with db.session() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_full_archive_dedup_cursor_restart_and_types(db):
    store = ArchiveStore(db)
    assert store.cursor() == 0
    messages = [
        prepare(chat(1, extra_field={"keep": True}), plaintext()),
        PreparedMessage(chat(2), decrypt_error="bad-key"),
        prepare(chat(3), plaintext(msgtype="future", future={"x": 1})),
        prepare(chat(4), plaintext(text={"content": 123})),
        prepare(chat(5), plaintext(msgtype="image", image={"sdkfileid": "sdk", "filesize": 12})),
        prepare(chat(6), {"action": "switch", "switch": {"user": "a"}}),
    ]
    committed = store.save_batch(messages, 6)
    assert len(committed) == 5
    assert store.cursor() == 6
    assert count(db, EncryptedMessage) == 6
    assert count(db, DecryptedMessage) == 5
    assert count(db, TYPE_MODELS["text"]) == 1
    assert count(db, TYPE_MODELS["image"]) == 1
    with db.session() as session:
        assert session.get(EncryptedMessage, "m1").raw_data["extra_field"] == {"keep": True}
        assert session.get(DecryptedMessage, "m1").raw_data == messages[0].message.data
        assert session.get(DecryptedMessage, "m4").parse_error
        assert session.get(TYPE_MODELS["image"], "m5").filesize == 12
    assert store.save_batch(messages, 4) == []
    assert count(db, EncryptedMessage) == 6
    assert ArchiveStore(db).cursor() == 6


@pytest.mark.parametrize("failure", ["insert", "commit"])
def test_failure_rolls_back_entire_batch_and_cursor(db, failure):
    store = ArchiveStore(db)
    store.save_batch([prepare(chat(1), plaintext())], 1)

    def fail_insert(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO archive_decrypted"):
            raise RuntimeError("injected write failure")

    def fail_commit(_connection):
        raise RuntimeError("injected commit failure")

    name, hook = (
        ("before_cursor_execute", fail_insert) if failure == "insert" else ("commit", fail_commit)
    )
    event.listen(db.engine, name, hook)
    try:
        with pytest.raises(RuntimeError):
            store.save_batch([prepare(chat(2), plaintext()), prepare(chat(3), plaintext())], 3)
    finally:
        event.remove(db.engine, name, hook)
    assert store.cursor() == 1
    assert count(db, EncryptedMessage) == 1
    assert count(db, DecryptedMessage) == 1
    assert len(store.save_batch([prepare(chat(2), plaintext())], 2)) == 1


def test_sdk_pipeline_partial_failure_duplicate_batch_and_resume(db):
    config = ArchiveConfig(corp_id="test", archive_secret="secret", database_url=str(db.engine.url))
    client = FakeClient([[chat(1), chat(1), chat(2)]], {"m2": ValueError("bad key")})
    service = MessageArchiveService(config, client=client)
    # 使用 fixture 数据库，以便同一测试运行于三种后端。
    service._db.dispose()
    service._db, service._store = db, ArchiveStore(db)
    messages = service._archive_batch()
    assert [m.msgid for m in messages] == ["m1"]
    assert client.decrypted == ["m1", "m2"]
    assert client.cursors == [0]
    client.batches = [[chat(1), chat(2), chat(3)]]
    service._store = ArchiveStore(db)
    assert [m.msgid for m in service._archive_batch()] == ["m3"]
    assert client.cursors == [0, 2]
    assert service._store.cursor() == 3


def test_case_sensitive_ids_and_large_json(db):
    store = ArchiveStore(db)
    data = plaintext(custom="🌏" * 20000)
    assert (
        len(store.save_batch([prepare(chat(1, "AbC"), data), prepare(chat(2, "abc"), data)], 2))
        == 2
    )
    assert count(db, EncryptedMessage) == 2


def test_migration_matches_orm_and_repeat_initialization(db):
    db.initialize()
    with db.engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
