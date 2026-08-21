"""Authenticated Edge-extension bridge tests."""

import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from browser_proxy.bridge import ExtensionBridge


def test_bridge_rejects_bad_token_and_dispatches_request(tmp_path, monkeypatch) -> None:
    """Only a paired extension can handshake and receive typed daemon requests."""
    monkeypatch.setenv("BROWSER_PROXY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BROWSER_PROXY_EXTENSION_PORT", "0")

    async def run() -> None:
        bridge = ExtensionBridge(timeout_seconds=1)
        bridge.pair()
        await bridge.start()
        async with connect(f"ws://127.0.0.1:{bridge.port}") as rejected:
            await rejected.send(
                json.dumps({"type": "handshake", "token": "wrong", "extension_id": "edge-test"})
            )
            with pytest.raises(ConnectionClosed):
                await rejected.recv()

        token = (tmp_path / "state/extension.token").read_text()
        async with connect(f"ws://127.0.0.1:{bridge.port}") as extension:
            await extension.send(
                json.dumps({"type": "handshake", "token": token, "extension_id": "edge-test"})
            )
            assert json.loads(await extension.recv())["status"] == "accepted"
            request_task = asyncio.create_task(
                bridge.request("bookmark.list", {"profile": "default"})
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
