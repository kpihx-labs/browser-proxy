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
    "page-click-coordinates",
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
    "cookie-list",
    "cookie-set",
    "cookie-remove",
    "storage-local-get",
    "storage-local-set",
    "storage-local-remove",
    "storage-local-clear",
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
    # 50 (profile-remove refonte) + tab-move + group-add-tabs + group-remove-tabs + group-sync
    # (tab/group refonte) = 54; -2 (page-list/page-get purged) +4 (tab-get, window-sync,
    # tab-move renamed tab-update so net 0) +4 (window-save/restore/saved-list/saved-remove) = 58;
    # -1 (workspace-list removed: Edge has no supported Workspace API) = 57;
    # +1 (bookmark-update: new fine-grained batch rename/re-url/move/reposition action) = 58;
    # +1 (bookmark-get: read ALL info about ONE bookmark/folder, same philosophy as tab-get) = 59;
    # +6 (extension-list/get/enable/disable/reload/search: chrome.management ecosystem control +
    # a direct-CDP store search convenience action — extension-uninstall deliberately NOT
    # implemented, live-verified Chrome platform restriction, see CONTRACT.md) = 65;
    # -1 (group-sync purged: window-sync without bounds/state/focused is a strict superset of
    # what it did — KπX, GRAVÉ "purge group-sync vu que inclus ds window-sync") = 64;
    # -1 (page-set-download-behavior purged, unused/classic ~/Downloads is enough) = 63;
    # +1 (page-click-coordinates: click at explicit viewport coordinates via page-scoped CDP —
    # reachs a cross-origin iframe no selector can address) = 64;
    # +2 (storage-local-remove/storage-local-clear: completes the symmetric storage/cookie action
    # pair — live-verified `getItem`/`setItem` had no `removeItem`/`clear` counterpart) = 66;
    # +1 (page-click-eval: atomic Runtime.evaluate-based bounding box resolution, eliminates
    # the race condition that affects page-click on dynamic pages) = 67;
    # +1 (page-click-eval counted in REGISTRY) = 68;
    # +1 (page-press: CDP Input.dispatchKeyEvent for proper keyboard input) = 69 → 70.
    assert len(REGISTRY) == 70
    assert "group-sync" not in REGISTRY


def test_extension_management_actions_have_the_right_policy_shape() -> None:
    """`extension-list`/`extension-get`/`extension-reload`/`extension-search`/`extension-enable`
    are read-only, self-only, or low-risk-reversible — never approval-gated (KπX directive for
    `extension-enable` specifically); `extension-disable` alone stays approval-gated, matching the
    real risk of silently turning off a security-relevant extension the user still trusts."""
    for name in ("extension-list", "extension-get", "extension-reload", "extension-search"):
        assert REGISTRY[name].policy.approval is False, name
    assert REGISTRY["extension-enable"].policy.approval is False
    assert REGISTRY["extension-enable"].policy.preflight_fields == ("ids",)
    assert REGISTRY["extension-disable"].policy.approval is True
    assert REGISTRY["extension-disable"].policy.preflight_fields == ("ids",)


def test_extension_uninstall_is_deliberately_not_implemented() -> None:
    """`chrome.management.uninstall()` targeting another extension requires a genuine synchronous
    DOM user gesture (live-verified, direct `Runtime.evaluate` on the paired extension's own
    service worker) — a WebSocket-message-triggered call can never satisfy that, for any
    extension-mediated architecture. No `extension-uninstall` action exists; see CONTRACT.md."""
    assert "extension-uninstall" not in REGISTRY


def test_bookmark_get_and_list_root_id_are_read_only_thin_extension_passthroughs() -> None:
    """`bookmark-get`/`bookmark-list` are read-only, never approval-gated — same rationale as
    `tab-get`/`window-list` — and both dispatch to `_extension()` unchanged (the real tree-walk,
    root_id scoping, and children-preview logic all live in the paired extension, see the
    TypeScript test suite for that coverage)."""
    for name in ("bookmark-get", "bookmark-list"):
        policy = REGISTRY[name].policy
        assert policy.approval is False, name
        assert policy.preflight_fields == (), name


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


def test_tab_list_shows_flat_tabs_enriched_with_real_window_and_group_context(
    monkeypatch, tmp_path
) -> None:
    """`tab-list` is a FLAT list (unlike `window-list`'s grouped shape), but each tab now carries
    its REAL `window_id` and `group_id`/`group_title` (KπX root-caused live: "tab-list doit
    indiquer ds quel fenêtre est la tab, ds quel dossier c'est si c'est ds un dossier") — reusing
    `_window_list`'s own resolution entirely, never a second independent computation."""

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "Browser.getWindowForTarget":
            return {"windowId": 1, "bounds": {}}
        raise AssertionError(f"unexpected CDP method {method}")

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return [
            {"targetId": "t1", "type": "page", "url": "https://a.example"},
            {"targetId": "t2", "type": "page", "url": "https://b.example"},
        ]

    async def extension_request(
        self: Daemon, kind: str, payload: dict[str, Any], profile: str
    ) -> dict[str, Any]:
        assert kind == "window.layout"
        return {
            "windows": {
                "1": {
                    "tabs": [
                        {
                            "chrome_tab_id": 101,
                            "index": 0,
                            "url": "https://a.example",
                            "title": "A",
                            "group_id": None,
                            "active": False,
                            "pinned": False,
                        },
                        {
                            "chrome_tab_id": 102,
                            "index": 1,
                            "url": "https://b.example",
                            "title": "B",
                            "group_id": 55,
                            "active": True,
                            "pinned": False,
                        },
                    ],
                    "groups": {"55": {"title": "Research", "color": "blue", "collapsed": False}},
                    "order": [],
                }
            }
        }

    monkeypatch.setattr(CdpBrowser, "targets", targets)
    monkeypatch.setattr(CdpBrowser, "call", call)
    monkeypatch.setattr(Daemon, "extension_request", extension_request)
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
    tabs = result.data["tabs"]
    assert [t["targetId"] for t in tabs] == ["t1", "t2"]
    assert tabs[0]["window_id"] == 1
    assert tabs[0]["group_id"] is None
    assert tabs[0]["group_title"] is None
    assert tabs[1]["window_id"] == 1
    assert tabs[1]["group_id"] == 55
    assert tabs[1]["group_title"] == "Research"


