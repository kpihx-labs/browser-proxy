"""Authenticated Edge-extension bridge tests."""

import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from browser_proxy.bridge import ExtensionBridge


def test_bridge_does_not_create_an_untransferable_secret_implicitly(tmp_path, monkeypatch) -> None:
    """Pairing stays explicitly operator-driven when no local token exists yet."""
    monkeypatch.setenv("BROWSER_PROXY_PERSISTENT_STATE_DIR", str(tmp_path / "state"))
    bridge = ExtensionBridge()
    assert bridge._token() == ""
    assert not (tmp_path / "state/extension.token").exists()


def test_bridge_pair_persists_the_operator_provided_secret(tmp_path, monkeypatch) -> None:
    """The daemon stores exactly the secret generated visibly in extension settings."""
    monkeypatch.setenv("BROWSER_PROXY_PERSISTENT_STATE_DIR", str(tmp_path / "state"))
    secret = "extension-generated-pairing-secret"
    bridge = ExtensionBridge()
    bridge.pair(secret)
    assert bridge._token() == secret


def test_bridge_pair_persists_across_a_simulated_ephemeral_runtime_wipe(
    tmp_path, monkeypatch
) -> None:
    """The pairing secret survives a `runtime_dir()` wipe — it never lived there (regression guard
    for the exact bug that forced perpetual re-pairing: the secret used to live in tmpfs)."""
    monkeypatch.setenv("BROWSER_PROXY_PERSISTENT_STATE_DIR", str(tmp_path / "persistent-state"))
    monkeypatch.setenv("BROWSER_PROXY_STATE_DIR", str(tmp_path / "runtime"))
    secret = "extension-generated-pairing-secret"
    ExtensionBridge().pair(secret)
    assert (tmp_path / "persistent-state/extension.token").exists()
    assert not (tmp_path / "runtime").exists()
    # Simulate the runtime dir (tmpfs) being wiped by a reboot/logout — the secret must survive.
    (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
    for entry in (tmp_path / "runtime").iterdir():
        entry.unlink()
    assert ExtensionBridge()._token() == secret


def test_bridge_pair_rejects_non_ascii_secret(tmp_path, monkeypatch) -> None:
    """A malformed pairing secret cannot crash later constant-time authentication."""
    monkeypatch.setenv("BROWSER_PROXY_PERSISTENT_STATE_DIR", str(tmp_path / "state"))
    with pytest.raises(ValueError, match="ASCII"):
        ExtensionBridge().pair("non-ascii-secret-é-123456")


def test_bridge_rejects_bad_token_and_dispatches_request(tmp_path, monkeypatch) -> None:
    """Only a paired extension can handshake and receive typed daemon requests."""
    monkeypatch.setenv("BROWSER_PROXY_PERSISTENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BROWSER_PROXY_EXTENSION_PORT", "0")

    async def run() -> None:
        bridge = ExtensionBridge(timeout_seconds=1)
        bridge.pair("test-pairing-secret-123456")
        await bridge.start()
        async with connect(f"ws://127.0.0.1:{bridge.port}") as rejected:
            await rejected.send(
                json.dumps(
                    {
                        "type": "handshake",
                        "token": "wrong",
                        "extension_id": "edge-test",
                        "profile": "default",
                    }
                )
            )
            with pytest.raises(ConnectionClosed):
                await rejected.recv()

        token = (tmp_path / "state/extension.token").read_text()
        async with connect(f"ws://127.0.0.1:{bridge.port}") as extension:
            await extension.send(
                json.dumps(
                    {
                        "type": "handshake",
                        "token": token,
                        "extension_id": "edge-test",
                        "profile": "default",
                    }
                )
            )
            assert json.loads(await extension.recv())["status"] == "accepted"
            assert bridge.is_connected("default")
            assert bridge.connected_profiles() == ("default",)
            request_task = asyncio.create_task(
                bridge.request("bookmark.list", {"profile": "default"}, "default")
            )
            message = json.loads(await extension.recv())
            assert message["type"] == "request"
            await extension.send(
                json.dumps(
                    {
                        "type": "response",
                        "id": message["id"],
                        "ok": True,
                        "data": {"bookmarks": []},
                    }
                )
            )
            assert await request_task == {"ok": True, "data": {"bookmarks": []}}
        await bridge.stop()

    asyncio.run(run())


def test_two_profiles_stay_isolated_and_are_never_answered_by_the_wrong_extension(
    tmp_path, monkeypatch
) -> None:
    """Regression guard: 3 profiles used to return the exact same bookmark tree because only ONE
    global connection slot existed. Two profiles connecting concurrently must be routed to their
    own extension, and a request for an unconnected profile must fail closed by name, never
    silently fall through to a different profile's connection."""
    monkeypatch.setenv("BROWSER_PROXY_PERSISTENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BROWSER_PROXY_EXTENSION_PORT", "0")

    async def run() -> None:
        bridge = ExtensionBridge(timeout_seconds=1)
        bridge.pair("shared-pairing-secret-1234567")
        await bridge.start()
        token = (tmp_path / "state/extension.token").read_text()

        async def handshake(profile: str):
            connection = await connect(f"ws://127.0.0.1:{bridge.port}").__aenter__()
            await connection.send(
                json.dumps(
                    {
                        "type": "handshake",
                        "token": token,
                        "extension_id": "edge-test",
                        "profile": profile,
                    }
                )
            )
            assert json.loads(await connection.recv())["status"] == "accepted"
            return connection

        default_conn = await handshake("default")
        research_conn = await handshake("research")
        assert bridge.connected_profiles() == ("default", "research")
        assert bridge.is_connected("default")
        assert bridge.is_connected("research")
        assert not bridge.is_connected("smoke")

        with pytest.raises(RuntimeError, match="EXTENSION_UNAVAILABLE: smoke"):
            await bridge.request("bookmark.list", {"profile": "smoke"}, "smoke")

        request_task = asyncio.create_task(
            bridge.request("bookmark.list", {"profile": "research"}, "research")
        )
        # The "default" extension must never see a request addressed to "research".
        default_recv = asyncio.create_task(asyncio.wait_for(default_conn.recv(), timeout=0.2))
        research_message = json.loads(await research_conn.recv())
        assert research_message["type"] == "request"
        with pytest.raises(TimeoutError):
            await default_recv
        await research_conn.send(
            json.dumps(
                {
                    "type": "response",
                    "id": research_message["id"],
                    "ok": True,
                    "data": {"bookmarks": ["research-only"]},
                }
            )
        )
        assert await request_task == {"ok": True, "data": {"bookmarks": ["research-only"]}}

        await default_conn.close()
        await research_conn.close()
        await bridge.stop()

    asyncio.run(run())
