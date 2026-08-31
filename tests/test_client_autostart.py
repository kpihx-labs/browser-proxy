"""Client auto-start race-safety tests — no real socket, no real systemctl involved."""

import asyncio
import struct
from typing import Any

from browser_proxy import client


def _framed(payload: bytes) -> bytes:
    """Purpose: build one length-prefixed frame identical to the real `ipc.write_message` wire shape."""
    return struct.pack("!Q", len(payload)) + payload


_DEFAULT_ENVELOPE = b'{"meta":{"status":"ok","comment":"","edited":false},"data":{}}'


class _FakeCompletedProcess:
    """Purpose: stand in for `subprocess.CompletedProcess` without spawning any real process."""

    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _FakeReader:
    """Purpose: hand back one valid framed `Envelope` response, matching the daemon's real
    length-prefixed wire shape (see `browser_proxy.ipc`)."""

    def __init__(self, payload: bytes = _DEFAULT_ENVELOPE) -> None:
        self._buffer = _framed(payload)
        self._pos = 0

    async def readexactly(self, n: int) -> bytes:
        end = self._pos + n
        if end > len(self._buffer):
            raise asyncio.IncompleteReadError(self._buffer[self._pos :], end - self._pos)
        chunk = self._buffer[self._pos : end]
        self._pos = end
        return chunk


class _FakeWriter:
    """Purpose: swallow every write, matching `asyncio.StreamWriter`'s awaited-close contract."""

    def write(self, data: bytes) -> None:
        del data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


def test_request_does_not_start_daemon_while_unit_is_still_deactivating(monkeypatch) -> None:
    """Regression guard (KπX root-caused live): while `systemctl --user is-active` reports
    `deactivating` (an in-flight async `admin service stop` teardown), the client must NOT issue a
    redundant `systemctl start` — doing so raced the OLD daemon's own socket unlink/rebind, leaving
    the socket PATH pointing at an orphaned, unlistened inode (`ECONNREFUSED` despite `systemctl
    status` reporting the unit "active"). Only once the unit genuinely settles (`inactive`) does
    exactly one real `start` fire."""
    states = iter(["deactivating", "deactivating", "inactive"])
    start_calls: list[list[str]] = []
    connect_attempts = {"count": 0}

    def fake_run(args: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        if "is-active" in args:
            return _FakeCompletedProcess(next(states, "inactive"))
        start_calls.append(list(args))
        return _FakeCompletedProcess()

    async def fake_open_unix_connection(path: str) -> tuple[_FakeReader, _FakeWriter]:
        connect_attempts["count"] += 1
        if connect_attempts["count"] < 4:
            raise OSError("not ready yet")
        return _FakeReader(), _FakeWriter()

    monkeypatch.setattr(client.subprocess, "run", fake_run)
    monkeypatch.setattr(client.asyncio, "open_unix_connection", fake_open_unix_connection)

    result = asyncio.run(client.request("ping", {}))

    assert result.meta.status == "ok"
    # Exactly one real `start`, issued only once the unit genuinely settled — never one per
    # `deactivating` poll, never zero (the unit does eventually need a real start once idle).
    assert start_calls == [["systemctl", "--user", "start", "browser-proxy.service"]]


def test_request_still_starts_daemon_immediately_on_a_clean_cold_start(monkeypatch) -> None:
    """The race-safety fix above must not regress the ordinary cold-start case: when the unit is
    genuinely idle (not mid-teardown), the client still issues exactly one `systemctl start` on
    the very first connection failure, same as before this fix."""
    start_calls: list[list[str]] = []
    connect_attempts = {"count": 0}

    def fake_run(args: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        if "is-active" in args:
            return _FakeCompletedProcess("inactive")
        start_calls.append(list(args))
        return _FakeCompletedProcess()

    async def fake_open_unix_connection(path: str) -> tuple[_FakeReader, _FakeWriter]:
        connect_attempts["count"] += 1
        if connect_attempts["count"] < 2:
            raise OSError("not ready yet")
        return _FakeReader(), _FakeWriter()

    monkeypatch.setattr(client.subprocess, "run", fake_run)
    monkeypatch.setattr(client.asyncio, "open_unix_connection", fake_open_unix_connection)

    result = asyncio.run(client.request("ping", {}))

    assert result.meta.status == "ok"
    assert start_calls == [["systemctl", "--user", "start", "browser-proxy.service"]]


def test_request_gives_up_after_bounded_retries_if_the_unit_never_settles(monkeypatch) -> None:
    """If the daemon never becomes connectable (e.g. a genuinely hung unit), the client still
    fails closed with `DAEMON_UNAVAILABLE` within the existing bounded retry budget — the
    settling-state check must never turn the retry loop unbounded."""

    def fake_run(args: list[str], **kwargs: Any) -> _FakeCompletedProcess:
        if "is-active" in args:
            return _FakeCompletedProcess("deactivating")
        return _FakeCompletedProcess()

    async def fake_open_unix_connection(path: str) -> tuple[_FakeReader, _FakeWriter]:
        raise OSError("never ready")

    real_sleep = asyncio.sleep
    monkeypatch.setattr(client.subprocess, "run", fake_run)
    monkeypatch.setattr(client.asyncio, "open_unix_connection", fake_open_unix_connection)
    monkeypatch.setattr(client.asyncio, "sleep", lambda _seconds: real_sleep(0))

    result = asyncio.run(client.request("ping", {}))

    assert result.meta.status == "error"
    assert result.data["code"] == "DAEMON_UNAVAILABLE"