def test_tab_get_merges_raw_cdp_metadata_with_window_and_group_context(
    monkeypatch, tmp_path
) -> None:
    """`tab-get` replaces `page-get`: ALL available information about ONE tab in a single call —
    the real `Target.getTargetInfo` CDP metadata PLUS `tab-list`'s window/group context, never two
    overlapping ways to read one tab's identity (KπX, GRAVÉ: "tab = page... je ne veux pas de
    duplication inutile... fusionne avec tab, gère tab-get pour avoir toutes les infos")."""

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "Browser.getWindowForTarget":
            return {"windowId": 1, "bounds": {}}
        if method == "Target.getTargetInfo":
            return {
                "targetInfo": {
                    "targetId": "t1",
                    "type": "page",
                    "title": "A",
                    "url": "https://a.example",
                    "attached": True,
                }
            }
        raise AssertionError(f"unexpected CDP method {method}")

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return [{"targetId": "t1", "type": "page", "url": "https://a.example"}]

    async def extension_request(
        self: Daemon, kind: str, payload: dict[str, Any], profile: str
    ) -> dict[str, Any]:
        assert kind == "window.layout"
        return {
            "windows": {
                "1": {
                    "tabs": [
                        {
                            "chrome_tab_id": 101,
                            "index": 0,
                            "url": "https://a.example",
                            "title": "A",
                            "group_id": 55,
                            "active": True,
                            "pinned": False,
                        }
                    ],
                    "groups": {"55": {"title": "Research", "color": "blue", "collapsed": False}},
                    "order": [],
                }
            }
        }

    monkeypatch.setattr(CdpBrowser, "targets", targets)
    monkeypatch.setattr(CdpBrowser, "call", call)
    monkeypatch.setattr(Daemon, "extension_request", extension_request)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={"action": "tab-get", "payload": {"profile": "default", "target_id": "t1"}},
            )
        )
    )
    tab = result.data["tab"]
    assert tab["targetId"] == "t1"
    assert tab["attached"] is True
    assert tab["window_id"] == 1
    assert tab["group_id"] == 55
    assert tab["group_title"] == "Research"


def test_tab_get_fails_closed_for_an_unknown_target_id(monkeypatch, tmp_path) -> None:
    """`tab-get` never silently returns an empty/partial object for a non-existent target."""

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "Browser.getWindowForTarget":
            return {"windowId": 1, "bounds": {}}
        raise AssertionError(f"unexpected CDP method {method}")

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(CdpBrowser, "targets", targets)
    monkeypatch.setattr(CdpBrowser, "call", call)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "tab-get",
                    "payload": {"profile": "default", "target_id": "missing"},
                },
            )
        )
    )
    assert result.meta.status == "error"
    assert result.data["code"] == "CDP_UNAVAILABLE"


def test_window_list_enriches_with_chrome_layout_and_correlates_target_ids(
    monkeypatch, tmp_path
) -> None:
    """`window-list` merges the canonical extension-sourced `chrome_layout` (real order, real
    group_id per tab) onto its pre-existing CDP-only `tabs`, correlating each chrome tab to its
    CDP `target_id` by matching URL in encounter order — never a flat list ignorant of groups."""

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "Browser.getWindowForTarget":
            return {"windowId": 1, "bounds": {}}
        raise AssertionError(f"unexpected CDP method {method}")

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return [
            {"targetId": "t1", "type": "page", "url": "https://a.example"},
            {"targetId": "t2", "type": "page", "url": "https://b.example"},
        ]

    async def extension_request(
        self: Daemon, kind: str, payload: dict[str, Any], profile: str
    ) -> dict[str, Any]:
        assert kind == "window.layout"
        return {
            "windows": {
                "1": {
                    "tabs": [
                        {
                            "chrome_tab_id": 101,
                            "index": 0,
                            "url": "https://a.example",
                            "title": "A",
                            "group_id": None,
                            "active": False,
                            "pinned": False,
                        },
                        {
                            "chrome_tab_id": 102,
                            "index": 1,
                            "url": "https://b.example",
                            "title": "B",
                            "group_id": 55,
                            "active": True,
                            "pinned": False,
                        },
                    ],
                    "groups": {"55": {"title": "Research", "color": "blue", "collapsed": False}},
                    "order": [
                        {"kind": "tab", "chrome_tab_id": 101},
                        {
                            "kind": "group",
                            "group_id": 55,
                            "title": "Research",
                            "color": "blue",
                            "collapsed": False,
                            "tabs": [102],
                        },
                    ],
                }
            }
        }

    monkeypatch.setattr(CdpBrowser, "targets", targets)
    monkeypatch.setattr(CdpBrowser, "call", call)
    monkeypatch.setattr(Daemon, "extension_request", extension_request)
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

    window = result.data["windows"][0]
    assert window["window_id"] == 1
    assert [t["targetId"] for t in window["tabs"]] == ["t1", "t2"]
    layout = window["chrome_layout"]
    assert [tab["target_id"] for tab in layout["tabs"]] == ["t1", "t2"]
    assert layout["order"][1]["tabs"] == [102]
    assert layout["groups"]["55"]["title"] == "Research"


def test_window_list_degrades_honestly_when_the_extension_is_unavailable(
    monkeypatch, tmp_path
) -> None:
    """No extension connected → `chrome_layout` is explicitly `None`, never a silent guess, and
    the pre-existing CDP-only `tabs` field is entirely unaffected."""

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "Browser.getWindowForTarget":
            return {"windowId": 1, "bounds": {}}
        raise AssertionError(f"unexpected CDP method {method}")

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return [{"targetId": "t1", "type": "page", "url": "https://a.example"}]

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

    window = result.data["windows"][0]
    assert window["chrome_layout"] is None
    assert [t["targetId"] for t in window["tabs"]] == ["t1"]


