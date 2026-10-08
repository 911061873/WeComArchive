import asyncio
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import FakeClient

from wecomarchive import ArchiveConfig, MessageArchiveService


@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM"])
def test_signal_drains_queue_restores_handlers_and_exits_without_traceback(tmp_path, signame):
    # 真实信号在子进程内发送，回归失败也不会中断 pytest 本身。
    script = """
import asyncio
import signal
import sys
from conftest import FakeClient, chat
from wecomarchive import ArchiveConfig, MessageArchiveService, TextRule

async def main():
    def previous_handler(*args):
        raise AssertionError('previous handler called during service shutdown')
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, previous_handler)
    client = FakeClient([[chat(1), chat(2)]])
    service = MessageArchiveService(ArchiveConfig(corp_id='test', archive_secret='test',
        database_url=sys.argv[1], consumer_workers=1), client=client)
    seen = []
    async def consume(message):
        if message.msgid == 'm1':
            signal.raise_signal(getattr(signal, sys.argv[2]))
            await asyncio.sleep(0.02)
            signal.raise_signal(getattr(signal, sys.argv[2]))
        seen.append(message.msgid)
    service.add_rule(TextRule(regex='.*'), consume)
    await service.run()
    assert seen == ['m1', 'm2'], seen
    assert client.closed
    assert client.cursors == [0]
    for signum in (signal.SIGINT, signal.SIGTERM):
        assert signal.getsignal(signum) is previous_handler
    print('clean shutdown')
asyncio.run(main())
"""
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root / "src"), str(root / "tests")])}
    result = subprocess.run(
        [sys.executable, "-c", script, f"sqlite:///{tmp_path / 'signals.db'}", signame],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "clean shutdown" in result.stdout
    assert "Traceback" not in result.stderr
    assert "KeyboardInterrupt" not in result.stderr


@pytest.mark.parametrize("background", [False, True])
def test_signal_opt_out_and_background_thread_leave_host_handlers_alone(tmp_path, background):
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    outside_loop = previous.copy()
    service = MessageArchiveService(
        ArchiveConfig(
            corp_id="test", archive_secret="test", database_url=f"sqlite:///{tmp_path / 'host.db'}"
        ),
        client=FakeClient(),
    )
    initialize = service._db.initialize

    def check_handlers():
        assert {s: signal.getsignal(s) for s in previous} == previous
        initialize()
        service.stop()

    service._db.initialize = check_handlers

    async def run_with_host_handlers():
        # Python 3.11+ 的 asyncio.run 会临时安装 SIGINT 处理器，在循环内记录宿主状态。
        nonlocal previous
        previous = {s: signal.getsignal(s) for s in previous}
        await service.run(handle_signals=background)

    if background:
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(lambda: asyncio.run(run_with_host_handlers())).result(timeout=10)
    else:
        asyncio.run(run_with_host_handlers())
    # asyncio.run 退出后恢复循环启动前的宿主处理器。
    assert {s: signal.getsignal(s) for s in outside_loop} == outside_loop


def test_startup_failure_restores_signal_handlers(tmp_path):
    previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    client = FakeClient()
    service = MessageArchiveService(
        ArchiveConfig(
            corp_id="test",
            archive_secret="test",
            database_url=f"sqlite:///{tmp_path / 'failure.db'}",
        ),
        client=client,
    )

    def fail():
        raise RuntimeError("startup failed")

    service._db.initialize = fail
    with pytest.raises(RuntimeError, match="startup failed"):
        asyncio.run(service.run())
    assert client.closed
    assert {s: signal.getsignal(s) for s in previous} == previous
