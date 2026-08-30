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
    monkeypatch.setattr("browser_proxy.cdp.connect", lambda _url, **_: websocket)

    result = asyncio.run(CdpBrowser(9222).call("Browser.getVersion", {}))

    assert result == {"product": "Microsoft Edge/140"}
    assert websocket.sent == [{"id": 1, "method": "Browser.getVersion", "params": {}}]


def test_raw_read_only_bypasses_approval_but_mutation_is_approved(monkeypatch, tmp_path) -> None:
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

    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    (tmp_path / "profiles/default").mkdir(parents=True)
    (tmp_path / "profiles/default/Local State").write_text("{}")
    monkeypatch.setenv("BROWSER_PROXY_EDGE_PORT", "9222")
    daemon = Daemon()
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


def test_raw_dispatches_across_all_three_protocol_families(monkeypatch, tmp_path) -> None:
    """`do raw` routes to the correct backend per family: cdp-browser (CdpBrowser.call), cdp-page
    (CdpBrowser.page_session — with an optional multi-call bundle), and ext (extension bridge
    kind chrome.call). Any result shape is returned verbatim — never coerced to a dict."""
    page_calls: list[list[str]] = []
    page_results: list[list[dict[str, Any]]] = []
    ext_kinds: list[str] = []
    browser_calls: list[str] = []

    async def browser_call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del self
        browser_calls.append(method)
        return {"method": method, **params}

    async def browser_page_session(
        self: CdpBrowser, target_id: str, calls: list[tuple[str, Any]]
    ) -> list[dict[str, Any]]:
        del self, target_id
        page_calls.append([method for method, _ in calls])
        page_results.append([{"ok": True} for _ in calls])
        return page_results[-1]

    async def daemon_extension_request(
        self: Daemon, kind: str, payload: dict[str, Any], profile: str
    ) -> dict[str, Any]:
        del self, profile
        ext_kinds.append(kind)
        del payload
        return {"method": "bookmarks.getTree", "result": {"roots": []}}

    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    (tmp_path / "profiles/default").mkdir(parents=True)
    (tmp_path / "profiles/default/Local State").write_text("{}")
    monkeypatch.setenv("BROWSER_PROXY_EDGE_PORT", "9222")
    daemon = Daemon()
    # All raw paths are read-only shape here; force no approval for every family.
    monkeypatch.setattr(Daemon, "_is_raw_read_only", lambda self, protocol, method: True)
    monkeypatch.setattr(CdpBrowser, "call", browser_call)
    monkeypatch.setattr(CdpBrowser, "page_session", browser_page_session)
    monkeypatch.setattr(Daemon, "extension_request", daemon_extension_request)

    async def run() -> tuple[Any, Any, Any, Any]:
        browser_level = await daemon.dispatch(
            RpcRequest(
                id="b",
                method="do",
                params={
                    "action": "raw",
                    "payload": {
                        "profile": "default",
                        "protocol": "cdp-browser",
                        "method": "Browser.getVersion",
                        "params": {},
                    },
                },
            )
        )
        page_level = await daemon.dispatch(
            RpcRequest(
                id="p",
                method="do",
                params={
                    "action": "raw",
                    "payload": {
                        "profile": "default",
                        "protocol": "cdp-page",
                        "target_id": "target-1",
                        "method": "Runtime.evaluate",
                        "params": {"expression": "1+1"},
                    },
                },
            )
        )
        page_bundle = await daemon.dispatch(
            RpcRequest(
                id="pb",
                method="do",
                params={
                    "action": "raw",
                    "payload": {
                        "profile": "default",
                        "protocol": "cdp-page",
                        "target_id": "target-1",
                        "calls": [
                            ["DOM.getDocument", {"depth": -1}],
                            ["DOM.querySelector", {"selector": "h1"}],
                        ],
                    },
                },
            )
        )
        extension_family = await daemon.dispatch(
            RpcRequest(
                id="e",
                method="do",
                params={
                    "action": "raw",
                    "payload": {
                        "profile": "default",
                        "protocol": "ext",
                        "method": "bookmarks.getTree",
                    },
                },
            )
        )
        return browser_level, page_level, page_bundle, extension_family

    browser_level, page_level, page_bundle, extension_family = asyncio.run(run())
    assert browser_level.meta.status == "ok"
    assert page_level.meta.status == "ok"
    assert page_bundle.meta.status == "ok"
    assert extension_family.meta.status == "ok"
    assert browser_calls == ["Browser.getVersion"]
    assert page_calls == [
        ["Runtime.evaluate"],
        ["DOM.getDocument", "DOM.querySelector"],
    ]
    assert ext_kinds == ["chrome.call"]
    # Result shape preserved: a list result stays a list (never coerced to dict).
    assert isinstance(page_bundle.data["result"], list) and len(page_bundle.data["result"]) == 2