def test_correlate_cdp_targets_pairs_duplicate_urls_deterministically() -> None:
    """Two tabs sharing the exact same URL pair up in encounter order on both sides, never an
    ambiguous 'first one wins' guess."""
    from browser_proxy.actions import _correlate_cdp_targets

    cdp_tabs = [
        {"targetId": "t1", "url": "https://same.example"},
        {"targetId": "t2", "url": "https://same.example"},
    ]
    chrome_tabs = [
        {"chrome_tab_id": 1, "url": "https://same.example"},
        {"chrome_tab_id": 2, "url": "https://same.example"},
    ]
    _correlate_cdp_targets(cdp_tabs, chrome_tabs)
    assert [tab["target_id"] for tab in chrome_tabs] == ["t1", "t2"]


def test_correlate_cdp_targets_sets_none_when_nothing_matches() -> None:
    from browser_proxy.actions import _correlate_cdp_targets

    cdp_tabs: list[dict[str, Any]] = []
    chrome_tabs = [{"chrome_tab_id": 1, "url": "https://a.example"}]
    _correlate_cdp_targets(cdp_tabs, chrome_tabs)
    assert chrome_tabs[0]["target_id"] is None


def test_tab_update_and_group_add_tabs_are_registered_without_approval() -> None:
    """`tab-update`/`group-add-tabs` follow the same no-approval rationale as
    `window-create`/`tab-create`: directly observable, already-visible manipulations. Unlike
    `group-remove-tabs`, now HITL-gated (KπX directive, GRAVÉ reversal)."""
    for name, identity_field in (
        ("tab-update", "tab_id"),
        ("group-add-tabs", "group_id"),
    ):
        policy = REGISTRY[name].policy
        assert policy.approval is False, name
        assert policy.preflight_fields == (identity_field,), name


def test_group_remove_tabs_is_now_approval_gated() -> None:
    """Reversal from the original "directly observable" stance (KπX directive, GRAVÉ): treated as
    a deliberate, reviewable reorganization, same as `group-create`/`group-update`."""
    assert REGISTRY["group-remove-tabs"].policy.approval is True


def test_window_sync_absorbs_the_purged_group_sync_action() -> None:
    """`group-sync` was purged (KπX, GRAVÉ: "purge group-sync vu que inclus ds window-sync") since
    `window-sync` without `bounds`/`state`/`focused` reorganizes the SAME `layout` schema — a
    strict superset, never a second differently-shaped way to say the same thing. The underlying
    bridge kind `group.sync` still exists (called internally by `window-sync`), but is no longer a
    standalone public `do` action."""
    assert "group-sync" not in REGISTRY
    policy = REGISTRY["window-sync"].policy
    assert policy.approval is True
    assert policy.preflight_fields == ("profile", "window_id")


def test_tab_update_forwards_to_the_extension_with_the_real_tab_id(monkeypatch, tmp_path) -> None:
    requests: list[tuple[str, dict[str, Any], str]] = []

    async def extension_request(
        self: Daemon, kind: str, payload: dict[str, Any], profile: str
    ) -> dict[str, Any]:
        requests.append((kind, payload, profile))
        return {"tab_id": 12, "url": None, "index": 0, "window_id": 1, "group_id": None}

    monkeypatch.setattr(Daemon, "extension_request", extension_request)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "tab-update",
                    "payload": {"profile": "default", "tab_id": 12, "index": 0},
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert requests == [("tab.update", {"profile": "default", "tab_id": 12, "index": 0}, "default")]


def test_tab_update_rejects_a_no_op_call() -> None:
    """`tab-update` requires at least one field beyond `tab_id` — never a silent no-op."""

    async def run() -> None:
        daemon = Daemon()
        return await daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={"action": "tab-update", "payload": {"profile": "default", "tab_id": 12}},
            )
        )

    result = asyncio.run(run())
    assert result.meta.status == "error"
    assert result.data["code"] == "VALIDATION_ERROR"


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
        if method == "Target.closeTarget":
            closed_targets.append(params["targetId"])
            return {}
        raise AssertionError(f"unexpected CDP method {method}")

    extension_calls: list[tuple[str, dict[str, Any]]] = []
    closed_targets: list[str] = []

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
                        "layout": [
                            {"type": "tab", "url": "https://a.example"},
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
    layout = data["layout"]
    assert [entry["type"] for entry in layout] == ["tab", "tab", "group", "tab"]
    assert layout[0]["url"] == "https://a.example"
    assert layout[1]["url"] == "https://b.example"
    assert layout[3]["url"] == "https://e.example"
    group_entry = layout[2]
    assert group_entry["title"] == "Research"
    assert [tab["chrome_tab_id"] for tab in group_entry["tabs"]] == [101, 102]
    assert group_entry["group"] == {"group_id": 55}
    # The very first target created is a disposable about:blank placeholder — every REAL layout
    # entry (including the first "tab") comes after it, and it alone is closed once done.
    assert created_targets[0]["params"]["url"] == "about:blank"
    assert all(target["params"].get("windowId") == 999 for target in created_targets[1:])
    assert closed_targets == [created_targets[0]["target_id"]]
    # group.create receives real chrome tab ids, never CDP target_id strings.
    group_calls = [payload for kind, payload in extension_calls if kind == "group.create"]
    assert group_calls == [
        {"profile": "default", "tab_ids": [101, 102], "title": "Research", "color": "blue"}
    ]


def test_window_create_rejects_a_malformed_layout_entry(monkeypatch, tmp_path) -> None:
    """An invalid `layout` shape fails closed with VALIDATION_ERROR before creating anything odd."""

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
                        "layout": [{"type": "unknown-kind"}],
                    },
                },
            )
        )
    )

    assert result.meta.status == "error"
    assert result.data["code"] == "VALIDATION_ERROR"


def test_window_create_requires_layout_with_no_separate_url_field(monkeypatch, tmp_path) -> None:
    """`window-create` has ONE way to describe its content — `layout` — never a separate
    top-level `url` for "the first tab" alongside a batch field for the rest (root-caused, KπX
    directive: that split was itself the unclean duplication this refonte removed)."""
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "window-create",
                    "payload": {"profile": "default", "url": "https://a.example"},
                },
            )
        )
    )

    assert result.meta.status == "error"
    assert result.data["code"] == "VALIDATION_ERROR"


async def _approve_as_is(
    self: Daemon, action: str, payload: dict[str, Any]
) -> tuple[dict[str, Any], str, bool]:
    """Bypass the real extension round-trip: approve every gated action unedited, for dispatch
    tests that only care about the handler/preview logic, never the HITL transport itself."""
    return payload, "", False


