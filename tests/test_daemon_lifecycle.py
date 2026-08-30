"""Daemon lock-race and idle/lifetime lifecycle tests using local Unix sockets only."""

import asyncio
from pathlib import Path

import pytest

from browser_proxy.daemon import Daemon
from browser_proxy.paths import lock_path, socket_path


async def _wait_for(path: Path) -> None:
    """Wait briefly until a daemon-created local runtime path exists."""
    for _ in range(100):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


def test_concurrent_daemons_fail_closed_on_lock_and_cleanup(tmp_path, monkeypatch) -> None:
    """Only one daemon owns a runtime directory during a startup race."""
    monkeypatch.setenv("BROWSER_PROXY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BROWSER_PROXY_EXTENSION_PORT", "0")

    async def run() -> None:
        first = Daemon(idle_seconds=30, max_lifetime_seconds=30)
        task = asyncio.create_task(first.serve())
        await _wait_for(socket_path())
        with pytest.raises(RuntimeError, match="DAEMON_ALREADY_RUNNING"):
            await Daemon(idle_seconds=30, max_lifetime_seconds=30).serve()
        first._stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())
    assert not socket_path().exists()
    assert not lock_path().exists()


def test_idle_and_lifetime_signals_stop_without_client_activity() -> None:
    """Idle and hard lifetime watchers raise the internal stop signal deterministically."""

    async def run() -> None:
        idle = Daemon(idle_seconds=0.01, max_lifetime_seconds=30)
        lifetime = Daemon(idle_seconds=30, max_lifetime_seconds=0.01)
        with pytest.raises(RuntimeError, match="DAEMON_STOP"):
            await idle._lifecycle()
        with pytest.raises(RuntimeError, match="DAEMON_STOP"):
            await lifetime._lifecycle()

    asyncio.run(run())


def test_idle_timeout_is_suspended_while_an_extension_stays_connected() -> None:
    """A paired, idle-but-connected extension must never be force-disconnected by the idle TTL
    (regression guard for the exact bug that caused frequent, unexplained bridge drops)."""

    async def run() -> None:
        daemon = Daemon(idle_seconds=0.05, max_lifetime_seconds=30)
        daemon.bridge._connections["default"] = object()  # type: ignore[assignment]
        task = asyncio.create_task(daemon._lifecycle())
        await asyncio.sleep(0.3)
        assert not task.done()
        daemon.bridge._connections.clear()
        with pytest.raises(RuntimeError, match="DAEMON_STOP"):
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())


def test_max_lifetime_still_applies_while_an_extension_stays_connected() -> None:
    """The hard lifetime cap is never suspended, even with a connected extension."""

    async def run() -> None:
        daemon = Daemon(idle_seconds=30, max_lifetime_seconds=0.05)
        daemon.bridge._connections["default"] = object()  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="DAEMON_STOP"):
            await daemon._lifecycle()

    asyncio.run(run())
