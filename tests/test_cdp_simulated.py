"""Simulated direct-CDP and registry policy tests without a browser UI."""

import asyncio
import json
from typing import Any, Self

from browser_proxy.actions import REGISTRY
from browser_proxy.cdp import CdpBrowser
from browser_proxy.daemon import Daemon
from browser_proxy.models import RpcRequest


class _HttpResponse:
    """Provide a context-manager-shaped fake HTTP response for CDP discovery."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, *_: object) -> bytes:
        return b'{"webSocketDebuggerUrl":"ws://127.0.0.1:9222/devtools/browser/test"}'


class _WebSocket:
    """Capture one mocked CDP request and yield unrelated then matching responses."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def __aiter__(self) -> "_WebSocket":
        self._frames = iter(
            [
                json.dumps({"method": "Target.targetCreated", "params": {}}),
                json.dumps({"id": 1, "result": {"product": "Microsoft Edge/140"}}),
            ]
        )
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._frames)
        except StopIteration as error:
            raise StopAsyncIteration from error


def test_direct_cdp_call_uses_http_discovery_and_websocket_request(monkeypatch) -> None:
    """A mocked HTTP/WebSocket CDP exchange preserves request and response semantics."""
    websocket = _WebSocket()
    monkeypatch.setattr("browser_proxy.cdp.urlopen", lambda *_args, **_kwargs: _HttpResponse())
    monkeypatch.setattr("browser_proxy.cdp.connect", lambda _url: websocket)

    result = asyncio.run(CdpBrowser(9222).call("Browser.getVersion", {}))

    assert result == {"product": "Microsoft Edge/140"}
    assert websocket.sent == [{"id": 1, "method": "Browser.getVersion", "params": {}}]


def test_raw_read_only_bypasses_approval_but_mutation_is_approved(monkeypatch) -> None:
    """Read-only raw CDP is direct; a mutation cannot reach CDP before approval."""
    approvals: list[dict[str, Any]] = []
    calls: list[str] = []

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(method)
        return {"method": method, **params}

    async def approve(
        self: Daemon, action: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str, bool]:
        approvals.append({"action": action, "payload": payload})
        return payload, "approved in test", False

    daemon = Daemon()
    daemon.profiles["default"] = 9222
    monkeypatch.setattr(CdpBrowser, "call", call)
    monkeypatch.setattr(Daemon, "_approve", approve)

    async def run() -> tuple[Any, Any]:
        read = await daemon.dispatch(
            RpcRequest(
                id="read",
                method="do",
                params={
                    "action": "raw",
                    "payload": {"profile": "default", "method": "Browser.getVersion", "params": {}},
                },
            )
        )
        mutation = await daemon.dispatch(
            RpcRequest(
                id="write",
                method="do",
                params={
                    "action": "raw",
                    "payload": {
                        "profile": "default",
                        "method": "Target.createTarget",
                        "params": {"url": "https://example.test"},
                    },
                },
            )
        )
        return read, mutation

    read, mutation = asyncio.run(run())
    assert read.meta.status == mutation.meta.status == "ok"
    assert approvals == [
        {
            "action": "raw",
            "payload": {
                "profile": "default",
                "method": "Target.createTarget",
                "params": {"url": "https://example.test"},
            },
        }
    ]
    assert calls == ["Browser.getVersion", "Target.createTarget"]


def test_registry_covers_full_profile_hierarchy_and_object_payload_contract() -> None:
    """The public action registry names all supported Edge profile resource domains."""
    groups = {action.group for action in REGISTRY.values()}
    assert {"Profiles", "Workspaces", "Windows", "Groups", "Tabs", "Bookmarks"} <= groups
    assert REGISTRY["raw"].policy.approval is False