def test_window_close_closes_multiple_targets_in_one_approval(monkeypatch, tmp_path) -> None:
    """`window-close` accepts a `target_ids` LIST and closes every one of them via its own
    `Target.closeTarget` call, in ONE approval round-trip — regression guard for the KπX-directed
    refonte from a singular `target_id` (root-caused live: too slow/tedious with repeated
    single-target calls each needing its own approval)."""
    closed: list[str] = []

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        # No pre-existing tabs to correlate — the approval preview enrichment degrades to an
        # empty windows list, never blocking the real close.
        return []

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "Target.closeTarget"
        closed.append(params["targetId"])
        return {}

    monkeypatch.setattr(CdpBrowser, "targets", targets)
    monkeypatch.setattr(CdpBrowser, "call", call)
    monkeypatch.setattr(Daemon, "_approve", _approve_as_is)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "window-close",
                    "payload": {"profile": "default", "target_ids": ["t1", "t2", "t3"]},
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert result.data["target_ids"] == ["t1", "t2", "t3"]
    assert result.data["closed"] is True
    assert closed == ["t1", "t2", "t3"]


def test_window_close_rejects_empty_or_non_list_target_ids(monkeypatch, tmp_path) -> None:
    """`target_ids` must be a non-empty list — a bare string or empty list fails closed with
    VALIDATION_ERROR before any CDP call, never a silent no-op close."""
    monkeypatch.setattr(Daemon, "_approve", _approve_as_is)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    for bad_payload in (
        {"profile": "default", "target_ids": []},
        {"profile": "default", "target_ids": "t1"},
    ):
        result = asyncio.run(
            daemon.dispatch(
                RpcRequest(
                    id="1", method="do", params={"action": "window-close", "payload": bad_payload}
                )
            )
        )
        assert result.meta.status == "error"
        assert result.data["code"] == "VALIDATION_ERROR"


def test_window_close_approval_preview_shows_first_and_last_tab_per_window(
    monkeypatch, tmp_path
) -> None:
    """The approval payload sent to `_approve` for `window-close` gains a `"context"` field — one
    readable line per REAL window touched, naming its first/last tab title — so a human can tell
    WHICH window corresponds to which opaque `target_id` (root-caused live, KπX: "je ne connais pas
    quel id correspond à quel windos ds le hitl de close... il faut que je vois aussi les 1ers et
    derns elts des windows en question"). Also a regression guard for the generalized
    `_target_approval_preview`: `window-close` is just ONE of the actions that reaches it."""
    all_targets = [
        {"targetId": "t1", "type": "page", "title": "First A", "url": "https://a1.example"},
        {"targetId": "t2", "type": "page", "title": "Last A", "url": "https://a2.example"},
        {"targetId": "t3", "type": "page", "title": "Only B", "url": "https://b1.example"},
    ]
    window_for_target = {"t1": 100, "t2": 100, "t3": 200}

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return all_targets

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "Browser.getWindowForTarget":
            return {"windowId": window_for_target[params["targetId"]], "bounds": {}}
        if method == "Target.closeTarget":
            return {}
        raise AssertionError(f"unexpected CDP method {method}")

    captured_approval_payload: dict[str, Any] = {}

    async def capture_approve(
        self: Daemon, action: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str, bool]:
        captured_approval_payload.update(payload)
        return payload, "", False

    monkeypatch.setattr(CdpBrowser, "targets", targets)
    monkeypatch.setattr(CdpBrowser, "call", call)
    monkeypatch.setattr(Daemon, "_approve", capture_approve)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "window-close",
                    "payload": {"profile": "default", "target_ids": ["t1", "t2", "t3"]},
                },
            )
        )
    )

    assert result.meta.status == "ok"
    windows = captured_approval_payload["context"]
    assert len(windows) == 2
    window_100 = next(line for line in windows if line.startswith("window 100:"))
    window_200 = next(line for line in windows if line.startswith("window 200:"))
    assert 'first: "First A"' in window_100
    assert 'last: "Last A"' in window_100
    assert "2/2 tabs to close" in window_100
    assert 'first: "Only B"' in window_200
    assert 'last: "Only B"' in window_200
    assert "1/1 tabs to close" in window_200
    # The real handler still ran normally afterward — the preview enrichment never leaks into
    # the actual close result or blocks the real action.
    assert result.data["target_ids"] == ["t1", "t2", "t3"]


def test_target_approval_preview_also_covers_a_singular_target_id(monkeypatch, tmp_path) -> None:
    """`_target_approval_preview` is centralized: `storage-local-set` (a SINGULAR `target_id`,
    not a `target_ids` list, and still HITL-gated now that `tab-activate` no longer is) gets the
    exact same first/last-tab `"context"` enrichment as `window-close` — never a
    `window-close`-only special case."""

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return [
            {"targetId": "t1", "type": "page", "title": "Solo Tab", "url": "https://solo.example"}
        ]

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "Browser.getWindowForTarget":
            return {"windowId": 42, "bounds": {}}
        raise AssertionError(f"unexpected CDP method {method}")

    async def page_session(
        self: CdpBrowser, target_id: str, session_calls: list[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        return [{"result": {}}]

    captured_approval_payload: dict[str, Any] = {}

    async def capture_approve(
        self: Daemon, action: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str, bool]:
        captured_approval_payload.update(payload)
        return payload, "", False

    monkeypatch.setattr(CdpBrowser, "targets", targets)
    monkeypatch.setattr(CdpBrowser, "call", call)
    monkeypatch.setattr(CdpBrowser, "page_session", page_session)
    monkeypatch.setattr(Daemon, "_approve", capture_approve)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "storage-local-set",
                    "payload": {
                        "profile": "default",
                        "target_id": "t1",
                        "key": "theme",
                        "value": "dark",
                    },
                },
            )
        )
    )

    assert result.meta.status == "ok"
    context = captured_approval_payload["context"]
    assert len(context) == 1
    assert 'first: "Solo Tab"' in context[0]
    assert 'last: "Solo Tab"' in context[0]
    assert "window 42:" in context[0]
    assert result.data["target_id"] == "t1"


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


