import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import chat, plaintext
from sqlalchemy import select

from wecomarchive import ServiceConfig
from wecomarchive.db import ConsumerTask, DecryptedMessage, EncryptedMessage, ParsedMessage
from wecomarchive.sdk import MissingKeyError
from wecomarchive.services import ConsumeService, DecryptService, ParseService, PullService
from wecomarchive.storage import ArchiveStore


def parsed(store, seq=1, timestamp=None):
    item = chat(seq)
    store.save_batch([item])
    store.claim("decrypt")
    store.decrypted(item, json.dumps({"msgid": item.msgid, **plaintext(timestamp)}))
    service = ParseService(ServiceConfig(), store)
    service.process(service.acquire())
    with store.db.session() as session:
        return session.get(ParsedMessage, item.msgid)


def state(db, model, key):
    with db.session() as session:
        return session.get(model, key).status


def test_cursor_dedup_and_atomic_rollback(db, monkeypatch):
    store = ArchiveStore(db)
    store.save_batch([chat(), chat(), chat(2)])
    assert store.cursor() == 2
    with db.session() as session:
        assert len(session.scalars(select(EncryptedMessage)).all()) == 2
    from sqlalchemy import event

    def fail(*_args):
        raise RuntimeError("模拟数据库提交失败")

    event.listen(db.engine, "commit", fail)
    with pytest.raises(RuntimeError):
        store.save_batch([chat(3)])
    event.remove(db.engine, "commit", fail)
    assert store.cursor() == 2
    with db.session() as session:
        assert session.get(EncryptedMessage, "m3") is None


def test_pull_failure_uses_saved_cursor_each_round(db):
    from conftest import FakeClient

    store = ArchiveStore(db)
    client = FakeClient([[chat()], RuntimeError("模拟拉取失败"), [chat(2)]])
    service = PullService(ServiceConfig(poll_interval=0.001), store, client)
    for _ in range(3):
        service.process(service.acquire())
    assert client.cursors == [0, 1, 1]
    assert store.cursor() == 2


def test_claim_is_unique_under_threads(db):
    store = ArchiveStore(db)
    store.save_batch([chat(i) for i in range(1, 21)])
    with ThreadPoolExecutor(max_workers=8) as pool:
        tasks = list(pool.map(lambda _: store.claim("decrypt"), range(30)))
    ids = [item.msgid for item in tasks if item is not None]
    assert len(ids) == len(set(ids)) == 20


@pytest.mark.parametrize(
    "error,expected", [(MissingKeyError(), "waiting_key"), (ValueError(), "failed")]
)
def test_decrypt_failure_and_missing_key_are_distinct(db, error, expected):
    from conftest import FakeClient

    store = ArchiveStore(db)
    store.save_batch([chat()])
    service = DecryptService(ServiceConfig(), store, FakeClient(data={"m1": error}))
    service.process(service.acquire())
    assert state(db, EncryptedMessage, "m1") == expected
    store.recover(set())
    assert service.acquire() is None
    store.recover({1})
    assert (service.acquire() is not None) == (expected == "waiting_key")


def test_decrypt_retains_raw_string_without_parsing(db):
    from conftest import FakeClient

    raw = '  { "msgtype": "text", "malformed": true }  '
    store = ArchiveStore(db)
    store.save_batch([chat()])
    service = DecryptService(ServiceConfig(), store, FakeClient(data={"m1": raw}))
    service.process(service.acquire())
    with db.session() as session:
        row = session.get(DecryptedMessage, "m1")
        assert row.raw_text == raw
        assert row.status == "pending"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("[]", "failed"),
        ("broken", "failed"),
        ('{"msgtype":"image"}', "unsupported"),
        ('{"msgtype":"text"}', "failed"),
        ('{"msgtype":"future"}', "unsupported"),
    ],
)
def test_parse_failure_and_unsupported_are_terminal(db, raw, expected):
    store = ArchiveStore(db)
    store.save_batch([chat()])
    store.claim("decrypt")
    store.decrypted(chat(), raw)
    service = ParseService(ServiceConfig(), store)
    service.process(service.acquire())
    assert state(db, DecryptedMessage, "m1") == expected
    store.recover({1})
    assert service.acquire() is None


