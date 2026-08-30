"""Registry-membership and simulated dispatch tests for the restored page-control surface."""

import asyncio
from typing import Any

from browser_proxy.actions import REGISTRY
from browser_proxy.cdp import CdpBrowser
from browser_proxy.daemon import Daemon
from browser_proxy.models import RpcRequest
from browser_proxy.paths import edge_profile_dir, materialize_edge_profile

NEW_ACTIONS = (
    "page-navigate",
    "page-reload",
    "page-back",
    "page-forward",
    "page-click",
    "page-hover",
    "page-type",
    "page-fill-form",
    "page-select-option",
    "page-scroll",
    "page-evaluate",
    "page-snapshot",
    "page-screenshot",
    "page-query",
    "page-console-list",
    "page-network-list",
    "page-dialog-policy",
    "page-set-download-behavior",
    "cookie-list",
    "cookie-set",
    "cookie-remove",
    "storage-local-get",
    "storage-local-set",
    "group-create",
    "group-update",
    "group-move",
    "browser-ask-user",
    "browser-dismiss-overlays",
    "browser-solve-captcha",
    "browser-set-date",
    "browser-set-combobox",
    "browser-drop-file",
    "browser-get-new-tab",
)


def test_all_new_page_actions_are_registered() -> None:
    """Every new action name from the restored page-control surface exists in REGISTRY."""
    assert set(NEW_ACTIONS) <= set(REGISTRY)
    assert len(REGISTRY) == 50  # +1 for profile-remove (profile lifecycle refonte)


def _daemon_with_profile(tmp_path, monkeypatch) -> Daemon:
    """Build a Daemon instance with one pre-started, Edge-initialized fake profile for dispatch
    tests — writes the same ``Local State`` marker a real Edge boot would, so `_profile()`'s
    ``edge_profile_state()`` check accepts it (a bare `mkdir` alone is only "declared")."""
    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    monkeypatch.setenv("BROWSER_PROXY_EDGE_PORT", "9222")
    materialize_edge_profile("default")
    (edge_profile_dir("default") / "Local State").write_text("{}")
    return Daemon()


def test_window_list_groups_tabs_by_their_real_window_id(monkeypatch, tmp_path) -> None:
    """Regression guard: `window-list` used to be byte-identical to `tab-list` — a flat target
    list with zero window grouping — because `Target.getTargets` alone carries no `windowId`.
    Each tab must now be grouped under the REAL window `Browser.getWindowForTarget` reports."""

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return [
            {"targetId": "t1", "type": "page", "title": "A"},
            {"targetId": "t2", "type": "page", "title": "B"},
            {"targetId": "t3", "type": "page", "title": "C"},
        ]

    window_for_target = {
        "t1": {"windowId": 100, "bounds": {"left": 0, "top": 0, "width": 800, "height": 600}},
        "t2": {"windowId": 100, "bounds": {"left": 0, "top": 0, "width": 800, "height": 600}},
        "t3": {"windowId": 200, "bounds": {"left": 900, "top": 0, "width": 800, "height": 600}},
    }

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "Browser.getWindowForTarget"
        return window_for_target[params["targetId"]]

    monkeypatch.setattr(CdpBrowser, "targets", targets)
    monkeypatch.setattr(CdpBrowser, "call", call)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={"action": "window-list", "payload": {"profile": "default"}},
            )
        )
    )

    windows = result.data["windows"]
    assert [w["window_id"] for w in windows] == [100, 200]
    assert [t["targetId"] for t in windows[0]["tabs"]] == ["t1", "t2"]
    assert [t["targetId"] for t in windows[1]["tabs"]] == ["t3"]
    assert windows[1]["bounds"] == {"left": 900, "top": 0, "width": 800, "height": 600}


def test_tab_list_stays_flat_and_independent_from_window_grouping(monkeypatch, tmp_path) -> None:
    """`tab-list` must never depend on `window-list`'s now-grouped shape — it lists targets
    directly via the shared `_page_targets()` helper."""

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return [
            {"targetId": "t1", "type": "page", "title": "A"},
            {"targetId": "t2", "type": "page", "title": "B"},
        ]

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("tab-list must never call Browser.getWindowForTarget")

    monkeypatch.setattr(CdpBrowser, "targets", targets)
    monkeypatch.setattr(CdpBrowser, "call", call)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={"action": "tab-list", "payload": {"profile": "default"}},
            )
        )
    )
    assert [t["targetId"] for t in result.data["tabs"]] == ["t1", "t2"]