def test_page_evaluate_falls_back_gracefully_on_non_serializable_result(
    monkeypatch, tmp_path
) -> None:
    """``page-evaluate`` retries without ``returnByValue`` and returns a safe description instead
    of crashing when the expression evaluates to a non-JSON-serializable value (e.g.
    `window.open(...)` returns a circular-reference `Window` object — live-verified: CDP always
    raises `CDP_ERROR: Object reference chain is too long` under `returnByValue: true`)."""
    calls: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = []

    async def page_session(
        self: CdpBrowser, target_id: str, session_calls: list[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        calls.append((target_id, session_calls))
        _method, params = session_calls[0]
        if params.get("returnByValue"):
            raise RuntimeError("CDP_ERROR: Object reference chain is too long")
        return [{"result": {"type": "object", "description": "Window"}}]

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
                        "expression": "window.open('https://example.org')",
                    },
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert result.data["result"] == "Window"
    assert len(calls) == 2
    assert calls[0][1][0][1]["returnByValue"] is True
    assert "returnByValue" not in calls[1][1][0][1]


def test_page_evaluate_reraises_unrelated_runtime_errors(monkeypatch, tmp_path) -> None:
    """A genuinely unrelated CDP failure is never silently swallowed by the serialization
    fallback — only the exact `Object reference chain is too long` message is caught."""

    async def page_session(
        self: CdpBrowser, target_id: str, session_calls: list[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        raise RuntimeError("CDP_ERROR: some unrelated failure")

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
                        "expression": "1 + 1",
                    },
                },
            )
        )
    )

    assert result.meta.status == "error"
    assert result.data["code"] == "CDP_ERROR"


def test_page_click_resolves_box_then_dispatches_mouse_events(monkeypatch, tmp_path) -> None:
    """``page-click`` resolves an element's box (via ONE bundled `page_session` call — never 3
    separate ones, the exact bug root-caused live: `DOM.getDocument`'s `nodeId`s do not survive a
    separate `page_session()` attach/detach cycle) then dispatches a full mouse click sequence."""
    calls: list[list[tuple[str, Any]]] = []

    async def page_session(
        self: CdpBrowser, target_id: str, session_calls: list[tuple[str, Any]]
    ) -> list[dict[str, Any]]:
        calls.append(session_calls)
        results: list[dict[str, Any]] = []
        for method, raw_params in session_calls:
            params = raw_params(results) if callable(raw_params) else raw_params
            if method == "DOM.getDocument":
                results.append({"root": {"nodeId": 1}})
            elif method == "DOM.querySelector":
                assert params == {"nodeId": 1, "selector": "#submit"}
                results.append({"nodeId": 7})
            elif method == "DOM.getBoxModel":
                assert params == {"nodeId": 7}
                results.append({"model": {"content": [0, 0, 10, 0, 10, 10, 0, 10]}})
            else:
                results.append({})
        return results

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
    # Exactly TWO page_session calls total: one bundled DOM-resolution session (never split
    # across 3 separate attach/detach cycles), then one bundled mouse-event session.
    assert len(calls) == 2
    assert [method for method, _ in calls[0]] == [
        "DOM.getDocument",
        "DOM.querySelector",
        "DOM.getBoxModel",
    ]
    mouse_methods = [method for method, _ in calls[1]]
    assert mouse_methods == ["Input.dispatchMouseEvent"] * 3