def test_raw_rejects_unknown_protocol_and_malformed_call_bundles(monkeypatch, tmp_path) -> None:
    """Unknown protocol values and malformed `calls` bundles fail closed with clear messages —
    never a silent fallback to cdp-browser."""
    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    (tmp_path / "profiles/default").mkdir(parents=True)
    (tmp_path / "profiles/default/Local State").write_text("{}")
    monkeypatch.setenv("BROWSER_PROXY_EDGE_PORT", "9222")
    daemon = Daemon()
    monkeypatch.setattr(Daemon, "_is_raw_read_only", lambda self, protocol, method: True)

    async def run() -> tuple[Any, Any, Any]:
        unknown_proto = await daemon.dispatch(
            RpcRequest(
                id="u",
                method="do",
                params={
                    "action": "raw",
                    "payload": {
                        "profile": "default",
                        "protocol": "nope",
                        "method": "whatever",
                    },
                },
            )
        )
        no_target = await daemon.dispatch(
            RpcRequest(
                id="nt",
                method="do",
                params={
                    "action": "raw",
                    "payload": {
                        "profile": "default",
                        "protocol": "cdp-page",
                        "method": "Runtime.evaluate",
                    },
                },
            )
        )
        bad_bundle = await daemon.dispatch(
            RpcRequest(
                id="bb",
                method="do",
                params={
                    "action": "raw",
                    "payload": {
                        "profile": "default",
                        "protocol": "cdp-page",
                        "target_id": "x",
                        "method": "DOM.getDocument",
                        "calls": ["not-a-pair"],
                    },
                },
            )
        )
        return unknown_proto, no_target, bad_bundle

    unknown_proto, no_target, bad_bundle = asyncio.run(run())
    assert unknown_proto.meta.status == "error"
    assert "unknown protocol" in unknown_proto.data["message"]
    assert no_target.meta.status == "error"
    assert "target_id" in no_target.data["message"]
    assert bad_bundle.meta.status == "error"
    assert "[method, params]" in bad_bundle.data["message"]


def test_registry_covers_full_profile_hierarchy_and_object_payload_contract() -> None:
    """The public action registry names all supported Edge profile resource domains."""
    groups = {action.group for action in REGISTRY.values()}
    assert {"Profiles", "Windows", "Groups", "Tabs", "Bookmarks"} <= groups
    assert "Workspaces" not in groups
    assert REGISTRY["raw"].policy.approval is False


class _SessionWebSocket:
    """Simulate one flattened attach/call/call/detach exchange for ``page_session``."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def __aiter__(self) -> "_SessionWebSocket":
        self._frames = iter(
            [
                json.dumps({"id": 1, "result": {"sessionId": "session-1"}}),
                json.dumps(
                    {"id": 2, "sessionId": "session-1", "result": {"nodes": [], "step": "enable"}}
                ),
                json.dumps(
                    {"id": 3, "sessionId": "session-1", "result": {"nodes": [1], "step": "tree"}}
                ),
                json.dumps({"id": 4, "sessionId": "session-1", "result": {}}),
            ]
        )
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._frames)
        except StopIteration as error:
            raise StopAsyncIteration from error


def test_page_session_uses_flattened_top_level_session_id(monkeypatch) -> None:
    """``page_session`` attaches once and issues N flattened calls sharing one sessionId."""
    websocket = _SessionWebSocket()
    monkeypatch.setattr("browser_proxy.cdp.urlopen", lambda *_args, **_kwargs: _HttpResponse())
    monkeypatch.setattr("browser_proxy.cdp.connect", lambda _url, **_: websocket)

    results = asyncio.run(
        CdpBrowser(9222).page_session(
            "target-1",
            [("Accessibility.enable", {}), ("Accessibility.getFullAXTree", {})],
        )
    )

    assert results == [{"nodes": [], "step": "enable"}, {"nodes": [1], "step": "tree"}]
    assert websocket.sent[0] == {
        "id": 1,
        "method": "Target.attachToTarget",
        "params": {"targetId": "target-1", "flatten": True},
    }
    for frame in websocket.sent[1:3]:
        assert "sessionId" in frame
        assert frame["sessionId"] == "session-1"
    assert websocket.sent[1]["method"] == "Accessibility.enable"
    assert websocket.sent[2]["method"] == "Accessibility.getFullAXTree"
    assert websocket.sent[3] == {
        "id": 4,
        "method": "Target.detachFromTarget",
        "params": {"sessionId": "session-1"},
    }
    assert all("message" not in frame for frame in websocket.sent)


def test_page_session_resolves_callable_params_from_earlier_results_in_the_same_session(
    monkeypatch,
) -> None:
    """A later call's params may be a callable receiving every earlier result IN THIS SAME
    session so far — the fix for a real, live-confirmed bug (KπX, GRAVÉ): resolving a chain (e.g.
    `DOM.querySelector`'s `nodeId` root from a PRIOR `DOM.getDocument`) across separate
    `page_session()` calls silently broke, since each call attaches then detaches a brand-new
    session and DOM-domain `nodeId`s do not survive that. Bundling the whole chain into ONE
    session, with later params resolved from earlier real results, is the fix — this proves the
    resolution happens against the REAL prior result, over the REAL websocket layer."""
    websocket = _SessionWebSocket()
    monkeypatch.setattr("browser_proxy.cdp.urlopen", lambda *_args, **_kwargs: _HttpResponse())
    monkeypatch.setattr("browser_proxy.cdp.connect", lambda _url, **_: websocket)
    seen_prior_step: list[str] = []

    def resolve_from_prior(results: list[dict[str, Any]]) -> dict[str, Any]:
        seen_prior_step.append(results[0]["step"])
        return {"nodeId": results[0]["nodes"]}

    results = asyncio.run(
        CdpBrowser(9222).page_session(
            "target-1",
            [("Accessibility.enable", {}), ("Accessibility.getFullAXTree", resolve_from_prior)],
        )
    )

    assert results == [{"nodes": [], "step": "enable"}, {"nodes": [1], "step": "tree"}]
    # The callable really ran against the REAL first result — never a placeholder/empty list.
    assert seen_prior_step == ["enable"]
    assert websocket.sent[2] == {
        "id": 3,
        "sessionId": "session-1",
        "method": "Accessibility.getFullAXTree",
        "params": {"nodeId": []},
    }
