from threading import Event, Thread

import pytest
from conftest import FakeSDK

from wecomarchive import ServiceConfig
from wecomarchive.services.base import Service


def test_constructor_initializes_and_releases_on_failure(tmp_path, archive_factory):
    class Broken(FakeSDK):
        def initialize(self):
            raise ValueError("模拟 SDK 初始化失败")

    sdk = Broken()
    url = f"sqlite:///{tmp_path / 'failure.db'}"
    with pytest.raises(ValueError):
        archive_factory("企业", "密钥", database_url=url, sdk=sdk)
    with archive_factory("企业", "密钥", database_url=url, sdk=FakeSDK()):
        pass


def test_database_rejects_second_instance(tmp_path, archive_factory):
    url = f"sqlite:///{tmp_path / 'locked.db'}"
    first = archive_factory("企业", "密钥", database_url=url, sdk=FakeSDK())
    try:
        with pytest.raises(RuntimeError, match="已有主服务"):
            archive_factory("企业", "密钥", database_url=url, sdk=FakeSDK())
    finally:
        first.close()
    with archive_factory("企业", "密钥", database_url=url, sdk=FakeSDK()):
        pass


def test_start_requires_main_thread_and_has_no_public_stop(tmp_path, archive_factory):
    app = archive_factory(
        "企业", "密钥", database_url=f"sqlite:///{tmp_path / 'thread.db'}", sdk=FakeSDK()
    )
    errors = []

    def run():
        try:
            app.start()
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = Thread(target=run)
    thread.start()
    thread.join()
    assert errors and "主线程" in errors[0]
    assert not hasattr(app, "stop")
    app.close()


def test_subservice_start_returns_and_stop_does_not_claim_backlog():
    entered, release = Event(), Event()

    class Worker(Service):
        claimed = 0

        def run_once(self):
            with self.work_scope(self.acquire) as task:
                if task is not None:
                    self.process(task)

        def acquire(self):
            self.claimed += 1
            return self.claimed

        def process(self, task):
            entered.set()
            release.wait(3)

    service = Worker(ServiceConfig(poll_interval=0.01))
    service.start()
    assert entered.wait(2)
    service.stop()
    assert service.remaining == 1
    release.set()
    service.join()
    assert service.claimed == 1
    assert service.remaining == 0


def test_database_lock_blocks_another_process(tmp_path, archive_factory):
    import os
    import subprocess
    import sys
    from pathlib import Path

    url = f"sqlite:///{tmp_path / 'process.db'}"
    with archive_factory("企业", "密钥", database_url=url, sdk=FakeSDK()):
        code = (
            f"from wecomarchive.db import Database; db = Database({url!r}); db.acquire_instance()"
        )
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, env=environment, timeout=10
        )
        assert result.returncode != 0
        assert b"RuntimeError" in result.stderr


def test_work_scope_releases_count_on_failure_and_does_not_claim_after_stop():
    class Worker(Service):
        def run_once(self):
            pass

    service = Worker(ServiceConfig())
    with pytest.raises(ValueError):
        with service.work_scope():
            assert service.remaining == 1
            raise ValueError("模拟处理失败")
    assert service.remaining == 0
    service.stop()
    claimed = []
    with service.work_scope(lambda: claimed.append("领取")) as task:
        assert task is None
    assert claimed == []
    with service.work_scope() as started:
        assert started is None
    assert service.remaining == 0