def test_page_click_coordinates_dispatches_mouse_events_at_coordinates(
    monkeypatch, tmp_path
) -> None:
    """``page-click-coordinates`` dispatches the SAME page-scoped mouse-event sequence as
    ``page-click`` but at caller-provided viewport coordinates — one bundled `page_session`
    call, three `Input.dispatchMouseEvent` events (moved/pressed/released) at the exact x/y,
    never a DOM resolution step (a cross-origin iframe such as a reCAPTCHA widget has no
    parent-page selector to resolve)."""
    calls: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = []

    async def page_session(
        self: CdpBrowser, target_id: str, session_calls: list[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        calls.append((target_id, session_calls))
        return [{}] * len(session_calls)

    monkeypatch.setattr(CdpBrowser, "page_session", page_session)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "page-click-coordinates",
                    "payload": {
                        "profile": "default",
                        "target_id": "target-1",
                        "x": 91.5,
                        "y": 312.3,
                    },
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert result.data == {
        "profile": "default",
        "target_id": "target-1",
        "x": 91.5,
        "y": 312.3,
        "button": "left",
        "click_count": 1,
        "clicked": True,
    }
    target_id, session_calls = calls[0]
    assert target_id == "target-1"
    assert [method for method, _ in session_calls] == ["Input.dispatchMouseEvent"] * 3
    moved, pressed, released = [params for _, params in session_calls]
    assert moved == {"type": "mouseMoved", "x": 91.5, "y": 312.3}
    assert pressed == {
        "type": "mousePressed",
        "x": 91.5,
        "y": 312.3,
        "button": "left",
        "clickCount": 1,
    }
    assert released == {
        "type": "mouseReleased",
        "x": 91.5,
        "y": 312.3,
        "button": "left",
        "clickCount": 1,
    }
    assert not REGISTRY["page-click-coordinates"].policy.approval


def test_page_click_coordinates_double_click_escalates_click_count(monkeypatch, tmp_path) -> None:
    """``click_count: 2`` emits mouseMoved once then TWO press/release pairs whose ``clickCount``
    escalates 1, 2 — the exact CDP shape a real double-click produces, so pages relying on the
    native ``dblclick`` event fire correctly."""
    calls: list[list[tuple[str, dict[str, Any]]]] = []

    async def page_session(
        self: CdpBrowser, target_id: str, session_calls: list[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        calls.append(session_calls)
        return [{}] * len(session_calls)

    monkeypatch.setattr(CdpBrowser, "page_session", page_session)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "page-click-coordinates",
                    "payload": {
                        "profile": "default",
                        "target_id": "target-1",
                        "x": 10,
                        "y": 20,
                        "click_count": 2,
                    },
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert result.data["button"] == "left"
    assert result.data["click_count"] == 2
    session_calls = calls[0]
    # mouseMoved once + (pressed, released) twice = 5 events in ONE bundled session.
    assert len(session_calls) == 5
    assert [method for method, _ in session_calls] == ["Input.dispatchMouseEvent"] * 5
    assert session_calls[0][1] == {"type": "mouseMoved", "x": 10, "y": 20}
    pressed_counts = [
        params["clickCount"]
        for method, params in session_calls
        if params.get("type") == "mousePressed"
    ]
    released_counts = [
        params["clickCount"]
        for method, params in session_calls
        if params.get("type") == "mouseReleased"
    ]
    assert pressed_counts == [1, 2]
    assert released_counts == [1, 2]
    for method, params in session_calls[1:]:
        assert params["button"] == "left"


def test_page_click_coordinates_right_click_uses_right_button(monkeypatch, tmp_path) -> None:
    """``button: right`` propagates through every press/release — a context-menu trigger CDP
    delivers to the page, never a silently ignored field."""
    calls: list[list[tuple[str, dict[str, Any]]]] = []

    async def page_session(
        self: CdpBrowser, target_id: str, session_calls: list[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        calls.append(session_calls)
        return [{}] * len(session_calls)

    monkeypatch.setattr(CdpBrowser, "page_session", page_session)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "page-click-coordinates",
                    "payload": {
                        "profile": "default",
                        "target_id": "target-1",
                        "x": 50,
                        "y": 60,
                        "button": "right",
                    },
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert result.data["button"] == "right"
    assert result.data["click_count"] == 1
    session_calls = calls[0]
    assert len(session_calls) == 3
    for method, params in session_calls[1:]:
        assert params["button"] == "right"
    assert session_calls[1][1]["clickCount"] == 1


def test_page_click_coordinates_rejects_invalid_button_and_click_count(
    monkeypatch, tmp_path
) -> None:
    """``button`` outside the CDP set and non-positive/non-integer ``click_count`` fail closed
    with VALIDATION_ERROR — never a silently defaulted or truncated click."""
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    for bad_payload in (
        {"profile": "default", "target_id": "target-1", "x": 5, "y": 5, "button": "turbo"},
        {"profile": "default", "target_id": "target-1", "x": 5, "y": 5, "click_count": 0},
        {"profile": "default", "target_id": "target-1", "x": 5, "y": 5, "click_count": -2},
        {"profile": "default", "target_id": "target-1", "x": 5, "y": 5, "click_count": 1.5},
    ):
        result = asyncio.run(
            daemon.dispatch(
                RpcRequest(
                    id="1",
                    method="do",
                    params={"action": "page-click-coordinates", "payload": bad_payload},
                )
            )
        )
        assert result.meta.status == "error", bad_payload
        assert result.data["code"] == "VALIDATION_ERROR", bad_payload


def test_page_click_coordinates_rejects_missing_or_non_numeric_coordinates(
    monkeypatch, tmp_path
) -> None:
    """``page-click-coordinates`` fails closed with VALIDATION_ERROR when coordinates are
    absent or non-numeric — never a silent 0,0 click at the top-left corner."""
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    for bad_payload in (
        {"profile": "default", "target_id": "target-1"},
        {"profile": "default", "target_id": "target-1", "x": 5},
        {"profile": "default", "target_id": "target-1", "y": 5},
        {"profile": "default", "target_id": "target-1", "x": "NaN", "y": 5},
        {"profile": "default", "target_id": "target-1", "x": 5, "y": None},
    ):
        result = asyncio.run(
            daemon.dispatch(
                RpcRequest(
                    id="1",
                    method="do",
                    params={"action": "page-click-coordinates", "payload": bad_payload},
                )
            )
        )
        assert result.meta.status == "error", bad_payload
        assert result.data["code"] == "VALIDATION_ERROR", bad_payload


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
            "Storage.setCookies",
            {
                "cookies": [
                    {
                        "name": "session",
                        "value": "abc",
                        "domain": "example.test",
                        "path": "/",
                        "secure": True,
                        "httpOnly": False,
                        "url": "https://example.test/",
                    }
                ]
            },
        )
    ]


def test_cookie_remove_uses_page_session_on_first_live_target(monkeypatch, tmp_path) -> None:
    """``cookie-remove`` calls `Network.deleteCookies` through a page session (browser-level
    `Network.*`/`Storage.deleteCookies` do not exist — live-verified method by method). Mocks the
    REAL raw `Target.getTargets` shape (`targetId`, never `id` — KπX, GRAVÉ: the prior mock used
    `id` here, matching a real `KeyError` bug in the handler instead of catching it)."""
    approvals: list[dict[str, Any]] = []
    session_calls: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = []

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return [
            {"targetId": "TARGET-1", "type": "page"},
            {"targetId": "TARGET-2", "type": "page"},
        ]

    async def page_session(
        self: CdpBrowser, target_id: str, calls: list[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        session_calls.append((target_id, calls))
        return [{}]

    async def approve(
        self: Daemon, action: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str, bool]:
        approvals.append({"action": action, "payload": payload})
        return payload, "approved in test", False

    monkeypatch.setattr(CdpBrowser, "targets", targets)
    monkeypatch.setattr(CdpBrowser, "page_session", page_session)
    monkeypatch.setattr(Daemon, "_approve", approve)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "cookie-remove",
                    "payload": {"profile": "default", "name": "session", "domain": "example.test"},
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert len(approvals) == 1
    assert session_calls == [
        (
            "TARGET-1",
            [("Network.deleteCookies", {"name": "session", "domain": "example.test"})],
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


def test_browser_solve_captcha_escalates_click_checkbox_to_a_real_cdp_coordinate_click(
    monkeypatch, tmp_path
) -> None:
    """``browser-solve-captcha``'s ``click_checkbox`` escalates the extension-reported iframe
    ``rect``/``url`` to a REAL ``Input.dispatchMouseEvent`` sequence (KπX, GRAVÉ: a content-script
    click on a genuinely cross-origin reCAPTCHA iframe never reaches the checkbox — live-verified
    against the official Google demo)."""
    session_calls: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = []

    async def extension_request(
        self: Daemon, kind: str, payload: dict[str, Any], profile: str
    ) -> dict[str, Any]:
        assert kind == "captcha.solve"
        return {
            "detected": True,
            "clicked": False,
            "reason": "reported iframe rect for CDP-level coordinate click",
            "rect": {"left": 10.0, "top": 20.0, "width": 304.0, "height": 78.0},
            "url": "https://example.com/",
        }

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return [
            {"targetId": "OTHER-TAB", "type": "page", "url": "https://unrelated.example/"},
            {"targetId": "TARGET-1", "type": "page", "url": "https://example.com/"},
        ]

    async def page_session(
        self: CdpBrowser, target_id: str, calls: list[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        session_calls.append((target_id, calls))
        return [{}] * len(calls)

    monkeypatch.setattr(Daemon, "extension_request", extension_request)
    monkeypatch.setattr(CdpBrowser, "targets", targets)
    monkeypatch.setattr(CdpBrowser, "page_session", page_session)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "browser-solve-captcha",
                    "payload": {"profile": "default", "action": "click_checkbox"},
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert result.data["clicked"] is True
    assert (
        result.data["reason"]
        == "clicked via CDP-level coordinate dispatch (cross-origin iframe checkbox)"
    )
    assert result.data["click"]["x"] == 40.0
    assert result.data["click"]["y"] == 59.0
    assert len(session_calls) == 1
    target_id, calls = session_calls[0]
    assert target_id == "TARGET-1"
    assert [call[0] for call in calls] == [
        "Input.dispatchMouseEvent",
        "Input.dispatchMouseEvent",
        "Input.dispatchMouseEvent",
    ]


def test_browser_solve_captcha_detect_never_attempts_a_cdp_click(monkeypatch, tmp_path) -> None:
    """``detect`` never escalates to CDP, even if a rect happened to be present."""

    async def extension_request(
        self: Daemon, kind: str, payload: dict[str, Any], profile: str
    ) -> dict[str, Any]:
        return {"detected": True, "clicked": False}

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        raise AssertionError("targets() must never be called for a plain detect")

    monkeypatch.setattr(Daemon, "extension_request", extension_request)
    monkeypatch.setattr(CdpBrowser, "targets", targets)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "browser-solve-captcha",
                    "payload": {"profile": "default", "action": "detect"},
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert result.data == {"detected": True, "clicked": False, "profile": "default"}


def test_browser_solve_captcha_click_checkbox_without_a_matching_target_returns_the_raw_reply(
    monkeypatch, tmp_path
) -> None:
    """If no live CDP target matches the reported url (tab closed mid-flight, e.g.), the raw
    extension reply is returned unchanged rather than raising."""

    async def extension_request(
        self: Daemon, kind: str, payload: dict[str, Any], profile: str
    ) -> dict[str, Any]:
        return {
            "detected": True,
            "clicked": False,
            "reason": "reported iframe rect for CDP-level coordinate click",
            "rect": {"left": 0.0, "top": 0.0, "width": 304.0, "height": 78.0},
            "url": "https://example.com/",
        }

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(Daemon, "extension_request", extension_request)
    monkeypatch.setattr(CdpBrowser, "targets", targets)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "browser-solve-captcha",
                    "payload": {"profile": "default", "action": "click_checkbox"},
                },
            )
        )
    )

    assert result.meta.status == "ok"
    assert result.data["clicked"] is False


def _window_save_fixture(monkeypatch, tmp_path) -> Daemon:
    """Shared fixture for the `window-save`/`window-restore`/`window-saved-*` tests: one live
    window (`window_id=100`) with a standalone tab and a 2-tab "Research" group, isolated
    persistent-state so tests never touch the REAL `~/.local/state/browser-proxy` saved-windows
    store."""
    monkeypatch.setenv("BROWSER_PROXY_PERSISTENT_STATE_DIR", str(tmp_path / "state"))

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "Browser.getWindowForTarget":
            return {"windowId": 100, "bounds": {"left": 0, "top": 0, "width": 800, "height": 600}}
        if method == "Target.createTarget":
            return {"targetId": f"created-{params.get('url')}"}
        if method == "Target.closeTarget":
            return {}
        raise AssertionError(f"unexpected CDP method {method}")

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return [
            {"targetId": "t1", "type": "page", "url": "https://a.example"},
            {"targetId": "t2", "type": "page", "url": "https://b.example"},
            {"targetId": "t3", "type": "page", "url": "https://c.example"},
        ]

    async def extension_request(
        self: Daemon, kind: str, payload: dict[str, Any], profile: str
    ) -> dict[str, Any]:
        if kind == "window.layout":
            return {
                "windows": {
                    "100": {
                        "tabs": [
                            {
                                "chrome_tab_id": 1,
                                "index": 0,
                                "url": "https://a.example",
                                "title": "A",
                                "group_id": None,
                                "active": False,
                                "pinned": False,
                            },
                            {
                                "chrome_tab_id": 2,
                                "index": 1,
                                "url": "https://b.example",
                                "title": "B",
                                "group_id": 55,
                                "active": False,
                                "pinned": False,
                            },
                            {
                                "chrome_tab_id": 3,
                                "index": 2,
                                "url": "https://c.example",
                                "title": "C",
                                "group_id": 55,
                                "active": False,
                                "pinned": False,
                            },
                        ],
                        "groups": {
                            "55": {"title": "Research", "color": "blue", "collapsed": False}
                        },
                        "order": [
                            {"kind": "tab", "chrome_tab_id": 1},
                            {
                                "kind": "group",
                                "group_id": 55,
                                "tabs": [2, 3],
                                "title": "Research",
                                "color": "blue",
                                "collapsed": False,
                            },
                        ],
                    }
                }
            }
        if kind == "tab.capture_next":
            return {"tab_id": 900}
        if kind == "group.create":
            return {"id": 999, "title": payload.get("title")}
        raise AssertionError(f"unexpected extension kind {kind}")

    monkeypatch.setattr(CdpBrowser, "targets", targets)
    monkeypatch.setattr(CdpBrowser, "call", call)
    monkeypatch.setattr(Daemon, "extension_request", extension_request)
    return _daemon_with_profile(tmp_path, monkeypatch)


def test_window_save_and_restore_round_trip_a_batch_of_windows(monkeypatch, tmp_path) -> None:
    """`window-save` snapshots a REAL window's exact tab/group structure to disk; `window-restore`
    reopens it as a real new window via the SAME `window-create` mechanism — both batch, both
    scoped by `profile` (KπX directive, GRAVÉ: "on peut save plusieurs d'un coup... on peut
    restore plusieurs d'un coup... on précise pour tout cela par le profil")."""
    daemon = _window_save_fixture(monkeypatch, tmp_path)

    save_result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "window-save",
                    "payload": {
                        "profile": "default",
                        "saves": [{"window_id": 100, "name": "Research"}],
                    },
                },
            )
        )
    )
    assert save_result.meta.status == "ok"
    assert save_result.data["saved"] == [{"name": "Research", "window_id": 100, "tab_count": 3}]

    list_result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="2",
                method="do",
                params={"action": "window-saved-list", "payload": {"profile": "default"}},
            )
        )
    )
    assert list_result.meta.status == "ok"
    windows = list_result.data["windows"]
    assert len(windows) == 1
    assert windows[0]["name"] == "Research"
    assert windows[0]["tab_count"] == 3
    assert windows[0]["layout"] == [
        {"type": "tab", "url": "https://a.example"},
        {
            "type": "group",
            "title": "Research",
            "color": "blue",
            "tabs": ["https://b.example", "https://c.example"],
        },
    ]

    restore_result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="3",
                method="do",
                params={
                    "action": "window-restore",
                    "payload": {"profile": "default", "names": ["Research"]},
                },
            )
        )
    )
    assert restore_result.meta.status == "ok"
    restored = restore_result.data["restored"]
    assert len(restored) == 1
    assert restored[0]["name"] == "Research"
    assert restored[0]["window_id"] == 100  # same fake Browser.getWindowForTarget answer
    assert len(restored[0]["layout"]) == 2


