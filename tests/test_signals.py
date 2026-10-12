import re
import signal
import time
from threading import Event, Thread

import pytest
from conftest import FakeSDK, chat

from wecomarchive import ServiceConfig
from wecomarchive.db import ConsumerTask, Database


def test_ctrl_c_waits_for_inflight_consumer_and_restores_handlers(
    tmp_path, monkeypatch, capsys, archive_factory
):
    handlers = {}
    previous = {signal.SIGINT: object(), signal.SIGTERM: object()}

    def install(signum, handler):
        old = handlers.get(signum, previous[signum])
        handlers[signum] = handler
        return old

    monkeypatch.setattr(signal, "signal", install)
    entered, release = Event(), Event()
    sdk = FakeSDK([[chat(), chat(2)]])
    url = f"sqlite:///{tmp_path / 'signals.db'}"
    config = ServiceConfig(poll_interval=0.01)
    app = archive_factory(
        "企业",
        "密钥",
        database_url=url,
        sdk=sdk,
        pull=config,
        decrypt=config,
        parse=config,
        consume=config,
    )
    consumed = []

    def consume(message):
        consumed.append(message.msgid)
        entered.set()
        release.wait(3)

    app.add_consumer("消费者", "", False, consume)

    def interrupt():
        assert entered.wait(3)
        handlers[signal.SIGINT](signal.SIGINT, None)
        # 连续信号不打断等待；关闭期间不释放 SDK。
        handlers[signal.SIGINT](signal.SIGINT, None)
        time.sleep(0.25)
        assert not sdk.closed
        release.set()

    thread = Thread(target=interrupt)
    thread.start()
    app.start()
    thread.join()
    assert sdk.closed
    assert consumed == ["m1"]
    assert handlers == previous
    output = capsys.readouterr().out
    assert "消费：剩余 1" in output
    completed, total = re.findall(r"退出进度：(\d+)/(\d+)，剩余 0", output)[-1]
    assert int(completed) == int(total) >= 1
    with pytest.raises(RuntimeError):
        app.start()
    db = Database(url)
    try:
        with db.session_factory() as session:
            assert session.get(ConsumerTask, ("m1", "消费者")).status == "success"
            assert session.get(ConsumerTask, ("m2", "消费者")) is None
    finally:
        db.dispose()


def test_worker_failure_triggers_cleanup(tmp_path, monkeypatch, archive_factory):
    monkeypatch.setattr(signal, "signal", lambda *_: signal.SIG_DFL)
    sdk = FakeSDK()
    app = archive_factory(
        "企业", "密钥", database_url=f"sqlite:///{tmp_path / 'fatal.db'}", sdk=sdk
    )

    def broken():
        raise RuntimeError("模拟领取失败")

    app._services["解析"].acquire = broken
    with pytest.raises(RuntimeError, match="子服务异常"):
        app.start()
    assert sdk.closed


def test_signal_install_failure_releases_resources(tmp_path, monkeypatch, archive_factory):
    sdk = FakeSDK()
    app = archive_factory(
        "企业", "密钥", database_url=f"sqlite:///{tmp_path / 'install.db'}", sdk=sdk
    )

    def broken(*_args):
        raise ValueError("模拟信号接管失败")

    monkeypatch.setattr(signal, "signal", broken)
    with pytest.raises(ValueError):
        app.start()
    assert sdk.closed
    assert app._closed