def test_window_create_builds_ordered_tab_and_group_items(monkeypatch, tmp_path) -> None:
    """`items` builds a whole tab/group layout in one call, in the exact given order, using the
    window's REAL window_id for every subsequent tab and REAL captured chrome tab ids for
    grouping — never a CDP target_id string passed to chrome.tabs.group by mistake."""
    created_targets: list[dict[str, Any]] = []
    next_target_id = iter(f"target-{i}" for i in range(1, 10))
    next_chrome_tab_id = iter(range(101, 110))

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "Target.createTarget":
            target_id = next(next_target_id)
            created_targets.append({"target_id": target_id, "params": dict(params)})
            return {"targetId": target_id}
        if method == "Browser.getWindowForTarget":
            return {"windowId": 999, "bounds": {"left": 0, "top": 0, "width": 800, "height": 600}}
        raise AssertionError(f"unexpected CDP method {method}")

    extension_calls: list[tuple[str, dict[str, Any]]] = []

    async def extension_request(
        self: Daemon, kind: str, payload: dict[str, Any], profile: str
    ) -> dict[str, Any]:
        extension_calls.append((kind, payload))
        if kind == "tab.capture_next":
            return {"tab_id": next(next_chrome_tab_id)}
        if kind == "group.create":
            return {"group_id": 55}
        raise AssertionError(f"unexpected extension kind {kind}")

    monkeypatch.setattr(CdpBrowser, "call", call)
    monkeypatch.setattr(Daemon, "extension_request", extension_request)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "window-create",
                    "payload": {
                        "profile": "default",
                        "url": "https://a.example",
                        "items": [
                            {"type": "tab", "url": "https://b.example"},
                            {
                                "type": "group",
                                "title": "Research",
                                "color": "blue",
                                "tabs": ["https://c.example", "https://d.example"],
                            },
                            {"type": "tab", "url": "https://e.example"},
                        ],
                    },
                },
            )
        )
    )

    assert result.meta.status == "ok"
    data = result.data
    assert data["window_id"] == 999
    items = data["items"]
    assert [entry["type"] for entry in items] == ["tab", "group", "tab"]
    assert items[0]["url"] == "https://b.example"
    assert items[2]["url"] == "https://e.example"
    group_entry = items[1]
    assert group_entry["title"] == "Research"
    assert [tab["chrome_tab_id"] for tab in group_entry["tabs"]] == [101, 102]
    assert group_entry["group"] == {"group_id": 55}
    # Every Target.createTarget after the window's own initial tab carries the real window_id.
    assert all(target["params"].get("windowId") == 999 for target in created_targets[1:])
    # group.create receives real chrome tab ids, never CDP target_id strings.
    group_calls = [payload for kind, payload in extension_calls if kind == "group.create"]
    assert group_calls == [
        {"profile": "default", "tab_ids": [101, 102], "title": "Research", "color": "blue"}
    ]


def test_window_create_rejects_a_malformed_items_entry(monkeypatch, tmp_path) -> None:
    """An invalid `items` shape fails closed with VALIDATION_ERROR before creating anything odd."""

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "Target.createTarget":
            return {"targetId": "target-1"}
        if method == "Browser.getWindowForTarget":
            return {"windowId": 999, "bounds": {}}
        raise AssertionError(f"unexpected CDP method {method}")

    monkeypatch.setattr(CdpBrowser, "call", call)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "window-create",
                    "payload": {
                        "profile": "default",
                        "url": "https://a.example",
                        "items": [{"type": "unknown-kind"}],
                    },
                },
            )
        )
    )

    assert result.meta.status == "error"
    assert result.data["code"] == "VALIDATION_ERROR"


