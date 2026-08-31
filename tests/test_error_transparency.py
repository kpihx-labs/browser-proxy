"""Error-code transparency regression tests (KπX, GRAVÉ: "le code retourne souvent le même code
d'erreur qui n'est pas exact" — live-confirmed the exact night `extension-uninstall`'s REAL
`"chrome.management.uninstall requires a user gesture."` rejection was being silently discarded
and reported as a bare, misleading `EXTENSION_UNAVAILABLE`)."""

import asyncio
import dataclasses
import json

import pytest
from websockets.asyncio.client import connect

from browser_proxy.actions import REGISTRY
from browser_proxy.bridge import ExtensionBridge
from browser_proxy.daemon import Daemon
from browser_proxy.models import RpcRequest


def test_extension_rejection_surfaces_the_real_message_as_its_own_code(
    tmp_path, monkeypatch
) -> None:
    """When the extension IS reachable, receives the request, and explicitly declines it with a
    real reason, the daemon must report `EXTENSION_REJECTED: <that real reason>` — never the
    misleading `EXTENSION_UNAVAILABLE` (the connection was never the problem)."""
    monkeypatch.setenv("BROWSER_PROXY_PERSISTENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BROWSER_PROXY_EXTENSION_PORT", "0")
    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))

    async def run() -> None:
        daemon = Daemon()
        daemon.bridge.pair("shared-pairing-secret-1234567")
        await daemon.bridge.start()
        token = (tmp_path / "state/extension.env").read_text()
        async with connect(f"ws://127.0.0.1:{daemon.bridge.port}") as connection:
            await connection.send(
                json.dumps(
                    {
                        "type": "handshake",
                        "token": token,
                        "extension_id": "edge-test",
                        "profile": "default",
                    }
                )
            )
            assert json.loads(await connection.recv())["status"] == "accepted"
            dispatch_task = asyncio.create_task(
                daemon.dispatch(
                    RpcRequest(
                        id="1",
                        method="do",
                        params={"action": "bookmark-list", "payload": {"profile": "default"}},
                    )
                )
            )
            request_message = json.loads(await connection.recv())
            await connection.send(
                json.dumps(
                    {
                        "type": "response",
                        "id": request_message["id"],
                        "ok": False,
                        "data": {"message": "chrome.management.uninstall requires a user gesture."},
                    }
                )
            )
            result = await dispatch_task
            assert result.meta.status == "error"
            assert result.data["code"] == "EXTENSION_REJECTED"
            assert "requires a user gesture" in result.data["message"]
        await daemon.bridge.stop()

    asyncio.run(run())


def test_dispatch_trusts_every_real_internal_code_instead_of_a_stale_whitelist(
    tmp_path, monkeypatch
) -> None:
    """A RuntimeError shaped as a real internal code (`"SOME_CODE: details"`) must surface AS
    THAT EXACT CODE, even one never explicitly whitelisted here — the previous fixed whitelist
    silently relabeled anything unrecognized back to a misleading `CDP_UNAVAILABLE` the instant a
    new code was introduced elsewhere without this list being remembered too. A genuinely
    unstructured error message (no real code shape at all) still falls back safely."""
    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    daemon = Daemon()

    async def failing_handler(payload: dict, context: object) -> dict:
        del payload, context
        raise RuntimeError("DAEMON_ALREADY_RUNNING: a genuinely different, never-whitelisted code")

    async def unstructured_handler(payload: dict, context: object) -> dict:
        del payload, context
        raise RuntimeError("a raw internal error with no real code shape at all")

    original = REGISTRY["bookmark-list"]
    try:
        REGISTRY["bookmark-list"] = dataclasses.replace(original, handler=failing_handler)
        result = asyncio.run(
            daemon.dispatch(
                RpcRequest(
                    id="1",
                    method="do",
                    params={"action": "bookmark-list", "payload": {"profile": "default"}},
                )
            )
        )
        assert result.meta.status == "error"
        assert result.data["code"] == "DAEMON_ALREADY_RUNNING"

        REGISTRY["bookmark-list"] = dataclasses.replace(original, handler=unstructured_handler)
        fallback = asyncio.run(
            daemon.dispatch(
                RpcRequest(
                    id="2",
                    method="do",
                    params={"action": "bookmark-list", "payload": {"profile": "default"}},
                )
            )
        )
        assert fallback.data["code"] == "CDP_UNAVAILABLE"
    finally:
        REGISTRY["bookmark-list"] = original


def test_disconnect_mid_flight_fails_the_pending_request_immediately_with_the_real_reason(
    tmp_path, monkeypatch
) -> None:
    """A profile's connection dying WHILE one of its requests is still in flight must fail that
    request right away with the REAL reason — never silently hang until the unrelated global
    `timeout_seconds`/`stop()`."""
    monkeypatch.setenv("BROWSER_PROXY_PERSISTENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BROWSER_PROXY_EXTENSION_PORT", "0")

    async def run() -> None:
        bridge = ExtensionBridge(timeout_seconds=30)
        bridge.pair("shared-pairing-secret-1234567")
        await bridge.start()
        token = (tmp_path / "state/extension.env").read_text()
        connection = await connect(f"ws://127.0.0.1:{bridge.port}").__aenter__()
        await connection.send(
            json.dumps(
                {
                    "type": "handshake",
                    "token": token,
                    "extension_id": "edge-test",
                    "profile": "default",
                }
            )
        )
        assert json.loads(await connection.recv())["status"] == "accepted"
        request_task = asyncio.create_task(
            bridge.request("bookmark.list", {"profile": "default"}, "default")
        )
        await asyncio.wait_for(connection.recv(), timeout=1)  # the request itself, never answered
        await connection.close()
        with pytest.raises(RuntimeError, match="EXTENSION_UNAVAILABLE: default"):
            await asyncio.wait_for(request_task, timeout=1)
        # The real reason is recorded and surfaces on the VERY NEXT attempt too, not just this one.
        with pytest.raises(RuntimeError, match=r"EXTENSION_UNAVAILABLE: default \("):
            await bridge.request("bookmark.list", {"profile": "default"}, "default")
        await bridge.stop()

    asyncio.run(run())


def test_extension_timeout_is_a_distinct_code_from_extension_unavailable(
    tmp_path, monkeypatch
) -> None:
    """A connection that stays open but never replies must fail with `EXTENSION_TIMEOUT`, never
    the misleading `EXTENSION_UNAVAILABLE` (the connection is NOT gone — it simply never
    answered)."""
    monkeypatch.setenv("BROWSER_PROXY_PERSISTENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BROWSER_PROXY_EXTENSION_PORT", "0")

    async def run() -> None:
        bridge = ExtensionBridge(timeout_seconds=30)
        bridge.pair("shared-pairing-secret-1234567")
        await bridge.start()
        token = (tmp_path / "state/extension.env").read_text()
        async with connect(f"ws://127.0.0.1:{bridge.port}") as connection:
            await connection.send(
                json.dumps(
                    {
                        "type": "handshake",
                        "token": token,
                        "extension_id": "edge-test",
                        "profile": "default",
                    }
                )
            )
            assert json.loads(await connection.recv())["status"] == "accepted"
            with pytest.raises(TimeoutError, match=r"EXTENSION_TIMEOUT: default \(0.1s\)"):
                await bridge.request(
                    "bookmark.list", {"profile": "default"}, "default", timeout_seconds=0.1
                )
        await bridge.stop()

    asyncio.run(run())
