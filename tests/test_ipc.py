"""Length-prefixed IPC framing tests — real Unix sockets for transport-level proof, plus pure
unit-level bound/error-path checks. Regression coverage for the reported bug: a single JSON line
terminated by ``\\n``, read via ``readline()``, silently capped every client<->daemon message at
asyncio's internal ~64 KiB line buffer, surfaced as the raw wire error ``Separator is found, but
chunk is longer than limit`` on any genuinely large single-page result (e.g. ``page-snapshot``'s
full accessibility tree)."""

import asyncio
import struct

import pytest

from browser_proxy import client, ipc
from browser_proxy.daemon import Daemon
from browser_proxy.models import Envelope
from browser_proxy.paths import socket_path


def test_max_message_bytes_is_configurable(monkeypatch) -> None:
    """The ceiling is resolved fresh from the environment at call time, never frozen at import."""
    monkeypatch.delenv("BROWSER_PROXY_IPC_MAX_MESSAGE_BYTES", raising=False)
    assert ipc.max_message_bytes() > 0
    monkeypatch.setenv("BROWSER_PROXY_IPC_MAX_MESSAGE_BYTES", "123456")
    assert ipc.max_message_bytes() == 123456


def test_max_message_bytes_never_goes_below_one(monkeypatch) -> None:
    """A misconfigured zero/negative override can never silently disable the bound entirely."""
    monkeypatch.setenv("BROWSER_PROXY_IPC_MAX_MESSAGE_BYTES", "0")
    assert ipc.max_message_bytes() == 1


async def _echo_once(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Read one framed message and immediately write it back, unchanged."""
    message = await ipc.read_message(reader)
    await ipc.write_message(writer, message)
    writer.close()
    await writer.wait_closed()


def test_round_trip_over_a_real_socket_carries_a_message_far_larger_than_the_old_line_limit(
    tmp_path,
) -> None:
    """A REAL Unix socket, not an in-memory fake: the reported failure only ever reproduced over
    a genuine OS-buffered stream, so the regression guard must exercise one too. 200_000 bytes
    comfortably exceeds asyncio's ~64 KiB line-buffer ceiling that used to truncate
    ``page-snapshot``-sized payloads before this fix."""
    socket_file = tmp_path / "ipc-round-trip.sock"
    payload = b"x" * 200_000

    async def run() -> bytes:
        server = await asyncio.start_unix_server(_echo_once, path=str(socket_file))
        async with server:
            reader, writer = await asyncio.open_unix_connection(str(socket_file))
            await ipc.write_message(writer, payload)
            echoed = await ipc.read_message(reader)
            writer.close()
            await writer.wait_closed()
            return echoed

    assert asyncio.run(run()) == payload


def test_oversized_message_is_rejected_before_the_body_is_ever_read(monkeypatch) -> None:
    """A length prefix beyond the configured ceiling raises immediately — the body is never
    read/allocated, defending against a malformed or hostile prefix. Asserted with a fake stream
    that raises if its body is ever requested, so the guarantee is checked directly, not just
    inferred from a passing/failing test."""
    monkeypatch.setenv("BROWSER_PROXY_IPC_MAX_MESSAGE_BYTES", "10")

    class _OversizeReader:
        async def readexactly(self, n: int) -> bytes:
            if n == 8:
                return struct.pack("!Q", 10**9)
            raise AssertionError("body must never be read once the length exceeds the ceiling")

    async def run() -> None:
        with pytest.raises(ValueError, match="exceeds the 10-byte limit"):
            await ipc.read_message(_OversizeReader())  # type: ignore[arg-type]

    asyncio.run(run())


def test_connection_closed_before_a_full_prefix_raises_connection_error() -> None:
    """A peer that closes mid-header surfaces a clear `ConnectionError`, never a raw
    `asyncio.IncompleteReadError` leaking out of this module's abstraction."""

    class _EmptyReader:
        async def readexactly(self, n: int) -> bytes:
            raise asyncio.IncompleteReadError(b"", n)

    async def run() -> None:
        with pytest.raises(ConnectionError, match="length prefix"):
            await ipc.read_message(_EmptyReader())  # type: ignore[arg-type]

    asyncio.run(run())


def test_connection_closed_mid_body_raises_connection_error() -> None:
    """A peer that closes after announcing a length but before sending the full body also
    surfaces a clear `ConnectionError`."""

    class _TruncatedReader:
        def __init__(self) -> None:
            self._calls = 0

        async def readexactly(self, n: int) -> bytes:
            self._calls += 1
            if self._calls == 1:
                return struct.pack("!Q", 100)
            raise asyncio.IncompleteReadError(b"short", n)

    async def run() -> None:
        with pytest.raises(ConnectionError, match="full message"):
            await ipc.read_message(_TruncatedReader())  # type: ignore[arg-type]

    asyncio.run(run())


def test_client_request_round_trips_a_response_far_larger_than_the_old_line_limit(
    tmp_path, monkeypatch
) -> None:
    """End-to-end regression guard for the literal reported bug, through the REAL production
    path (`client.request` -> `Daemon._handle`), not an isolated echo: a response body far larger
    than asyncio's old ~64 KiB line-buffer ceiling — the exact shape of a full ``page-snapshot``
    accessibility tree — must survive the round trip completely intact."""
    monkeypatch.setenv("BROWSER_PROXY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BROWSER_PROXY_EXTENSION_PORT", "0")
    large_text = "n" * 500_000

    async def fake_dispatch(request: object) -> Envelope:
        del request
        return Envelope.ok({"nodes": large_text})

    async def run() -> Envelope:
        daemon_instance = Daemon()
        daemon_instance.dispatch = fake_dispatch  # type: ignore[method-assign]
        socket_path().parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        server = await asyncio.start_unix_server(daemon_instance._handle, path=str(socket_path()))
        async with server:
            return await client.request("do", {"action": "page-snapshot", "payload": {}})

    result = asyncio.run(run())
    assert result.meta.status == "ok"
    assert result.data["nodes"] == large_text