def test_text_parse_and_independent_consumers(db):
    store = ArchiveStore(db)
    row = parsed(store)
    assert row.data["sender"] == "sender"
    assert row.data["data"]["text"]["content"] == "订单 123 你好 🌏"
    service = ConsumeService(ServiceConfig(), store, 300)
    called = []

    def failed(message):
        message.data["text"]["content"] = "修改副本"
        raise ValueError("模拟消费者失败")

    async def successful(message):
        called.append(message.text)

    service.register("失败消费者", "", False, failed)
    service.register("成功消费者", "", False, successful)
    service.process(service.acquire())
    service.process(service.acquire())
    assert called == ["订单 123 你好 🌏"]
    assert state(db, ConsumerTask, ("m1", "失败消费者")) == "failed"
    assert state(db, ConsumerTask, ("m1", "成功消费者")) == "success"
    store.recover({1})
    assert service.acquire() is None
    service.register("新增消费者", "", False, successful)
    service.process(service.acquire())
    assert len(called) == 2


def test_window_is_checked_immediately_before_callback(db, monkeypatch):
    store = ArchiveStore(db)
    timestamp = int(time.time() * 1000)
    parsed(store, timestamp=timestamp)
    service = ConsumeService(ServiceConfig(), store, 300)
    calls = []
    service.register("消费者", "", False, calls.append)
    task = service.acquire()
    monkeypatch.setattr(time, "time", lambda: timestamp / 1000 + 301)
    service.process(task)
    assert calls == []
    assert state(db, ConsumerTask, ("m1", "消费者")) == "expired"


@pytest.mark.parametrize("age", [301, -10])
def test_new_consumer_does_not_replay_outside_window(db, age):
    store = ArchiveStore(db)
    parsed(store, timestamp=int((time.time() - age) * 1000))
    assert store.claim_consumer(["新增消费者"], 300) is None
    with db.session() as session:
        assert session.get(ParsedMessage, "m1") is not None
        assert session.scalars(select(ConsumerTask)).all() == []


def test_restart_recovers_only_processing_and_configured_waiting_keys(db):
    store = ArchiveStore(db)
    for index, status in enumerate(
        ["processing", "success", "failed", "waiting_key", "pending"], 1
    ):
        store.save_batch([chat(index)])
        store.fail("decrypt", f"m{index}", status, None)
    parsed(store, seq=10)
    task = store.claim_consumer(["消费者"], 300)
    assert task is not None
    store.recover(set())
    assert state(db, ConsumerTask, ("m10", "消费者")) == "pending"
    assert [state(db, EncryptedMessage, f"m{i}") for i in range(1, 6)] == [
        "pending",
        "success",
        "failed",
        "waiting_key",
        "pending",
    ]
    store.recover({1})
    assert state(db, EncryptedMessage, "m4") == "pending"


def test_consumer_claim_is_unique_under_threads(db):
    store = ArchiveStore(db)
    parsed(store)
    with ThreadPoolExecutor(max_workers=8) as pool:
        tasks = list(pool.map(lambda _: store.claim_consumer(["A", "B"], 300), range(20)))
    claims = [task[0] for task in tasks if task]
    assert sorted(claims) == ["A", "B"]


def test_async_cancellation_is_terminal_without_affecting_other_consumers(db):
    import asyncio

    store = ArchiveStore(db)
    parsed(store)
    service = ConsumeService(ServiceConfig(), store, 300)
    calls = []

    async def canceled(message):
        raise asyncio.CancelledError()

    service.register("取消消费者", "", False, canceled)
    service.register("其他消费者", "", False, calls.append)
    service.process(service.acquire())
    service.process(service.acquire())
    assert len(calls) == 1
    assert state(db, ConsumerTask, ("m1", "取消消费者")) == "failed"
    assert service.acquire() is None


