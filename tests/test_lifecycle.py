import asyncio
import time

import pytest
from conftest import FakeClient, chat, plaintext
from sqlalchemy import select

from wecomarchive import ArchiveConfig, MessageArchiveService, TextRule
from wecomarchive.db import Database, EncryptedMessage
from wecomarchive.storage import prepare


def make_service(db, client, **kwargs):
    return MessageArchiveService(
        ArchiveConfig(
            corp_id="corp",
            archive_secret="secret",
            database_url=db.engine.url.render_as_string(hide_password=False),
            poll_interval=0.01,
            **kwargs,
        ),
        client=client,
    )


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(wait(), 5)


def test_fetch_failure_continues_and_only_committed_new_messages_dispatch(db):
    async def scenario():
        client = FakeClient([RuntimeError("network"), [chat(1)], [chat(1), chat(2)]])
        service = make_service(db, client, consumer_workers=1)
        seen = []

        async def consume(message):
            # 消費者执行时已经能从独立连接读取已提交的密文。
            with service._db.session() as session:
                assert session.get(EncryptedMessage, message.msgid) is not None
            seen.append(message.msgid)
            if len(seen) == 4:
                service.stop()

        service.add_rule(TextRule(contains="订单"), consume)
        service.add_rule(TextRule(regex="123"), consume)
        await asyncio.wait_for(service.run(), 5)
        assert seen == ["m1", "m1", "m2", "m2"]
        assert client.cursors[:3] == [0, 0, 1]
        assert client.decrypted == ["m1", "m2"]
        assert client.closed

    asyncio.run(scenario())


def test_storage_failure_dispatches_nothing_until_next_success(db):
    async def scenario():
        client = FakeClient([[chat(1)], [chat(1)]])
        service = make_service(db, client)
        original = service._store.save_batch
        attempts = []

        def fail_once(results, cursor):
            attempts.append(cursor)
            if len(attempts) == 1:
                raise RuntimeError("database failure")
            return original(results, cursor)

        service._store.save_batch = fail_once
        seen = []

        async def consume(message):
            seen.append(message.msgid)
            service.stop()

        service.add_rule(TextRule(contains="订单"), consume)
        await asyncio.wait_for(service.run(), 5)
        assert client.cursors[:2] == [0, 0]
        assert seen == ["m1"]

    asyncio.run(scenario())


@pytest.mark.parametrize("window, expected", [(300, ["m2"]), (None, ["m1", "m2", "m3"])])
def test_time_window_only_limits_consumption_and_restart_never_replays(db, window, expected):
    async def scenario():
        client = FakeClient(
            [[chat(1), chat(2), chat(3)]],
            {
                "m1": plaintext(timestamp=int((time.time() - 600) * 1000)),
                "m3": plaintext(msgtime="bad timestamp"),
            },
        )
        service = make_service(db, client, consumption_window_seconds=window, consumer_workers=1)
        seen = []

        async def consume(message):
            seen.append(message.msgid)
            if len(seen) == len(expected):
                service.stop()

        service.add_rule(TextRule(contains="订单"), consume)
        await asyncio.wait_for(service.run(), 5)
        assert seen == expected
        archived_db = Database(service.config.database_url)
        with archived_db.session() as session:
            assert len(list(session.scalars(select(EncryptedMessage)))) == 3
        archived_db.dispose()

        resumed_client = FakeClient([[chat(1), chat(2), chat(3)]])
        resumed = make_service(db, resumed_client, consumption_window_seconds=None)
        resumed.add_rule(TextRule(contains="订单"), consume)
        task = asyncio.create_task(resumed.run())
        await until(lambda: len(resumed_client.cursors) >= 2)
        resumed.stop()
        await asyncio.wait_for(task, 5)
        assert seen == expected
        assert resumed_client.cursors[0] == 3
        assert resumed_client.decrypted == []

    asyncio.run(scenario())


def test_backpressure_consumer_failure_and_graceful_drain(db):
    async def scenario():
        client = FakeClient([[chat(i) for i in range(1, 5)]])
        service = make_service(db, client, queue_capacity=1, consumer_workers=1)
        release = asyncio.Event()
        seen = []

        async def consume(message):
            seen.append(message.msgid)
            if message.msgid == "m1":
                await release.wait()
                raise RuntimeError("business failure")

        service.add_rule(TextRule(contains="订单"), consume)
        task = asyncio.create_task(service.run())
        await until(lambda: seen == ["m1"] and service._queue.full())
        await asyncio.sleep(0.03)
        assert client.cursors == [0]  # 队列满时不继续拉取。
        service.stop()
        release.set()
        await asyncio.wait_for(task, 5)
        assert seen == ["m1", "m2", "m3", "m4"]
        assert service._queue.empty()

    asyncio.run(scenario())