def test_window_save_fails_closed_without_the_extension(monkeypatch, tmp_path) -> None:
    """`window-save` requires `chrome_layout` (paired extension) — never a snapshot silently
    missing real group/order context."""
    monkeypatch.setenv("BROWSER_PROXY_PERSISTENT_STATE_DIR", str(tmp_path / "state"))

    async def call(self: CdpBrowser, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"windowId": 100, "bounds": {}}

    async def targets(self: CdpBrowser) -> list[dict[str, Any]]:
        return [{"targetId": "t1", "type": "page", "url": "https://a.example"}]

    monkeypatch.setattr(CdpBrowser, "targets", targets)
    monkeypatch.setattr(CdpBrowser, "call", call)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)

    result = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "window-save",
                    "payload": {"profile": "default", "saves": [{"window_id": 100, "name": "X"}]},
                },
            )
        )
    )
    assert result.meta.status == "error"
    assert result.data["code"] == "EXTENSION_UNAVAILABLE"


def test_window_saved_remove_is_all_or_nothing_and_batch(monkeypatch, tmp_path) -> None:
    """`window-saved-remove` deletes several names in ONE call, but fails closed BEFORE deleting
    anything if any single name does not exist — never a partial delete."""
    daemon = _window_save_fixture(monkeypatch, tmp_path)
    asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "window-save",
                    "payload": {
                        "profile": "default",
                        "saves": [{"window_id": 100, "name": "Research"}],
                    },
                },
            )
        )
    )

    partial = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="2",
                method="do",
                params={
                    "action": "window-saved-remove",
                    "payload": {"profile": "default", "names": ["Research", "does-not-exist"]},
                },
            )
        )
    )
    assert partial.meta.status == "error"
    assert partial.data["code"] == "NOT_FOUND"

    still_there = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="3",
                method="do",
                params={"action": "window-saved-list", "payload": {"profile": "default"}},
            )
        )
    )
    assert len(still_there.data["windows"]) == 1  # nothing was deleted by the failed batch

    removed = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="4",
                method="do",
                params={
                    "action": "window-saved-remove",
                    "payload": {"profile": "default", "names": ["Research"]},
                },
            )
        )
    )
    assert removed.meta.status == "ok"
    assert removed.data["removed"] == ["Research"]

    empty = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="5",
                method="do",
                params={"action": "window-saved-list", "payload": {"profile": "default"}},
            )
        )
    )
    assert empty.data["windows"] == []


