"""Contract, policy, and real Unix-socket daemon integration tests."""

import asyncio
from pathlib import Path

from browser_proxy.actions import REGISTRY, validate_registry
from browser_proxy.client import request
from browser_proxy.daemon import Daemon
from browser_proxy.models import RpcRequest


def test_registry_covers_all_required_edge_domains() -> None:
    """The registry documents profiles, windows, tabs, workspaces, bookmarks, and raw CDP."""
    validate_registry()
    assert {
        "profile-list",
        "window-create",
        "tab-list",
        "page-list",
        "workspace-list",
        "bookmark-list",
        "raw",
    } <= set(REGISTRY)
    assert not REGISTRY["raw"].policy.approval
    assert REGISTRY["window-create"].policy.approval


def test_readiness_does_not_require_edge() -> None:
    """Ping is available before Edge, profiles, or the extension are connected."""
    result = asyncio.run(Daemon().dispatch(RpcRequest(id="1", method="ping")))
    assert result.meta.status == "ok"
    assert result.data["ready"] is True
    assert result.data["profiles"] == {}


def test_raw_mutation_requires_the_extension_before_cdp() -> None:
    """Mutating raw CDP methods fail closed before profile or CDP access without approval."""
    result = asyncio.run(
        Daemon().dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "raw",
                    "payload": {"profile": "default", "method": "Target.createTarget"},
                },
            )
        )
    )
    assert result.data["code"] == "EXTENSION_UNAVAILABLE"


def test_socket_roundtrip_uses_real_client_transport(tmp_path: Path, monkeypatch) -> None:
    """An isolated daemon accepts a real client request over its Unix-domain socket."""
    monkeypatch.setenv("BROWSER_PROXY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("BROWSER_PROXY_EXTENSION_PORT", "39391")

    async def run() -> None:
        daemon = Daemon(idle_seconds=30, max_lifetime_seconds=30)
        task = asyncio.create_task(daemon.serve())
        for _ in range(50):
            if (tmp_path / "state/browser-proxy.sock").exists():
                break
            await asyncio.sleep(0.02)
        response = await request("do", {"action": "profile-list", "payload": {}})
        assert response.meta.status == "ok"
        assert response.data == {"profiles": []}
        await request("shutdown", {})
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())
    assert not (tmp_path / "state/browser-proxy.sock").exists()
    assert not (tmp_path / "state/browser-proxy.lock").exists()