def test_started_callback_completes_after_window_expires(db, monkeypatch):
    store = ArchiveStore(db)
    timestamp = int(time.time() * 1000)
    parsed(store, timestamp=timestamp)
    service = ConsumeService(ServiceConfig(), store, 300)

    def callback(message):
        monkeypatch.setattr(time, "time", lambda: timestamp / 1000 + 301)

    service.register("消费者", "", False, callback)
    service.process(service.acquire())
    assert state(db, ConsumerTask, ("m1", "消费者")) == "success"


def test_restart_recovers_interrupted_parse_but_preserves_unsupported(db):
    store = ArchiveStore(db)
    for i in (1, 2):
        store.save_batch([chat(i)])
        store.claim("decrypt")
        store.decrypted(chat(i), json.dumps({"msgtype": "image"}))
    task = store.claim("parse")
    ParseService(ServiceConfig(), store).process(store.claim("parse"))
    store.recover({1})
    assert store.claim("parse")[0] == task[0]
    assert store.claim("parse") is None
    assert state(db, DecryptedMessage, "m2") == "unsupported"


def test_database_error_saving_consumer_result_leaves_recoverable_processing(db, monkeypatch):
    store = ArchiveStore(db)
    parsed(store)
    service = ConsumeService(ServiceConfig(), store, 300)
    service.register("消费者", "", False, lambda _: None)
    task = service.acquire()

    def failed(*_args, **_kwargs):
        raise RuntimeError("模拟保存消费状态失败")

    monkeypatch.setattr(store, "finish_consumer", failed)
    with pytest.raises(RuntimeError):
        service.process(task)
    assert state(db, ConsumerTask, ("m1", "消费者")) == "processing"
    store.recover({1})
    assert state(db, ConsumerTask, ("m1", "消费者")) == "pending"


def test_consumers_share_claims_without_starvation(db):
    store = ArchiveStore(db)
    for seq in (1, 2, 3):
        parsed(store, seq=seq)
    service = ConsumeService(ServiceConfig(), store, 300)
    service.register("A", "", False, lambda _: None)
    service.register("B", "", False, lambda _: None)
    tasks = [service.acquire() for _ in range(4)]
    assert [task[0] for task in tasks] == ["A", "B", "A", "B"]
    assert [task[1].msgid for task in tasks] == ["m1", "m1", "m2", "m2"]


@pytest.mark.parametrize(
    "match_text,is_regex,matched",
    [
        ("订单", False, True),
        ("工单", False, False),
        (r"订单\s+\d+", True, True),
        (r"^123", True, False),
        ("", False, True),
        ("你好 🌏", False, True),
        (".", False, False),
    ],
)
def test_consumer_content_filters_and_terminal_skip(db, match_text, is_regex, matched):
    store = ArchiveStore(db)
    parsed(store)
    service = ConsumeService(ServiceConfig(), store, 300)
    calls = []
    service.register("内容过滤", match_text, is_regex, calls.append)
    service.process(service.acquire())
    assert bool(calls) == matched
    assert state(db, ConsumerTask, ("m1", "内容过滤")) == ("success" if matched else "skipped")
    store.recover({1})
    assert service.acquire() is None


def test_unmatched_consumer_does_not_prevent_another_match(db):
    store = ArchiveStore(db)
    parsed(store)
    service = ConsumeService(ServiceConfig(), store, 300)
    calls = []
    service.register("未匹配", "工单", False, calls.append)
    service.register("匹配", "订单", False, calls.append)
    service.process(service.acquire())
    service.process(service.acquire())
    assert len(calls) == 1
    assert state(db, ConsumerTask, ("m1", "未匹配")) == "skipped"
    assert state(db, ConsumerTask, ("m1", "匹配")) == "success"