def test_window_save_restore_saved_actions_are_not_approval_gated() -> None:
    """All 4 new actions follow the established rationale: `window-save`/`window-restore`
    (directly observable, same as `window-create`) and `window-saved-remove` (locked identity,
    admin-tier, same as `profile-remove` — never touches the live browser at all)."""
    for name in ("window-save", "window-restore", "window-saved-list", "window-saved-remove"):
        assert REGISTRY[name].policy.approval is False, name


def test_page_screenshot_result_field_is_path_not_output(monkeypatch, tmp_path) -> None:
    """Regression guard for the documented drift (KπX, fixed): `page-screenshot` with an
    `output` PAYLOAD field writes decoded bytes and returns the RESULT field `path` — never a
    result field named `output` (which connoted the payload name). When `output` is omitted,
    the result carries base64 `data` instead."""
    import base64 as _b64

    async def page_session(
        self: CdpBrowser, target_id: str, session_calls: list[tuple[str, dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        del self, target_id
        return [{"data": _b64.b64encode(b"PNGDATA").decode()} for _ in session_calls]

    monkeypatch.setattr(CdpBrowser, "page_session", page_session)
    daemon = _daemon_with_profile(tmp_path, monkeypatch)
    dest = tmp_path / "shot.png"

    saved = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="1",
                method="do",
                params={
                    "action": "page-screenshot",
                    "payload": {"profile": "default", "target_id": "t1", "output": str(dest)},
                },
            )
        )
    )
    assert saved.meta.status == "ok"
    assert "path" in saved.data and saved.data["path"] == str(dest)
    assert "output" not in saved.data
    assert dest.read_bytes() == b"PNGDATA"

    raw = asyncio.run(
        daemon.dispatch(
            RpcRequest(
                id="2",
                method="do",
                params={
                    "action": "page-screenshot",
                    "payload": {"profile": "default", "target_id": "t1", "base64": True},
                },
            )
        )
    )
    assert raw.meta.status == "ok"
    assert raw.data["data"] == _b64.b64encode(b"PNGDATA").decode()
    assert "path" not in raw.data