def test_page_evaluate_runs_read_only_without_approval(monkeypatch, tmp_path) -> None:
    """``page-evaluate`` issues one flattened Runtime.evaluate call with no approval gate."""
    calls: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = []

    async def page_session(
        self: CdpBrowser, target_id: str, session_calls: list[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        calls.append((target_id, session_calls))
        return [{"result": {"value": 42}}]

    monkeypatch.setattr(CdpBrowser, "page_session", page_session)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "page-evaluate",
                    "payload": {
                        "profile": "default",
                        "target_id": "target-1",
                        "expression": "1 + 41",
                    },
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert result.data["result"] == 42
    assert calls == [
        (
            "target-1",
            [
                (
                    "Runtime.evaluate",
                    {"expression": "1 + 41", "returnByValue": True, "awaitPromise": False},
                )
            ],
        )
    ]
    assert not REGISTRY["page-evaluate"].policy.approval


def test_page_click_resolves_box_then_dispatches_mouse_events(monkeypatch, tmp_path) -> None:
    """``page-click`` resolves an element's box then dispatches a full mouse click sequence."""
    calls: list[list[tuple[str, dict[str, Any]]]] = []

    async def page_session(
        self: CdpBrowser, target_id: str, session_calls: list[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        calls.append(session_calls)
        method = session_calls[0][0]
        if method == "DOM.getDocument":
            return [{"root": {"nodeId": 1}}]
        if method == "DOM.querySelector":
            return [{"nodeId": 7}]
        if method == "DOM.getBoxModel":
            return [{"model": {"content": [0, 0, 10, 0, 10, 10, 0, 10]}}]
        return [{} for _ in session_calls]

    monkeypatch.setattr(CdpBrowser, "page_session", page_session)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "page-click",
                    "payload": {
                        "profile": "default",
                        "target_id": "target-1",
                        "selector": "#submit",
                    },
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert result.data == {
        "profile": "default",
        "target_id": "target-1",
        "selector": "#submit",
        "x": 5.0,
        "y": 5.0,
        "clicked": True,
    }
    assert [batch[0][0] for batch in calls[:3]] == [
        "DOM.getDocument",
        "DOM.querySelector",
        "DOM.getBoxModel",
    ]
    mouse_methods = [method for method, _ in calls[3]]
    assert mouse_methods == ["Input.dispatchMouseEvent"] * 3


def test_page_fill_form_embeds_field_map_and_returns_count(monkeypatch, tmp_path) -> None:
    """``page-fill-form`` embeds the selector-value map as JSON and returns the filled count."""
    seen: list[str] = []

    async def page_session(
        self: CdpBrowser, target_id: str, session_calls: list[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        seen.append(session_calls[0][1]["expression"])
        return [{"result": {"value": 2}}]

    monkeypatch.setattr(CdpBrowser, "page_session", page_session)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "page-fill-form",
                    "payload": {
                        "profile": "default",
                        "target_id": "target-1",
                        "fields": {"#email": "a@b.test", "#name": "Ada"},
                    },
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert result.data["filled"] == 2
    assert '"#email": "a@b.test"' in seen[0] or '"#email":"a@b.test"' in seen[0]


def test_cookie_set_is_approval_gated_before_reaching_cdp(monkeypatch, tmp_path) -> None:
    """``cookie-set`` requests fail-closed approval before the CDP call is ever issued."""
    approvals: list[dict[str, Any]] = []
    cdp_calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        cdp_calls.append((method, params))
        return {}

    async def approve(
        self: Daemon, action: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str, bool]:
        approvals.append({"action": action, "payload": payload})
        return payload, "approved in test", False

    monkeypatch.setattr(CdpBrowser, "call", call)
    monkeypatch.setattr(Daemon, "_approve", approve)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "cookie-set",
                    "payload": {
                        "profile": "default",
                        "name": "session",
                        "value": "abc",
                        "domain": "example.test",
                    },
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert REGISTRY["cookie-set"].policy.approval is True
    assert len(approvals) == 1
    assert approvals[0]["action"] == "cookie-set"
    assert cdp_calls == [
        (
            "Network.setCookie",
            {
                "name": "session",
                "value": "abc",
                "domain": "example.test",
                "path": "/",
                "secure": True,
                "httpOnly": False,
            },
        )
    ]


def test_browser_ask_user_forwards_to_extension_bridge(monkeypatch, tmp_path) -> None:
    """``browser-ask-user`` is extension-mediated and never requires the approval gate."""
    requests: list[tuple[str, dict[str, Any], str]] = []

    async def extension_request(
        self: Daemon, kind: str, payload: dict[str, Any], profile: str
    ) -> dict[str, Any]:
        requests.append((kind, payload, profile))
        return {"answer": "yes"}

    monkeypatch.setattr(Daemon, "extension_request", extension_request)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "browser-ask-user",
                    "payload": {"profile": "default", "question": "Continue?"},
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert result.data == {"answer": "yes", "profile": "default"}
    assert requests == [("user.ask", {"profile": "default", "question": "Continue?"}, "default")]
    assert not REGISTRY["browser-ask-user"].policy.approval
