"""Daemon lock-race and explicit-stop-only lifecycle tests using local Unix sockets only."""

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
        first = Daemon()
        task = asyncio.create_task(first.serve())
        await _wait_for(socket_path())
        with pytest.raises(RuntimeError, match="DAEMON_ALREADY_RUNNING"):
            await Daemon().serve()
        first._stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())
    assert not socket_path().exists()
    assert not lock_path().exists()


def test_daemon_never_stops_on_its_own_no_matter_how_long_it_waits() -> None:
    """No idle TTL, no maximum lifetime (KπX directive): `_await_explicit_stop()` must never
    resolve on its own — only an explicit `admin service stop`/`shutdown` RPC setting `_stop` can end it.
    Regression guard for the exact bug that killed the whole daemon (CDP included) after an idle
    TTL resumed the instant the extension bridge merely dropped for an unrelated reason."""

    async def run() -> None:
        daemon = Daemon()
        task = asyncio.create_task(daemon._await_explicit_stop())
        await asyncio.sleep(0.5)
        assert not task.done()
        daemon._stop.set()
        with pytest.raises(RuntimeError, match="DAEMON_STOP"):
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())


def test_daemon_constructor_takes_no_lifecycle_configuration() -> None:
    """`Daemon()` exposes no `idle_seconds`/`max_lifetime_seconds` at all — removed entirely,
    never merely defaulted to a huge number, so there is nothing left to silently misconfigure."""
    daemon = Daemon()
    assert not hasattr(daemon, "idle_seconds")
    assert not hasattr(daemon, "max_lifetime_seconds")
    assert not hasattr(daemon, "_last_work")
