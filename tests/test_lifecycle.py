from threading import Event, Thread

import pytest
from conftest import FakeClient

from wecomarchive import ServiceConfig, WeComArchive
from wecomarchive.services.base import Service


def test_constructor_initializes_and_releases_on_failure(tmp_path):
    class Broken(FakeClient):
        def initialize(self):
            raise ValueError("模拟 SDK 初始化失败")

    client = Broken()
    url = f"sqlite:///{tmp_path / 'failure.db'}"
    with pytest.raises(ValueError):
        WeComArchive("企业", "密钥", database_url=url, client=client)
    assert client.closed
    with WeComArchive("企业", "密钥", database_url=url, client=FakeClient()):
        pass


def test_database_rejects_second_instance(tmp_path):
    url = f"sqlite:///{tmp_path / 'locked.db'}"
    first = WeComArchive("企业", "密钥", database_url=url, client=FakeClient())
    try:
        with pytest.raises(RuntimeError, match="已有主服务"):
            WeComArchive("企业", "密钥", database_url=url, client=FakeClient())
    finally:
        first.close()
    with WeComArchive("企业", "密钥", database_url=url, client=FakeClient()):
        pass


def test_start_requires_main_thread_and_has_no_public_stop(tmp_path):
    app = WeComArchive(
        "企业", "密钥", database_url=f"sqlite:///{tmp_path / 'thread.db'}", client=FakeClient()
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


def test_late_configuration_rejected(tmp_path):
    app = WeComArchive(
        "企业", "密钥", database_url=f"sqlite:///{tmp_path / 'config.db'}", client=FakeClient()
    )
    app._started = True
    with pytest.raises(RuntimeError):
        app.add_consumer("A", "", False, lambda _: None)
    with pytest.raises(RuntimeError):
        app.set_private_key("私钥", 1)
    app.close()


def test_database_lock_blocks_another_process(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    url = f"sqlite:///{tmp_path / 'process.db'}"
    with WeComArchive("企业", "密钥", database_url=url, client=FakeClient()):
        code = (
            f"from wecomarchive.db import Database; db = Database({url!r}); db.acquire_instance()"
        )
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, env=environment, timeout=10
        )
        assert result.returncode != 0
        assert b"RuntimeError" in result.stderr