def test_shutdown_timeout_cancels_and_does_not_replay(db):
    async def scenario():
        client = FakeClient([[chat(1), chat(2), chat(3)]])
        service = make_service(
            db, client, shutdown_timeout=0.05, queue_capacity=1, consumer_workers=1
        )
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def stuck(message):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        service.add_rule(TextRule(contains="订单"), stuck)
        task = asyncio.create_task(service.run())
        await asyncio.wait_for(started.wait(), 5)
        service.stop()
        await asyncio.wait_for(task, 2)
        assert cancelled.is_set() and client.closed
        resumed_client = FakeClient([[chat(1), chat(2), chat(3)]])
        resumed = make_service(db, resumed_client)
        seen = []

        async def consume(message):
            seen.append(message)

        resumed.add_rule(TextRule(contains="订单"), consume)
        task = asyncio.create_task(resumed.run())
        await until(lambda: len(resumed_client.cursors) >= 2)
        resumed.stop()
        await asyncio.wait_for(task, 5)
        assert seen == []

    asyncio.run(scenario())


def test_worker_count_and_message_isolation(db):
    async def scenario():
        client = FakeClient([[chat(1)]])
        service = make_service(db, client, consumer_workers=2)
        both_started = asyncio.Event()
        seen = []

        async def consumer(message):
            seen.append(message.text)
            message.data["text"]["content"] = "changed"
            if len(seen) == 2:
                both_started.set()
            await both_started.wait()
            service.stop()

        service.add_rule(TextRule(contains="订单"), consumer)
        service.add_rule(TextRule(contains="订单"), consumer)
        await asyncio.wait_for(service.run(), 5)
        assert len(seen) == 2 and seen[0] == seen[1]

    asyncio.run(scenario())


def test_queue_wait_does_not_recheck_time_window(db, monkeypatch):
    async def scenario():
        service = make_service(db, FakeClient(), queue_capacity=1, consumption_window_seconds=5)
        clock = [100.0]
        monkeypatch.setattr("wecomarchive.service.time.time", lambda: clock[0])

        async def consume(_message):
            pass

        service.add_rule(TextRule(contains="订单"), consume)
        service.add_rule(TextRule(contains="订单"), consume)
        message = prepare(chat(), plaintext(timestamp=99000)).message
        task = asyncio.create_task(service._dispatch([message]))
        await until(service._queue.full)
        clock[0] = 200.0
        service._queue.get_nowait()
        service._queue.task_done()
        await asyncio.wait_for(task, 2)
        assert service._queue.qsize() == 1
        service._db.dispose()

    asyncio.run(scenario())


def test_registration_and_single_run_guards(db):
    service = make_service(db, FakeClient())
    with pytest.raises(TypeError):
        service.add_rule(TextRule(contains="x"), lambda message: None)

    async def scenario():
        service.stop()
        await service.run()
        with pytest.raises(RuntimeError):
            await service.run()
        with pytest.raises(RuntimeError):
            service.add_rule(TextRule(contains="x"), service.run)

    asyncio.run(scenario())


def test_cancel_run_drains_then_releases_client(db):
    async def scenario():
        client = FakeClient([[chat(1)]])
        service = make_service(db, client)
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []

        async def consume(message):
            entered.set()
            await release.wait()
            seen.append(message.msgid)

        service.add_rule(TextRule(contains="订单"), consume)
        task = asyncio.create_task(service.run())
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert seen == ["m1"]
        assert client.closed
        assert not any(t.get_name().startswith("archive-") for t in asyncio.all_tasks())

    asyncio.run(scenario())


def test_slow_sdk_does_not_block_loop_and_is_not_closed_while_running(db):
    import threading

    class SlowClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.entered, self.release = threading.Event(), threading.Event()
            self.completed = False

        def fetch(self, cursor):
            self.entered.set()
            assert self.release.wait(5)
            self.completed = True
            return [chat(1)]

        def close(self):
            assert self.completed
            super().close()

    async def scenario():
        client = SlowClient()
        service = make_service(db, client, shutdown_timeout=0.02)
        task = asyncio.create_task(service.run())
        try:
            await until(client.entered.is_set)
            service.stop()
            # 让消费排空期限过期，底层线程仍须被安全等待。
            await asyncio.sleep(0.05)
            assert not client.closed
        finally:
            client.release.set()
        await asyncio.wait_for(task, 5)
        assert client.closed

    asyncio.run(scenario())


def test_consumer_self_cancellation_does_not_kill_worker(db):
    async def scenario():
        client = FakeClient([[chat(1), chat(2)]])
        service = make_service(db, client, consumer_workers=1)
        seen = []

        async def consume(message):
            seen.append(message.msgid)
            if len(seen) == 1:
                raise asyncio.CancelledError()
            service.stop()

        service.add_rule(TextRule(contains="订单"), consume)
        await asyncio.wait_for(service.run(), 5)
        assert seen == ["m1", "m2"]

    asyncio.run(scenario())
