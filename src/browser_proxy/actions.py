"""Registry of documented, flat Edge-only browser actions."""

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from browser_proxy.cdp import CdpBrowser
from browser_proxy.doc import attach_public_docstrings
from browser_proxy.paths import (
    edge_cdp_port,
    edge_profile_dir,
    edge_profile_state,
    saved_windows_path,
)
from browser_proxy.policy import (
    Policy,
    policy_of,
    require_approval,
    require_preflight,
    require_verification,
)

Handler = Callable[[dict[str, Any], "DaemonContext"], Awaitable[dict[str, Any]]]


class DaemonContext(Protocol):
    """Structural daemon interface available to action handlers."""

    async def start_profile(self, name: str) -> int:
        """Purpose: start the named managed Edge profile.

        Args:
            name (str): Safe persistent Edge profile name.

        Returns:
            int: Loopback CDP port for the started profile.

        Examples:
            >>> DaemonContext.start_profile.__name__
            'start_profile'
            >>> asyncio.iscoroutinefunction(DaemonContext.start_profile)
            True
        """
        ...

    async def profile_inventory(self) -> list[dict[str, Any]]:
        """Purpose: discover persistent profiles and report their live state.

        Args:
            None.

        Returns:
            list[dict[str, Any]]: On-disk profile records enriched with live systemd/CDP/extension
            state (see ``profile_state.describe_edge_profile()`` for the 3 daemon-independent axes).

        Examples:
            >>> asyncio.iscoroutinefunction(DaemonContext.profile_inventory)
            True
            >>> hasattr(DaemonContext, 'profile_inventory')
            True
        """
        ...

    async def remove_profile(self, name: str) -> dict[str, Any]:
        """Purpose: stop and safely trash one persistent Edge profile — never a permanent delete.

        Args:
            name (str): Persistent Edge profile name to remove.

        Returns:
            dict[str, Any]: ``profile``, ``removed``, ``was_active``, ``trashed_path``.

        Examples:
            >>> DaemonContext.remove_profile.__name__
            'remove_profile'
            >>> asyncio.iscoroutinefunction(DaemonContext.remove_profile)
            True
        """
        ...

    async def extension_request(
        self, kind: str, payload: dict[str, Any], profile: str
    ) -> dict[str, Any]:
        """Purpose: forward a typed object request to ONE specific profile's Edge extension.

        Args:
            kind (str): Stable typed extension request name.
            payload (dict[str, Any]): Complete single action object to forward.
            profile (str): Target browser-proxy profile; never answered by a different profile's
                extension even if one happens to be connected.

        Returns:
            dict[str, Any]: Object-valued typed extension response.

        Examples:
            >>> DaemonContext.extension_request.__name__
            'extension_request'
            >>> asyncio.iscoroutinefunction(DaemonContext.extension_request)
            True
        """
        ...


@dataclass(frozen=True)
class ActionDef:
    """Public action name, help group, implementation, and safety policy."""

    name: str
    group: str
    handler: Handler
    policy: Policy


def _profile(payload: dict[str, Any], context: DaemonContext) -> tuple[str, CdpBrowser]:
    """Purpose: resolve a managed Edge profile into its direct CDP client.

    Args:
        payload (dict[str, Any]): Action object containing an optional ``profile`` name.
        context (DaemonContext): Daemon transport; profile identity comes from disk.

    Returns:
        tuple[str, CdpBrowser]: Resolved profile name and browser-level CDP client.

    Raises:
        RuntimeError: ``PROFILE_UNAVAILABLE: ...`` when the profile was never declared (run
            ``profile-start`` first) or is declared but Edge has never actually started there yet
            (also run ``profile-start`` first) — uses the same ``edge_profile_state()`` predicate
            as ``profile-list``/``admin edge status``/``admin status``, never a private ad hoc check.

    Examples:
        >>> _profile.__name__
        '_profile'
        >>> callable(_profile)
        True
    """
    del context
    name = str(payload.get("profile", "default"))
    profile_dir = edge_profile_dir(name)
    state = edge_profile_state(profile_dir)
    if state == "not_declared":
        raise RuntimeError(f"PROFILE_UNAVAILABLE: {name} is not declared — run profile-start first")
    if state == "declared":
        raise RuntimeError(
            f"PROFILE_UNAVAILABLE: {name} is declared but Edge has never started there — "
            "run profile-start first"
        )
    return name, CdpBrowser(edge_cdp_port(name))


async def _profile_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: list browser-proxy-managed Microsoft Edge profiles.

    Args:
        payload (dict[str, Any]): Empty action object; retained for a uniform handler contract.
        context (DaemonContext): Daemon that discovers on-disk profile identity.

    Returns:
        dict[str, Any]: Persistent profiles enriched with systemd and CDP state.

    Examples:
        >>> _profile_list.__name__
        '_profile_list'
        >>> callable(_profile_list)
        True
    """
    return {"profiles": await context.profile_inventory()}


async def _profile_start(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: start a persistent Microsoft Edge profile with a private CDP endpoint.

    Args:
        payload (dict[str, Any]): Object with an optional persistent ``profile`` name.
        context (DaemonContext): Daemon that materializes and starts the persistent profile.

    Returns:
        dict[str, Any]: Started profile name and its loopback CDP port.

    Examples:
        >>> _profile_start.__name__
        '_profile_start'
        >>> callable(_profile_start)
        True
    """
    name = str(payload.get("profile", "default"))
    return {"profile": name, "cdp_port": await context.start_profile(name)}


@require_preflight("profile")
async def _profile_remove(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: stop and safely trash one persistent Edge profile — never a permanent delete.

    Args:
        payload (dict[str, Any]): Object with the required persistent ``profile`` name to remove.
        context (DaemonContext): Daemon that stops the unit and trashes the profile directory.

    Returns:
        dict[str, Any]: ``profile``, ``removed``, ``was_active``, ``trashed_path`` — the directory
        is moved to the KpihX trash (`trash-cli`), never permanently destroyed; bookmarks, cookies,
        and sessions inside it remain recoverable with ``trash-restore`` until purged on purpose.

    Notes:
        Deliberately NOT ``@require_approval``: an extension-mediated overlay would require THAT
        profile's own extension to be reachable, which is exactly untrue for the most likely
        removal candidates (a never-initialized or orphaned profile — the extension was never even
        loaded there). This is an admin-tier action, the same trust level as ``admin edge
        start``/``stop`` (explicit CLI invocation with the exact profile name), not a content
        mutation inside a live page. Safety instead comes from the mandatory named
        ``@require_preflight("profile")`` identity and the trash-not-delete guarantee.

    Examples:
        >>> _profile_remove.__name__
        '_profile_remove'
        >>> callable(_profile_remove)
        True
    """
    name = str(payload["profile"])
    return await context.remove_profile(name)


async def _page_targets(browser: CdpBrowser) -> list[dict[str, Any]]:
    """Purpose: list real page-type CDP targets — the one shared fact behind every action needing it.

    Args:
        browser (CdpBrowser): Client bound to one managed Edge debugging port.

    Returns:
        list[dict[str, Any]]: Every ``Target.getTargets`` entry with ``type == "page"``. Used
        by ``window-list`` (``tab-list``/``tab-get`` reuse ``window-list``'s full computation instead
        — see ``_tabs_with_context`` — for window/group context; ``page-list``/``page-get`` were
        purged and merged into ``tab-list``/``tab-get``, KπX directive: "tab = page... je ne veux pas
        de duplication inutile") — never a private re-implementation of this exact filter in each
        handler.

    Examples:
        >>> asyncio.iscoroutinefunction(_page_targets)
        True
        >>> callable(_page_targets)
        True
    """
    return [target for target in await browser.targets() if target.get("type") == "page"]


async def _window_id_for_target(
    browser: CdpBrowser, target_id: str
) -> tuple[int | None, dict[str, Any]]:
    """Purpose: resolve the REAL Edge window a page target lives in, via one genuine CDP call.

    Args:
        browser (CdpBrowser): Client bound to one managed Edge debugging port.
        target_id (str): CDP page target ID to resolve.

    Returns:
        tuple[int | None, dict[str, Any]]: The numeric ``windowId`` (``None`` if Edge could not
        resolve one — grouped separately rather than silently dropped) and its window ``bounds``.
        Never guessed from target order or ``browserContextId`` — ``Target.getTargets`` alone
        carries no window-grouping field at all (verified live: 2 real tabs both had identical
        ``browserContextId`` while genuinely being able to sit in different windows); this is the
        one real signal, `Browser.getWindowForTarget`.

    Examples:
        >>> asyncio.iscoroutinefunction(_window_id_for_target)
        True
        >>> callable(_window_id_for_target)
        True
    """
    try:
        info = await browser.call("Browser.getWindowForTarget", {"targetId": target_id})
    except RuntimeError:
        return None, {}
    window_id = info.get("windowId")
    return (window_id if isinstance(window_id, int) else None), dict(info.get("bounds", {}))


def _correlate_cdp_targets(
    cdp_tabs: list[dict[str, Any]], chrome_tabs: list[dict[str, Any]]
) -> None:
    """Purpose: attach a best-effort CDP ``target_id`` onto each extension-sourced chrome tab entry.

    Args:
        cdp_tabs (list[dict[str, Any]]): One real window's CDP page targets, in ``Target.getTargets``
            encounter order.
        chrome_tabs (list[dict[str, Any]]): The SAME window's canonical chrome-tab entries (from
            ``window.layout``), in real ``index`` order. Mutated in place: adds ``"target_id"``.

    Returns:
        None: CDP and the extension are two independent identifier systems (CDP ``target_id``
        strings vs. real numeric ``chrome.tabs.Tab.id``) with no first-class mapping between them —
        this pairs them by matching URL in left-to-right encounter order on both sides, which is
        exact for the common case (each URL open once) and deterministic even for duplicate URLs
        (Nth occurrence pairs with Nth occurrence on both sides) rather than silently picking
        whichever matches first. Sets ``"target_id": None`` when nothing matches at all.

    Examples:
        >>> _correlate_cdp_targets.__name__
        '_correlate_cdp_targets'
        >>> callable(_correlate_cdp_targets)
        True
    """
    remaining_by_url: dict[str | None, list[str]] = {}
    for target in cdp_tabs:
        remaining_by_url.setdefault(target.get("url"), []).append(target["targetId"])
    for tab in chrome_tabs:
        candidates = remaining_by_url.get(tab.get("url"))
        tab["target_id"] = candidates.pop(0) if candidates else None


async def windows_preview_for_targets(
    browser: CdpBrowser, target_ids: list[str]
) -> list[dict[str, Any]]:
    """Purpose: summarize each REAL window touched by a batch of target_ids for human approval.

    Args:
        browser (CdpBrowser): Client bound to one managed Edge debugging port.
        target_ids (list[str]): CDP target ids about to be acted on (e.g. closed).

    Returns:
        list[dict[str, Any]]: One entry per distinct window touched by ``target_ids``, each with
        ``window_id``, ``target_count`` (how many of the given ``target_ids`` live in that window),
        ``tab_count`` (total real tabs currently in that window), and ``first``/``last``
        (``{"title", "url"}`` of the window's first/last tab in real ``Target.getTargets``
        encounter order) — root-caused live (KπX): a ``window-close`` approval showing only opaque
        CDP ``target_ids`` gave no way to recognize WHICH window corresponds to which id; this is
        the exact first/last-tab context needed to tell windows apart at a glance. Public (no
        leading underscore) because ``daemon.dispatch()`` calls it cross-module before approval,
        unlike the rest of this module's private per-window helpers.

    Examples:
        >>> asyncio.iscoroutinefunction(windows_preview_for_targets)
        True
        >>> callable(windows_preview_for_targets)
        True
    """
    all_tabs = await _page_targets(browser)
    window_for_target: dict[str, int | None] = {}
    for tab in all_tabs:
        window_id, _ = await _window_id_for_target(browser, tab["targetId"])
        window_for_target[tab["targetId"]] = window_id
    tabs_by_window: dict[int | None, list[dict[str, Any]]] = {}
    for tab in all_tabs:
        tabs_by_window.setdefault(window_for_target[tab["targetId"]], []).append(tab)
    touched_windows: dict[int | None, int] = {}
    for target_id in target_ids:
        window_id = window_for_target.get(target_id)
        touched_windows[window_id] = touched_windows.get(window_id, 0) + 1

    def _summary(tab: dict[str, Any]) -> dict[str, str]:
        """Purpose: reduce one raw CDP page target to its human-readable title/url pair.

        Args:
            tab (dict[str, Any]): One ``Target.getTargets`` page entry.

        Returns:
            dict[str, str]: ``{"title": ..., "url": ...}``, always both keys present (empty string
            fallback), never a raw target blob leaking unrelated CDP fields into the approval UI.

        Examples:
            >>> _summary({"title": "Pi", "url": "https://en.wikipedia.org/wiki/Pi"})
            {'title': 'Pi', 'url': 'https://en.wikipedia.org/wiki/Pi'}
            >>> _summary({})
            {'title': '', 'url': ''}
        """
        return {"title": str(tab.get("title", "")), "url": str(tab.get("url", ""))}

    previews: list[dict[str, Any]] = []
    for window_id, target_count in touched_windows.items():
        tabs = tabs_by_window.get(window_id, [])
        previews.append(
            {
                "window_id": window_id,
                "target_count": target_count,
                "tab_count": len(tabs),
                "first": _summary(tabs[0]) if tabs else None,
                "last": _summary(tabs[-1]) if tabs else None,
            }
        )
    return previews


def format_window_preview(preview: dict[str, Any]) -> str:
    """Purpose: render one ``windows_preview_for_targets`` entry as a single human-readable line.

    Args:
        preview (dict[str, Any]): One entry produced by ``windows_preview_for_targets``.

    Returns:
        str: ``"window <id>: <closing>/<total> tabs to close — first: \"<title>\", last:
        \"<title>\""`` — the exact first/last recognition context KπX asked for in the HITL
        overlay, never just a bare window id or opaque target_ids.

    Examples:
        >>> format_window_preview({"window_id": 1, "target_count": 2, "tab_count": 2, \
"first": {"title": "A", "url": "https://a"}, "last": {"title": "B", "url": "https://b"}})
        'window 1: 2/2 tabs to close — first: "A", last: "B"'
        >>> format_window_preview({"window_id": 2, "target_count": 1, "tab_count": 1, \
"first": None, "last": None})
        'window 2: 1/1 tabs to close — first: (empty), last: (empty)'
    """
    first = preview.get("first")
    last = preview.get("last")
    first_desc = f'"{first["title"]}"' if first else "(empty)"
    last_desc = f'"{last["title"]}"' if last else "(empty)"
    return (
        f"window {preview['window_id']}: {preview['target_count']}/{preview['tab_count']} "
        f"tabs to close — first: {first_desc}, last: {last_desc}"
    )


async def _window_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: list Edge windows with real bounds, tabs, AND the canonical tab/group structure.

    Args:
        payload (dict[str, Any]): Object containing a started ``profile`` name.
        context (DaemonContext): Daemon state used to resolve that profile and, when connected,
            the paired extension.

    Returns:
        dict[str, Any]: Profile name and one entry per REAL Edge window (``window_id``, ``bounds``,
        ``tabs`` — the pre-existing raw CDP target list, unchanged shape) plus ``chrome_layout``:
        ``{"tabs", "groups", "order"}`` from the SAME canonical computation ``group-list``/
        ``tab-update``/``group-add-tabs`` use (see ``profile_state``-style single-source discipline),
        with each chrome tab correlated to its CDP ``target_id`` (see ``_correlate_cdp_targets``).
        ``chrome_layout`` is ``None`` when the extension is not connected for this profile — CDP
        alone has no concept of tab groups or real tab order (``Target.getTargets`` carries neither
        an ``index`` nor a ``groupId``), so this is an honest degradation, never a silent guess.
        Grouping is resolved via one genuine ``Browser.getWindowForTarget`` call per tab (see
        ``_window_id_for_target``), not guessed: a flat list (the previous implementation, byte-
        identical to ``tab-list``) could never answer "which tab is in which window."

    Examples:
        >>> _window_list.__name__
        '_window_list'
        >>> callable(_window_list)
        True
    """
    name, browser = _profile(payload, context)
    order: list[int | None] = []
    grouped: dict[int | None, dict[str, Any]] = {}
    for target in await _page_targets(browser):
        window_id, bounds = await _window_id_for_target(browser, target["targetId"])
        if window_id not in grouped:
            grouped[window_id] = {"window_id": window_id, "bounds": bounds, "tabs": []}
            order.append(window_id)
        grouped[window_id]["tabs"].append(target)

    chrome_windows: dict[int, dict[str, Any]] = {}
    try:
        layout_reply = await context.extension_request("window.layout", {"profile": name}, name)
        windows_field = layout_reply.get("windows")
        if isinstance(windows_field, dict):
            for key, layout in windows_field.items():
                try:
                    chrome_windows[int(key)] = layout
                except (TypeError, ValueError):
                    continue
    except RuntimeError:
        chrome_windows = {}

    windows: list[dict[str, Any]] = []
    for window_id in order:
        entry = grouped[window_id]
        chrome_layout = chrome_windows.get(window_id) if window_id is not None else None
        if chrome_layout is None:
            entry["chrome_layout"] = None
        else:
            chrome_tabs = list(chrome_layout.get("tabs", []))
            _correlate_cdp_targets(entry["tabs"], chrome_tabs)
            entry["chrome_layout"] = {
                "tabs": chrome_tabs,
                "groups": chrome_layout.get("groups", {}),
                "order": chrome_layout.get("order", []),
            }
        windows.append(entry)
    return {"profile": name, "windows": windows}


async def _create_window_tab(
    browser: CdpBrowser,
    context: DaemonContext,
    profile: str,
    window_id: int | None,
    url: str,
    capture_chrome_id: bool,
    *,
    new_window: bool = False,
) -> tuple[str, int | None]:
    """Purpose: create ONE tab — inside an existing window, in a genuinely new one, or the current
    one — optionally capturing its REAL chrome tab id. The single, centralized tab-creation
    primitive shared by ``window-create``'s layout builder AND ``tab-create`` (KπX directive:
    "centralise vraiment tout cela" — never two independently-written ways to create a tab).

    Args:
        browser (CdpBrowser): Client bound to one managed Edge debugging port.
        context (DaemonContext): Daemon state exposing the paired extension.
        profile (str): Target browser-proxy profile (routes the capture request correctly).
        window_id (int | None): Real Edge window to open the tab in, from ``Browser.getWindowForTarget``.
            Mutually exclusive with ``new_window`` — the caller validates this, never both truthy.
        url (str): Absolute page URL for the new tab.
        capture_chrome_id (bool): Whether a real ``chrome.tabs.Tab.id`` must be resolved — needed
            whenever this tab will be grouped or repositioned afterwards (``chrome.tabs.group``/
            ``chrome.tabs.move`` require the extension's own numeric id, never the CDP ``targetId``
            string).
        new_window (bool): Force a genuinely new Edge window instead of the current one; ignored
            (never sent) when ``window_id`` is given.

    Returns:
        tuple[str, int | None]: CDP ``target_id`` and, when requested, the real chrome tab id
        (``None`` if capture was not requested or the extension never reported one).

    Examples:
        >>> asyncio.iscoroutinefunction(_create_window_tab)
        True
        >>> callable(_create_window_tab)
        True
    """
    options: dict[str, Any] = {"url": url}
    if window_id is not None:
        options["windowId"] = window_id
    elif new_window:
        options["newWindow"] = True
    if not capture_chrome_id:
        result = await browser.call("Target.createTarget", options)
        return result["targetId"], None
    capture_task = asyncio.ensure_future(
        context.extension_request(
            "tab.capture_next", {"profile": profile, "timeout_seconds": 20}, profile
        )
    )
    result = await browser.call("Target.createTarget", options)
    capture = await capture_task
    chrome_tab_id = capture.get("tab_id")
    return result["targetId"], (chrome_tab_id if isinstance(chrome_tab_id, int) else None)


async def _apply_window_layout(
    layout: Any, browser: CdpBrowser, context: DaemonContext, profile: str, window_id: int | None
) -> list[dict[str, Any]]:
    """Purpose: create a whole ordered tab/group layout inside one window from a flat JSON list.

    Args:
        layout (Any): Untrusted ``window-create``/``layout`` payload value — REQUIRED, non-empty
            (a window with zero tabs cannot exist). The SAME shape and field name as
            ``group-sync``'s ``layout`` — one uniform vocabulary for "describe a window's tab/group
            content", never a second, differently-shaped way to say the same thing.
        browser (CdpBrowser): Client bound to one managed Edge debugging port.
        context (DaemonContext): Daemon state exposing the paired extension (needed for groups).
        profile (str): Target browser-proxy profile.
        window_id (int | None): Real Edge window every created tab/group must land in.

    Returns:
        list[dict[str, Any]]: One entry per item in the exact order given. Each entry is
        ``{"type": "tab", "url", "target_id"}`` or ``{"type": "group", "title", "tabs": [...],
        "group": <extension-confirmed result>}``.

    Raises:
        ValueError: If ``layout``, or any entry inside it, is not shaped as documented, or empty.

    Examples:
        >>> asyncio.iscoroutinefunction(_apply_window_layout)
        True
        >>> callable(_apply_window_layout)
        True
    """
    if not isinstance(layout, list) or not layout:
        raise ValueError("layout must be a non-empty list of {type: 'tab'|'group', ...} objects")
    created: list[dict[str, Any]] = []
    for index, item in enumerate(layout):
        if not isinstance(item, dict) or "type" not in item:
            raise ValueError(f"layout[{index}] must be an object with a 'type' field")
        kind = item["type"]
        if kind == "tab":
            url = str(item.get("url", "about:blank"))
            target_id, _ = await _create_window_tab(
                browser, context, profile, window_id, url, capture_chrome_id=False
            )
            created.append({"type": "tab", "url": url, "target_id": target_id})
        elif kind == "group":
            tabs_spec = item.get("tabs")
            if not isinstance(tabs_spec, list) or not tabs_spec:
                raise ValueError(
                    f"layout[{index}] of type 'group' requires a non-empty 'tabs' list"
                )
            chrome_tab_ids: list[int] = []
            tab_entries: list[dict[str, Any]] = []
            for tab_index, tab_item in enumerate(tabs_spec):
                if isinstance(tab_item, dict):
                    tab_url = str(tab_item.get("url", "about:blank"))
                elif isinstance(tab_item, str):
                    tab_url = tab_item
                else:
                    raise ValueError(
                        f"layout[{index}].tabs[{tab_index}] must be a URL or {{'url': ...}}"
                    )
                target_id, chrome_tab_id = await _create_window_tab(
                    browser, context, profile, window_id, tab_url, capture_chrome_id=True
                )
                if chrome_tab_id is None:
                    raise RuntimeError(
                        f"EXTENSION_UNAVAILABLE: {profile} (could not capture a real tab id for "
                        f"layout[{index}].tabs[{tab_index}] — grouping requires the paired extension)"
                    )
                chrome_tab_ids.append(chrome_tab_id)
                tab_entries.append(
                    {"url": tab_url, "target_id": target_id, "chrome_tab_id": chrome_tab_id}
                )
            group_payload: dict[str, Any] = {"profile": profile, "tab_ids": chrome_tab_ids}
            if "title" in item:
                group_payload["title"] = item["title"]
            if "color" in item:
                group_payload["color"] = item["color"]
            group_result = await context.extension_request("group.create", group_payload, profile)
            created.append(
                {
                    "type": "group",
                    "title": item.get("title"),
                    "tabs": tab_entries,
                    "group": group_result,
                }
            )
        else:
            raise ValueError(f"layout[{index}].type must be 'tab' or 'group', got {kind!r}")
    return created


@require_preflight("profile", "layout")
async def _window_create(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: create a visible Edge window and lay out its whole ordered tab/group content.

    Args:
        payload (dict[str, Any]): Profile, optional CDP window bounds/state, and the REQUIRED,
            non-empty ``layout`` — e.g. ``[{"type":"tab","url":"..."}, {"type":"group","title":
            "...","tabs":["...","..."]}]`` — created in that exact order. One single, uniform way
            to describe a window's content, the SAME field name and entry shape as ``group-sync``
            — never a separate top-level ``url`` for "the first tab" alongside a batch field for
            "the rest" (root-caused, KπX directive: that split was itself the exact unclean,
            patched-on-afterward duplication this whole tab/group refonte set out to remove).
        context (DaemonContext): Daemon state used to resolve the Edge profile and, only when
            ``layout`` contains a group, the paired extension.

    Returns:
        dict[str, Any]: Profile, the window's real ``window_id`` (from
        ``Browser.getWindowForTarget`` — see ``## Window grouping``), and the ordered ``layout``
        creation result.

    Notes:
        Deliberately NOT ``@require_approval`` (KπX directive): every managed Edge window is
        already always real and visible (never headless — see ``## Edge lifecycle``), so opening
        one is directly observable the instant it happens; it carries no hidden side effect an
        approval overlay would meaningfully gate. Because the parent action is itself
        approval-free, the tabs/groups created via ``layout`` bypass ``tab-create``'s/
        ``group-create``'s own individual approval gates too — this whole layout is one single
        deliberate command, not a series of separately-approved ones. Mechanically: the window is
        first created with a disposable ``about:blank`` placeholder tab (needed because
        ``Target.createTarget`` always requires an initial URL) so its real ``window_id`` can be
        resolved BEFORE any real tab/group exists; every ``layout`` entry then lands in that SAME
        window; the placeholder is closed last, once at least one real tab already exists — never
        left behind as a stray extra tab.

    Examples:
        >>> _window_create.__name__
        '_window_create'
        >>> callable(_window_create)
        True
    """
    name, browser = _profile(payload, context)
    options: dict[str, Any] = {"url": "about:blank", "newWindow": True}
    for key in ("left", "top", "width", "height", "windowState", "focus"):
        if key in payload:
            options[key] = payload[key]
    result = await browser.call("Target.createTarget", options)
    placeholder_target_id = result["targetId"]
    window_id, _bounds = await _window_id_for_target(browser, placeholder_target_id)
    layout_result = await _apply_window_layout(
        payload.get("layout"), browser, context, name, window_id
    )
    await browser.call("Target.closeTarget", {"targetId": placeholder_target_id})
    return {"profile": name, "window_id": window_id, "layout": layout_result}


@require_approval
@require_preflight("profile", "target_ids")
async def _window_close(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: close one or many Edge targets (tabs/windows) by CDP target identifier, in ONE call.

    Args:
        payload (dict[str, Any]): Profile and required, non-empty ``target_ids`` list — closing
            several tabs/windows across the profile is ONE deliberate command with ONE approval,
            never N separate ``do window-close`` calls each needing its own approval round-trip
            (root-caused, KπX directive: too slow/tedious in practice, confirmed live).
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, the exact ``target_ids`` closed, and a confirmed close intent.

    Examples:
        >>> _window_close.__name__
        '_window_close'
        >>> callable(_window_close)
        True
    """
    name, browser = _profile(payload, context)
    target_ids = payload.get("target_ids")
    if not isinstance(target_ids, list) or not target_ids:
        raise ValueError("target_ids must be a non-empty list of CDP target ids")
    closed: list[str] = []
    for target_id in target_ids:
        await browser.call("Target.closeTarget", {"targetId": str(target_id)})
        closed.append(str(target_id))
    return {"profile": name, "target_ids": closed, "closed": True}


@require_approval
@require_preflight("profile", "window_id")
async def _window_sync(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: reorganize an EXISTING window's own properties AND its whole tab/group content in
    ONE call — the window-level equivalent of ``group-sync``, "très complet et flexible" (KπX).

    Args:
        payload (dict[str, Any]): Profile, required ``window_id`` (the REAL Edge window to target),
            optional ``bounds`` (``{"left","top","width","height"}``, any subset), optional
            ``state`` (``"normal"|"maximized"|"minimized"|"fullscreen"``), optional ``focused``
            (bool), optional ``layout`` (the SAME ordered tab/group schema ``window-create``/
            ``group-sync`` use — reused unchanged, never a second, differently-shaped vocabulary
            for "describe a window's content").
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: ``window_id``, the extension-confirmed window properties after any
        requested ``bounds``/``state``/``focused`` change, and ``layout`` (present only if a
        ``layout`` was given) with the SAME shape ``group-sync`` returns.

    Raises:
        ValueError: No field beyond ``profile``/``window_id`` given at all (a no-op call).

    Notes:
        HITL-gated (KπX directive, GRAVÉ): reorganizing a whole window's structure — its own
        bounds/state and/or its entire tab/group content — is treated as one deliberate, reviewable
        command, same as its sibling ``group-sync``.

    Examples:
        >>> _window_sync.__name__
        '_window_sync'
        >>> callable(_window_sync)
        True
    """
    window_id = int(payload["window_id"])
    profile = str(payload.get("profile", "default"))
    wants_window_update = any(key in payload for key in ("bounds", "state", "focused"))
    layout = payload.get("layout")
    if not wants_window_update and not layout:
        raise ValueError("window-sync requires at least one of: bounds, state, focused, layout")
    result: dict[str, Any] = {"window_id": window_id}
    if wants_window_update:
        update_payload: dict[str, Any] = {"profile": profile, "window_id": window_id}
        for field in ("bounds", "state", "focused"):
            if field in payload:
                update_payload[field] = payload[field]
        window_result = await context.extension_request("window.update", update_payload, profile)
        result.update(window_result)
    if layout is not None:
        layout_result = await context.extension_request(
            "group.sync", {"profile": profile, "layout": layout}, profile
        )
        result["layout"] = layout_result.get("layout", [])
    return result


def _load_saved_windows(profile: str) -> dict[str, Any]:
    """Purpose: read one profile's persisted saved-window snapshots from disk.

    Args:
        profile (str): Target browser-proxy profile — one JSON file per profile, never shared.

    Returns:
        dict[str, Any]: ``{name: {"saved_at", "bounds", "layout"}}``, or ``{}`` for a profile with
        no saved windows yet, or a genuinely corrupt file — never raises, a saved-window store is
        additive convenience state, not a source of truth the daemon must trust unconditionally.

    Examples:
        >>> isinstance(_load_saved_windows("does-not-exist-xyz"), dict)
        True
        >>> _load_saved_windows.__name__
        '_load_saved_windows'
    """
    path = saved_windows_path(profile)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_saved_windows(profile: str, store: dict[str, Any]) -> None:
    """Purpose: persist one profile's saved-window snapshots to disk.

    Args:
        profile (str): Target browser-proxy profile.
        store (dict[str, Any]): Complete replacement mapping (never a partial merge — callers
            read-modify-write the full dict via ``_load_saved_windows`` first).

    Returns:
        None. Creates the parent directory (mode ``0o700``, same as an Edge profile directory)
        the first time a profile saves a window.

    Examples:
        >>> callable(_write_saved_windows)
        True
        >>> _write_saved_windows.__name__
        '_write_saved_windows'
    """
    path = saved_windows_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(store, indent=2))


def _layout_from_chrome_layout(chrome_layout: dict[str, Any]) -> list[dict[str, Any]]:
    """Purpose: convert a REAL window's canonical `chrome_layout` into a `window-create`-shaped
    `layout` (URLs, not live ids) — the one conversion `window-save` needs to turn a live window
    into a re-creatable snapshot.

    Args:
        chrome_layout (dict[str, Any]): One window's `window-list`-style `chrome_layout` (`tabs`,
            `groups`, `order` — see `_window_list`/`computeWindowLayouts`).

    Returns:
        list[dict[str, Any]]: Same shape `window-create`/`group-sync` accept: `{"type":"tab",
        "url":...}` or `{"type":"group","title":...,"color"?:...,"tabs":[url,...]}`, in the exact
        real visual order. A tab/group whose member tabs cannot be resolved to a URL is skipped
        (never a broken entry with a missing field) rather than aborting the whole snapshot.

    Examples:
        >>> _layout_from_chrome_layout({"tabs": [{"chrome_tab_id": 1, "url": "https://a.example"}], \
"groups": {}, "order": [{"kind": "tab", "chrome_tab_id": 1}]})
        [{'type': 'tab', 'url': 'https://a.example'}]
        >>> _layout_from_chrome_layout({"tabs": [], "groups": {}, "order": []})
        []
    """
    urls_by_tab_id: dict[int, str] = {
        tab["chrome_tab_id"]: str(tab.get("url", "")) for tab in chrome_layout.get("tabs", [])
    }
    layout: list[dict[str, Any]] = []
    for entry in chrome_layout.get("order", []):
        if entry.get("kind") == "tab":
            url = urls_by_tab_id.get(entry.get("chrome_tab_id"))
            if url:
                layout.append({"type": "tab", "url": url})
        elif entry.get("kind") == "group":
            tabs = [
                urls_by_tab_id[tab_id]
                for tab_id in entry.get("tabs", [])
                if tab_id in urls_by_tab_id
            ]
            if not tabs:
                continue
            group_entry: dict[str, Any] = {
                "type": "group",
                "title": str(entry.get("title", "")),
                "tabs": tabs,
            }
            if entry.get("color"):
                group_entry["color"] = entry["color"]
            layout.append(group_entry)
    return layout


@require_preflight("profile", "saves")
async def _window_save(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: snapshot one or MORE real windows' full structure to disk, batch, by name — the
    save half of a browser-proxy-native "workspace" (KπX, GRAVÉ: real Edge Workspaces expose no
    programmatic API at all — see `## Workspaces`; this is our own honest substitute).

    Args:
        payload (dict[str, Any]): Profile and required, non-empty `saves`: a list of
            `{"window_id": int, "name": str}` — several windows saved in ONE call, each under its
            own name, never one call per window.
        context (DaemonContext): Daemon state used to resolve the profile and the paired
            extension (required — a snapshot with no real group/order context would be a lie).

    Returns:
        dict[str, Any]: Profile and, per entry, `{"name", "window_id", "tab_count"}` confirming
        what was actually saved.

    Raises:
        ValueError: `saves` malformed or empty, or an entry missing `window_id`/`name`.
        RuntimeError: `CDP_UNAVAILABLE` for a `window_id` that does not exist right now;
            `EXTENSION_UNAVAILABLE` if that window's real tab/group structure cannot be read.

    Notes:
        Deliberately NOT `@require_approval` — reading a window's current structure and writing it
        to a local JSON file touches nothing live in the browser, no different in kind from
        `bookmark-create`'s own non-secret persistence. Re-saving an existing name overwrites it
        (a save slot, not an append-only log — matches the operator's own mental model of "save").

    Examples:
        >>> _window_save.__name__
        '_window_save'
        >>> callable(_window_save)
        True
    """
    profile = str(payload.get("profile", "default"))
    saves = payload.get("saves")
    if not isinstance(saves, list) or not saves:
        raise ValueError("saves must be a non-empty list of {window_id, name}")
    window_view = await _window_list(payload, context)
    windows_by_id = {window["window_id"]: window for window in window_view["windows"]}
    store = _load_saved_windows(profile)
    saved: list[dict[str, Any]] = []
    for index, entry in enumerate(saves):
        if not isinstance(entry, dict) or "window_id" not in entry or "name" not in entry:
            raise ValueError(f"saves[{index}] must be an object with window_id and name")
        window_id = int(entry["window_id"])
        name = str(entry["name"])
        window = windows_by_id.get(window_id)
        if window is None:
            raise RuntimeError(f"CDP_UNAVAILABLE: no live window {window_id} in profile {profile}")
        chrome_layout = window.get("chrome_layout")
        if chrome_layout is None:
            raise RuntimeError(
                f"EXTENSION_UNAVAILABLE: {profile} (cannot snapshot window {window_id} without "
                "the paired extension's real tab/group structure)"
            )
        store[name] = {
            "saved_at": datetime.now(UTC).isoformat(),
            "bounds": window.get("bounds", {}),
            "layout": _layout_from_chrome_layout(chrome_layout),
        }
        saved.append(
            {"name": name, "window_id": window_id, "tab_count": len(chrome_layout["tabs"])}
        )
    _write_saved_windows(profile, store)
    return {"profile": profile, "saved": saved}


@require_preflight("profile", "names")
async def _window_restore(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: reopen one or MORE saved windows by name, batch, as real new windows.

    Args:
        payload (dict[str, Any]): Profile and required, non-empty `names` — several saved windows
            restored in ONE call, never one call per window.
        context (DaemonContext): Daemon state used to resolve the profile and the paired
            extension (required whenever a saved layout includes a group).

    Returns:
        dict[str, Any]: Profile and, per name, `{"name", "window_id", "layout"}` — the SAME
        `window-create` result shape, since restoring genuinely IS `window-create` under the hood.

    Raises:
        ValueError: `names` malformed or empty.
        RuntimeError: `NOT_FOUND: <name>` for any name with no saved snapshot in this profile.

    Notes:
        Deliberately NOT `@require_approval` — same rationale as `window-create`: every restored
        window is real and visible the instant it opens, directly observable. Bounds are restored
        via `window-create`'s own flat `left`/`top`/`width`/`height`/`windowState` fields — the
        SAME convention, never a second nested shape for the same concept.

    Examples:
        >>> _window_restore.__name__
        '_window_restore'
        >>> callable(_window_restore)
        True
    """
    profile = str(payload.get("profile", "default"))
    names = payload.get("names")
    if not isinstance(names, list) or not names:
        raise ValueError("names must be a non-empty list of saved window names")
    store = _load_saved_windows(profile)
    restored: list[dict[str, Any]] = []
    for name in names:
        entry = store.get(str(name))
        if entry is None:
            raise RuntimeError(f"NOT_FOUND: no saved window named {name!r} in profile {profile}")
        create_payload: dict[str, Any] = {"profile": profile, "layout": entry["layout"]}
        bounds = entry.get("bounds") or {}
        for key in ("left", "top", "width", "height", "windowState"):
            if key in bounds:
                create_payload[key] = bounds[key]
        result = await _window_create(create_payload, context)
        restored.append(
            {"name": str(name), "window_id": result["window_id"], "layout": result["layout"]}
        )
    return {"profile": profile, "restored": restored}


@require_preflight("profile")
async def _window_saved_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: list every saved window snapshot for one profile.

    Args:
        payload (dict[str, Any]): Object containing a started `profile` name.
        context (DaemonContext): Unused — a pure local-disk read, no CDP/extension needed.

    Returns:
        dict[str, Any]: Profile and `windows`: one entry per saved name, `{"name", "saved_at",
        "bounds", "tab_count", "layout"}` — the full `layout` is included (not just a summary) so
        a caller can preview exactly what `window-restore` would recreate before running it.

    Examples:
        >>> _window_saved_list.__name__
        '_window_saved_list'
        >>> callable(_window_saved_list)
        True
    """
    profile = str(payload.get("profile", "default"))
    store = _load_saved_windows(profile)
    windows = [
        {
            "name": name,
            "saved_at": entry.get("saved_at"),
            "bounds": entry.get("bounds", {}),
            "tab_count": sum(
                len(item["tabs"]) if item["type"] == "group" else 1
                for item in entry.get("layout", [])
            ),
            "layout": entry.get("layout", []),
        }
        for name, entry in store.items()
    ]
    return {"profile": profile, "windows": windows}


@require_preflight("profile", "names")
async def _window_saved_remove(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: permanently delete one or MORE saved window snapshots by name, batch.

    Args:
        payload (dict[str, Any]): Profile and required, non-empty `names` — several removed in
            ONE call, never one call per snapshot; every name must exist, explicitly, no wildcard
            "delete all" shortcut.
        context (DaemonContext): Unused — a pure local-disk write, no CDP/extension needed.

    Returns:
        dict[str, Any]: Profile and `removed`: the exact names deleted.

    Raises:
        ValueError: `names` malformed or empty.
        RuntimeError: `NOT_FOUND: <name>` for any name that does not exist — checked for ALL names
            BEFORE deleting any of them, so a batch call is all-or-nothing, never a partial delete
            silently leaving the store in a state the caller never asked for.

    Notes:
        Deliberately NOT `@require_approval` — same rationale as `profile-remove`: this never
        touches a live browser window at all (pure local JSON file edit), so gating it behind an
        extension overlay would add a dependency the action does not actually need; the locked,
        explicit-name-only identity (no wildcard) is the real safety net here, same admin-tier
        pattern as `profile-remove`.

    Examples:
        >>> _window_saved_remove.__name__
        '_window_saved_remove'
        >>> callable(_window_saved_remove)
        True
    """
    profile = str(payload.get("profile", "default"))
    names = payload.get("names")
    if not isinstance(names, list) or not names:
        raise ValueError("names must be a non-empty list of saved window names")
    store = _load_saved_windows(profile)
    for name in names:
        if str(name) not in store:
            raise RuntimeError(f"NOT_FOUND: no saved window named {name!r} in profile {profile}")
    for name in names:
        del store[str(name)]
    _write_saved_windows(profile, store)
    return {"profile": profile, "removed": [str(name) for name in names]}


async def _tabs_with_context(
    payload: dict[str, Any], context: DaemonContext
) -> tuple[str, list[dict[str, Any]]]:
    """Purpose: build the flat per-tab view enriched with its REAL window and group/folder context.

    Args:
        payload (dict[str, Any]): Object containing a started ``profile`` name.
        context (DaemonContext): Daemon state used to resolve that profile and, when connected,
            the paired extension.

    Returns:
        tuple[str, list[dict[str, Any]]]: Profile name and one entry per real page target, each
        the pre-existing ``Target.getTargets`` shape PLUS ``window_id`` (the real Edge window it
        lives in — never omitted, resolved via the SAME ``Browser.getWindowForTarget`` mechanism
        ``window-list`` uses) and ``group_id``/``group_title`` (the real Edge tab-group/"folder" it
        is in, both ``None`` when ungrouped or when the extension is not connected — an honest
        degradation, never a silent guess). Root-caused live (KπX): "tab-list doit indiquer ds
        quel fenêtre est la tab, ds quel dossier c'est si c'est ds un dossier" — a flat tab list
        with zero window/folder context gave no way to recognize which tab is which without
        cross-referencing `window-list` by hand. Reuses `_window_list` entirely rather than a
        second, independently-computed view (single source of truth for window/group grouping).

    Examples:
        >>> asyncio.iscoroutinefunction(_tabs_with_context)
        True
        >>> callable(_tabs_with_context)
        True
    """
    window_view = await _window_list(payload, context)
    tabs: list[dict[str, Any]] = []
    for window in window_view["windows"]:
        chrome_layout = window.get("chrome_layout")
        groups: dict[str, Any] = chrome_layout.get("groups", {}) if chrome_layout else {}
        chrome_by_target: dict[str | None, dict[str, Any]] = {}
        if chrome_layout:
            for chrome_tab in chrome_layout.get("tabs", []):
                chrome_by_target[chrome_tab.get("target_id")] = chrome_tab
        for tab in window["tabs"]:
            entry = dict(tab)
            entry["window_id"] = window["window_id"]
            chrome_tab = chrome_by_target.get(tab["targetId"])
            group_id = chrome_tab.get("group_id") if chrome_tab else None
            entry["group_id"] = group_id
            entry["group_title"] = (
                groups.get(str(group_id), {}).get("title") if group_id is not None else None
            )
            tabs.append(entry)
    return window_view["profile"], tabs


async def _tab_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: list all page tabs in a persistent Edge profile, with real window+folder context.

    Args:
        payload (dict[str, Any]): Object containing a started ``profile`` name.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: Profile name and its Edge page targets as tabs, each carrying
        ``window_id``/``group_id``/``group_title`` (see ``_tabs_with_context``).

    Examples:
        >>> _tab_list.__name__
        '_tab_list'
        >>> callable(_tab_list)
        True
    """
    profile, tabs = await _tabs_with_context(payload, context)
    return {"profile": profile, "tabs": tabs}


async def _tab_get(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: read ALL available information about ONE tab in a single comprehensive call.

    Args:
        payload (dict[str, Any]): Profile and required CDP ``target_id``.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: Profile name and one merged ``tab`` object — the real, raw
        ``Target.getTargetInfo`` CDP metadata (title, url, type, attached, ``browserContextId``,
        ...) PLUS the SAME ``window_id``/``group_id``/``group_title`` context ``tab-list`` exposes.
        Replaces the former ``page-get`` (KπX directive, GRAVÉ: "tab = page ... je ne veux pas de
        duplication inutile" — ``page-get`` was a second, narrower "get one tab's identity" action
        with zero window/group context; merged here as the single source, never two overlapping
        ways to read one tab's identity).

    Raises:
        ValueError: ``target_id`` missing.
        RuntimeError: ``CDP_UNAVAILABLE: ...`` when no live tab matches ``target_id``.

    Examples:
        >>> _tab_get.__name__
        '_tab_get'
        >>> callable(_tab_get)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    _, tabs = await _tabs_with_context(payload, context)
    match = next((tab for tab in tabs if tab.get("targetId") == target_id), None)
    if match is None:
        raise RuntimeError(f"CDP_UNAVAILABLE: no live tab found for target_id {target_id}")
    raw = await browser.call("Target.getTargetInfo", {"targetId": target_id})
    return {"profile": name, "tab": {**match, **raw.get("targetInfo", {})}}


@require_preflight("profile")
@require_verification("url")
async def _tab_create(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: create a tab in Edge — as fine-grained as the possibilities allow: optionally in a
    specific EXISTING window, optionally into an existing group/folder, optionally at a precise
    position — never just "create somewhere and hope it lands right" (KπX directive: "on doit être
    le plus fin possible... donner l'illusion d'un aspect esthétique visuel, pas juste créer du
    bullshit").

    Args:
        payload (dict[str, Any]): Profile, ``url``, optional ``new_window`` (bool — a genuinely new
            window instead of the current one), optional ``window_id`` (open directly in that
            EXISTING window instead — mutually exclusive with ``new_window``), optional ``group_id``
            (add the new tab into that EXISTING group/folder the instant it is created), and at
            most one position hint (``index``/``before_tab_id``/``after_tab_id``, same convention
            as ``tab-update``).
        context (DaemonContext): Daemon state used to resolve the profile and, when ``group_id`` or
            a position is requested, the paired extension (to capture the real chrome tab id and
            apply the placement via the SAME ``tab.update`` mechanism ``tab-update`` uses).

    Returns:
        dict[str, Any]: Profile, CDP ``target_id``, requested ``url`` — plus, whenever a real
        chrome tab id was resolved, ``tab_id`` and (when a placement was requested) the
        extension-confirmed ``window_id``/``group_id``/``index`` after that placement.

    Raises:
        ValueError: Both ``new_window`` and ``window_id`` given (ambiguous intent).

    Notes:
        Deliberately NOT ``@require_approval`` (KπX directive, same rationale as
        ``window-create``): opening a tab in an always-visible managed Edge window is directly
        observable the instant it happens. Still preflight-``profile`` and verify-``url``.

    Examples:
        >>> _tab_create.__name__
        '_tab_create'
        >>> callable(_tab_create)
        True
    """
    name, browser = _profile(payload, context)
    url = str(payload.get("url", "about:blank"))
    new_window = bool(payload.get("new_window", False))
    raw_window_id = payload.get("window_id")
    if new_window and raw_window_id is not None:
        raise ValueError("new_window and window_id are mutually exclusive")
    group_id = payload.get("group_id")
    position_fields = ("index", "before_tab_id", "after_tab_id")
    wants_position = any(payload.get(field) is not None for field in position_fields)
    needs_chrome_id = group_id is not None or wants_position

    target_id, chrome_tab_id = await _create_window_tab(
        browser,
        context,
        name,
        int(raw_window_id) if raw_window_id is not None else None,
        url,
        capture_chrome_id=needs_chrome_id,
        new_window=new_window,
    )
    outcome: dict[str, Any] = {"profile": name, "target_id": target_id, "url": url}
    if chrome_tab_id is None:
        return outcome
    outcome["tab_id"] = chrome_tab_id
    update_fields: dict[str, Any] = {}
    if group_id is not None:
        update_fields["group_id"] = group_id
    for field in position_fields:
        if payload.get(field) is not None:
            update_fields[field] = payload[field]
    if not update_fields:
        return outcome
    updated = await context.extension_request(
        "tab.update", {"profile": name, "tab_id": chrome_tab_id, **update_fields}, name
    )
    outcome["window_id"] = updated.get("window_id")
    outcome["group_id"] = updated.get("group_id")
    outcome["index"] = updated.get("index")
    return outcome


@require_preflight("profile", "target_id")
async def _tab_activate(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: activate an existing Edge tab target — bring it to the front as the visible tab.

    Args:
        payload (dict[str, Any]): Profile and required CDP ``target_id``.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: Profile, activated target ID, and activation state.

    Notes:
        Deliberately NOT ``@require_approval`` anymore (KπX directive, root-caused live): activating
        an already-open, already-visible tab is directly observable the instant it happens — same
        rationale as ``tab-create``/``window-create``/``tab-update``. Its role is purely navigational
        focus (bring a specific already-open tab to the front, e.g. to make it the active tab before
        a screenshot/interaction, or to surface a background tab KπX should look at) — it never
        creates, closes, or mutates any content, so it carries no more risk than looking at a window.

    Examples:
        >>> _tab_activate.__name__
        '_tab_activate'
        >>> callable(_tab_activate)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    await browser.call("Target.activateTarget", {"targetId": target_id})
    return {"profile": name, "target_id": target_id, "active": True}


@require_preflight("tab_id")
async def _tab_update(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: the ONE fine-grained way to change anything about one real, already-open tab — its
    url, its window, its group/folder, or its position — any subset of these, in one call.

    Args:
        payload (dict[str, Any]): Profile, required ``tab_id`` (the REAL numeric
            ``chrome.tabs.Tab.id`` — see ``window-list``'s ``chrome_layout``/``group-list``, never
            a CDP ``target_id``), and ANY combination of: ``url`` (navigate this tab in place),
            ``window_id`` (move it to another window), ``group_id`` (move it into that
            group/folder — explicit ``null`` removes it from its current group), and AT MOST ONE
            of ``index`` (``-1`` moves to the end), ``before_tab_id``, or ``after_tab_id`` — the
            same primitive a mouse drag performs.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed ``{tab_id, url, index, window_id, group_id}`` reflecting
        the tab's real state after every requested change.

    Raises:
        ValueError: No field beyond ``tab_id`` given at all (a no-op call is rejected, never
            silently accepted).

    Notes:
        Renamed from ``tab-move`` (KπX directive, GRAVÉ: "renomme en tab-update... url, window,
        folder, index... centralise vraiment tout cela pour redistribuer partout cette philo de
        fin ajustement") — one deliberate command for every fine, surgical adjustment a tab can
        need, never N separate primitive calls for what is conceptually ONE placement decision.
        Deliberately NOT ``@require_approval`` — adjusting an already-visible tab is directly
        observable the instant it happens, the same rationale as ``window-create``/``tab-create``.

    Examples:
        >>> _tab_update.__name__
        '_tab_update'
        >>> callable(_tab_update)
        True
    """
    fields = ("url", "window_id", "group_id", "index", "before_tab_id", "after_tab_id")
    if not any(field in payload for field in fields):
        raise ValueError("tab-update requires at least one of: " + ", ".join(fields))
    return await _extension(payload, context, "tab.update")


async def _group_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: list Edge tab-group hints for one profile through the extension bridge.

    Args:
        payload (dict[str, Any]): Object identifying the Edge profile to inspect.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Profile-scoped group metadata, explicitly heuristic where applicable.

    Examples:
        >>> _group_list.__name__
        '_group_list'
        >>> callable(_group_list)
        True
    """
    return await _extension(payload, context, "group.list")


async def _extension(payload: dict[str, Any], context: DaemonContext, kind: str) -> dict[str, Any]:
    """Purpose: send a typed request to the ONE Edge extension declaring the requested profile.

    Args:
        payload (dict[str, Any]): Complete single action object forwarded unchanged; its
            ``profile`` field selects the target connection (defaults to ``default``).
        context (DaemonContext): Daemon state exposing the extension bridge.
        kind (str): Extension request kind, for example ``bookmark.list``.

    Returns:
        dict[str, Any]: Typed extension response data with ``profile`` echoed back, confirming
        which profile actually answered (never silently the wrong one — every profile is its own
        isolated extension install with its own ``chrome.storage.local``, so a request for profile
        "research" can never be transparently answered by profile "default"'s extension).

    Examples:
        >>> _extension.__name__
        '_extension'
        >>> callable(_extension)
        True
    """
    profile = str(payload.get("profile", "default"))
    data = await context.extension_request(kind, payload, profile)
    return {**data, "profile": profile}


async def _bookmark_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: reveal the REAL Edge bookmark folder/subfolder tree, filesystem-like, through the
    paired privileged extension (KπX, GRAVÉ: "les bookmarks sont carrément comme un système de
    fichier avec dossier sous dossier... le list doit bien révéler cela").

    Args:
        payload (dict[str, Any]): Profile, optional ``depth`` (int, non-negative, or ``None``)
            capping how many levels below the returned roots are included — omitted or ``None``
            returns the full tree, unbounded — and optional ``root_id`` (an existing real folder
            id) scoping the WHOLE call to just that one subfolder instead of the top-level roots
            (``Bookmarks bar``/``Other bookmarks``/``Mobile bookmarks``); ``depth`` then counts
            from THAT folder.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: ``{depth, roots: [...]}`` — a real nested tree (never a flat dump); each
        node carries ``id``, ``title``, ``type`` (``"folder"``/``"bookmark"``), ``url``,
        ``parent_id``, ``index``, and, for folders only, a real ``children`` list (possibly empty
        once ``depth`` truncates it). Without ``root_id``, ``roots`` holds the top-level folders;
        with ``root_id``, ``roots`` is a single-element list holding just that one requested
        folder — kept as a list either way, so callers never special-case the two modes.

    Examples:
        >>> _bookmark_list.__name__
        '_bookmark_list'
        >>> callable(_bookmark_list)
        True
    """
    return await _extension(payload, context, "bookmark.list")


async def _bookmark_get(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: read ALL available real information about ONE bookmark or folder in a single
    call, through the paired extension — same "everything about ONE X" philosophy as ``tab-get``,
    extended to bookmarks (KπX, GRAVÉ: "un truc bookmark-get qui affiche toutes les infos sur un
    bookmark donné").

    Args:
        payload (dict[str, Any]): Profile and required ``id`` (a real bookmark or folder id).
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed ``{id, title, type, url, parent_id, parent_title,
        index, date_added}`` always, plus, for a folder, ``{date_group_modified, children_count,
        children_preview: {first, last}|None}``; for a leaf bookmark, ``date_last_used`` instead —
        never the full subtree (see ``bookmark-list`` for that).

    Examples:
        >>> _bookmark_get.__name__
        '_bookmark_get'
        >>> callable(_bookmark_get)
        True
    """
    return await _extension(payload, context, "bookmark.get")


@require_approval
@require_preflight("items")
async def _bookmark_create(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: create one or MORE real bookmarks/folders through the paired extension, batch, in
    ONE call, with absolute placement finesse (KπX, GRAVÉ: "le create doit vraiment permettre de
    bien placer ds ce système... être lancé en batch, faire plusieurs d'un coup").

    Args:
        payload (dict[str, Any]): Profile and required, non-empty ``items``: an ORDERED list of
            ``{"type": "folder"|"bookmark", "title", "url"? (bookmark only), "parent_id"?
            (an existing real folder id), "parent_ref"? (a LOCAL ref naming an EARLIER folder item
            in this SAME batch — mutually exclusive with ``parent_id``), "ref"? (a LOCAL name later
            items may target via their own ``parent_ref``), "index"?}`` — several bookmarks/folders
            created in ONE call, never one call per item.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed ``{created: [{ref, id, type, title, url, parent_id,
        index}, ...]}``, one entry per input item, same order.

    Examples:
        >>> _bookmark_create.__name__
        '_bookmark_create'
        >>> callable(_bookmark_create)
        True
    """
    return await _extension(payload, context, "bookmark.create")


@require_approval
@require_preflight("ids")
async def _bookmark_remove(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: permanently remove one or MORE real bookmarks/folders through the paired
    extension, batch, in ONE call — mixing a whole folder (removed WITH its subtree) and a
    standalone leaf bookmark in the SAME call is deliberate (KπX, GRAVÉ: "on peut supprimer de
    dossier sous dossier juste et élément").

    Args:
        payload (dict[str, Any]): Profile and required, non-empty ``ids``: real bookmark or folder
            identifiers, in any mix, several removed in ONE call, never one call per id.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed ``{removed: [{id, type, title, url}, ...]}`` — the real
        identity of every removed node, never a bare id echoed back blind.

    Examples:
        >>> _bookmark_remove.__name__
        '_bookmark_remove'
        >>> callable(_bookmark_remove)
        True
    """
    return await _extension(payload, context, "bookmark.remove")


@require_approval
@require_preflight("items")
async def _bookmark_update(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: the ONE fine-grained way to change anything about one or MORE existing real
    bookmarks/folders through the paired extension — rename, change url, relocate to a different
    folder, and/or reposition — any subset, batch, in ONE call (KπX, GRAVÉ: same "absolute finesse"
    philosophy as ``tab-update``, extended to bookmarks).

    Args:
        payload (dict[str, Any]): Profile and required, non-empty ``items``: a list of
            ``{"id", "title"?, "url"? (bookmark only), "parent_id"?, "index"?}`` — at least one
            field beyond ``id`` required per item (a no-op item is rejected), several updates
            applied in ONE call, never one call per item.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed ``{updated: [{id, title, url, parent_id, index}, ...]}``
        — each entry reflecting that node's REAL state after every requested change.

    Examples:
        >>> _bookmark_update.__name__
        '_bookmark_update'
        >>> callable(_bookmark_update)
        True
    """
    return await _extension(payload, context, "bookmark.update")


async def _page_navigate(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: navigate an Edge page target to a URL and wait for document readiness.

    Args:
        payload (dict[str, Any]): Profile, ``target_id``, ``url``, and optional ``wait_seconds``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, requested URL, and last observed ready state.

    Examples:
        >>> _page_navigate.__name__
        '_page_navigate'
        >>> callable(_page_navigate)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    url = str(payload.get("url", ""))
    wait_seconds = int(payload.get("wait_seconds", 10))
    await browser.page_session(target_id, [("Page.navigate", {"url": url})])
    ready_state = ""
    for _ in range(max(1, int(wait_seconds / 0.2))):
        result = await browser.page_session(
            target_id,
            [("Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True})],
        )
        ready_state = str(result[0].get("result", {}).get("value", ""))
        if ready_state == "complete":
            break
        await asyncio.sleep(0.2)
    return {"profile": name, "target_id": target_id, "url": url, "ready_state": ready_state}


async def _page_reload(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: reload an Edge page target and wait for document readiness.

    Args:
        payload (dict[str, Any]): Profile, ``target_id``, optional ``ignore_cache``/``wait_seconds``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and a confirmed reload flag.

    Examples:
        >>> _page_reload.__name__
        '_page_reload'
        >>> callable(_page_reload)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    ignore_cache = bool(payload.get("ignore_cache", False))
    wait_seconds = int(payload.get("wait_seconds", 10))
    await browser.page_session(target_id, [("Page.reload", {"ignoreCache": ignore_cache})])
    for _ in range(max(1, int(wait_seconds / 0.2))):
        result = await browser.page_session(
            target_id,
            [("Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True})],
        )
        if str(result[0].get("result", {}).get("value", "")) == "complete":
            break
        await asyncio.sleep(0.2)
    return {"profile": name, "target_id": target_id, "reloaded": True}


async def _history_step(browser: CdpBrowser, target_id: str, offset: int) -> None:
    """Purpose: move one page target's navigation history back or forward.

    Args:
        browser (CdpBrowser): Client bound to one managed Edge debugging port.
        target_id (str): CDP page target whose navigation history moves.
        offset (int): ``-1`` for back or ``1`` for forward.

    Returns:
        None: Raises ``ValueError`` when no history entry exists at that offset.

    Examples:
        >>> asyncio.iscoroutinefunction(_history_step)
        True
        >>> callable(_history_step)
        True
    """
    result = await browser.page_session(target_id, [("Page.getNavigationHistory", {})])
    history = result[0]
    entries = history.get("entries", [])
    index = int(history.get("currentIndex", 0)) + offset
    if index < 0 or index >= len(entries):
        raise ValueError("no history entry")
    entry_id = entries[index]["id"]
    await browser.page_session(target_id, [("Page.navigateToHistoryEntry", {"entryId": entry_id})])


async def _page_back(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: navigate an Edge page target one step back in its history.

    Args:
        payload (dict[str, Any]): Profile and required ``target_id``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and a confirmed navigation flag.

    Examples:
        >>> _page_back.__name__
        '_page_back'
        >>> callable(_page_back)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    await _history_step(browser, target_id, -1)
    return {"profile": name, "target_id": target_id, "navigated": True}


async def _page_forward(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: navigate an Edge page target one step forward in its history.

    Args:
        payload (dict[str, Any]): Profile and required ``target_id``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and a confirmed navigation flag.

    Examples:
        >>> _page_forward.__name__
        '_page_forward'
        >>> callable(_page_forward)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    await _history_step(browser, target_id, 1)
    return {"profile": name, "target_id": target_id, "navigated": True}


async def _resolve_box(browser: CdpBrowser, target_id: str, selector: str) -> tuple[float, float]:
    """Purpose: resolve one CSS selector's viewport center point via the DOM CDP domain.

    Args:
        browser (CdpBrowser): Client bound to one managed Edge debugging port.
        target_id (str): CDP page target searched for the selector.
        selector (str): CSS selector expected to match exactly one element.

    Returns:
        tuple[float, float]: Viewport ``(x, y)`` center of the matched element's content box.

    Examples:
        >>> asyncio.iscoroutinefunction(_resolve_box)
        True
        >>> callable(_resolve_box)
        True
    """
    document = await browser.page_session(target_id, [("DOM.getDocument", {"depth": -1})])
    root_id = document[0].get("root", {}).get("nodeId")
    node = await browser.page_session(
        target_id, [("DOM.querySelector", {"nodeId": root_id, "selector": selector})]
    )
    node_id = node[0].get("nodeId", 0)
    if not node_id:
        raise ValueError(f"element not found: {selector}")
    box = await browser.page_session(target_id, [("DOM.getBoxModel", {"nodeId": node_id})])
    quad = box[0]["model"]["content"]
    return (quad[0] + quad[4]) / 2, (quad[1] + quad[5]) / 2


async def _page_click(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: click an Edge page element resolved by CSS selector.

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, and required ``selector``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, selector, clicked coordinates, and click flag.

    Examples:
        >>> _page_click.__name__
        '_page_click'
        >>> callable(_page_click)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    selector = str(payload.get("selector", ""))
    if not target_id or not selector:
        raise ValueError("target_id and selector are required")
    center_x, center_y = await _resolve_box(browser, target_id, selector)
    await browser.page_session(
        target_id,
        [
            ("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": center_x, "y": center_y}),
            (
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": center_x,
                    "y": center_y,
                    "button": "left",
                    "clickCount": 1,
                },
            ),
            (
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": center_x,
                    "y": center_y,
                    "button": "left",
                    "clickCount": 1,
                },
            ),
        ],
    )
    return {
        "profile": name,
        "target_id": target_id,
        "selector": selector,
        "x": center_x,
        "y": center_y,
        "clicked": True,
    }


async def _page_hover(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: move the pointer over an Edge page element resolved by CSS selector.

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, and required ``selector``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, selector, hovered coordinates, and hover flag.

    Examples:
        >>> _page_hover.__name__
        '_page_hover'
        >>> callable(_page_hover)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    selector = str(payload.get("selector", ""))
    if not target_id or not selector:
        raise ValueError("target_id and selector are required")
    center_x, center_y = await _resolve_box(browser, target_id, selector)
    await browser.page_session(
        target_id,
        [("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": center_x, "y": center_y})],
    )
    return {
        "profile": name,
        "target_id": target_id,
        "selector": selector,
        "x": center_x,
        "y": center_y,
        "hovered": True,
    }


async def _page_type(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: focus an Edge page element and type text into it, optionally clearing first.

    Args:
        payload (dict[str, Any]): Profile, ``target_id``, ``selector``, ``text``, opt. ``clear``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, selector, and a confirmed typed flag.

    Examples:
        >>> _page_type.__name__
        '_page_type'
        >>> callable(_page_type)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    selector = str(payload.get("selector", ""))
    text = str(payload.get("text", ""))
    if not target_id or not selector:
        raise ValueError("target_id and selector are required")
    center_x, center_y = await _resolve_box(browser, target_id, selector)
    calls: list[tuple[str, dict[str, Any]]] = [
        ("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": center_x, "y": center_y}),
        (
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": center_x,
                "y": center_y,
                "button": "left",
                "clickCount": 1,
            },
        ),
        (
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "x": center_x,
                "y": center_y,
                "button": "left",
                "clickCount": 1,
            },
        ),
    ]
    if bool(payload.get("clear", False)):
        clear_expression = f"document.querySelector({json.dumps(selector)}).value=''"
        calls.append(("Runtime.evaluate", {"expression": clear_expression}))
    calls.append(("Input.insertText", {"text": text}))
    await browser.page_session(target_id, calls)
    return {"profile": name, "target_id": target_id, "selector": selector, "typed": True}


async def _page_fill_form(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: bulk-fill an Edge page form from a selector-to-value mapping.

    Args:
        payload (dict[str, Any]): Profile, ``target_id``, and ``fields`` selector-to-value map.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and the count of matched/filled fields.

    Examples:
        >>> _page_fill_form.__name__
        '_page_fill_form'
        >>> callable(_page_fill_form)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    fields = payload.get("fields", {})
    valid = isinstance(fields, dict) and all(
        isinstance(key, str) and isinstance(value, str) for key, value in fields.items()
    )
    if not target_id or not valid:
        raise ValueError("target_id is required and fields must be a str-to-str mapping")
    expression = (
        "(() => { const FIELDS = " + json.dumps(fields) + "; let count = 0; "
        "for (const [sel, val] of Object.entries(FIELDS)) { "
        "const el = document.querySelector(sel); if (!el) continue; "
        "el.value = val; "
        "el.dispatchEvent(new Event('input', {bubbles: true})); "
        "el.dispatchEvent(new Event('change', {bubbles: true})); "
        "count += 1; } return count; })()"
    )
    result = await browser.page_session(
        target_id, [("Runtime.evaluate", {"expression": expression, "returnByValue": True})]
    )
    filled = int(result[0].get("result", {}).get("value", 0))
    return {"profile": name, "target_id": target_id, "filled": filled}


async def _page_select_option(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: set an Edge page ``<select>`` element's value and dispatch a change event.

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, ``selector``, and ``value``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, selector, value, and a confirmed selected flag.

    Examples:
        >>> _page_select_option.__name__
        '_page_select_option'
        >>> callable(_page_select_option)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    selector = str(payload.get("selector", ""))
    value = str(payload.get("value", ""))
    if not target_id or not selector:
        raise ValueError("target_id and selector are required")
    expression = (
        f"(() => {{ const el = document.querySelector({json.dumps(selector)}); "
        f"el.value = {json.dumps(value)}; "
        "el.dispatchEvent(new Event('change', {bubbles: true})); })()"
    )
    await browser.page_session(target_id, [("Runtime.evaluate", {"expression": expression})])
    return {
        "profile": name,
        "target_id": target_id,
        "selector": selector,
        "value": value,
        "selected": True,
    }


async def _page_scroll(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: scroll an Edge page to an element or an explicit coordinate.

    Args:
        payload (dict[str, Any]): Profile, ``target_id``, optional ``selector``, ``x``, ``y``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and a confirmed scrolled flag.

    Examples:
        >>> _page_scroll.__name__
        '_page_scroll'
        >>> callable(_page_scroll)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    selector = payload.get("selector")
    if selector:
        target = json.dumps(str(selector))
        expression = f"document.querySelector({target})?.scrollIntoView({{block: 'center'}})"
    else:
        expression = f"window.scrollTo({int(payload.get('x', 0))}, {int(payload.get('y', 0))})"
    await browser.page_session(target_id, [("Runtime.evaluate", {"expression": expression})])
    return {"profile": name, "target_id": target_id, "scrolled": True}


async def _page_evaluate(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: evaluate arbitrary JavaScript in an Edge page and return its value.

    Args:
        payload (dict[str, Any]): Profile, ``target_id``, ``expression``, opt. ``await_promise``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and the evaluated JSON-safe result value. Same trust
        model as the existing ``raw`` action's already-whitelisted ``Runtime.evaluate``: arbitrary
        JS runs with full page privileges.

    Examples:
        >>> _page_evaluate.__name__
        '_page_evaluate'
        >>> callable(_page_evaluate)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    expression = str(payload.get("expression", ""))
    if not target_id or not expression:
        raise ValueError("target_id and expression are required")
    await_promise = bool(payload.get("await_promise", False))
    result = await browser.page_session(
        target_id,
        [
            (
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True, "awaitPromise": await_promise},
            )
        ],
    )
    if exception := result[0].get("exceptionDetails"):
        raise RuntimeError(f"CDP_ERROR: {exception.get('text', 'evaluation failed')}")
    return {
        "profile": name,
        "target_id": target_id,
        "result": result[0].get("result", {}).get("value"),
    }


async def _page_snapshot(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: capture an Edge page accessibility tree using one attached CDP session.

    Args:
        payload (dict[str, Any]): Profile and required ``target_id``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and the full accessibility tree node list.

    Examples:
        >>> _page_snapshot.__name__
        '_page_snapshot'
        >>> callable(_page_snapshot)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    result = await browser.page_session(
        target_id, [("Accessibility.enable", {}), ("Accessibility.getFullAXTree", {})]
    )
    return {"profile": name, "target_id": target_id, "nodes": result[1].get("nodes", [])}


async def _page_screenshot(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: capture an Edge page screenshot, optionally saved to a local file path.

    Args:
        payload (dict[str, Any]): Profile, ``target_id``, optional ``format``, optional ``output``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and either a saved ``path`` or base64 ``data``.

    Examples:
        >>> _page_screenshot.__name__
        '_page_screenshot'
        >>> callable(_page_screenshot)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    image_format = str(payload.get("format", "png"))
    result = await browser.page_session(
        target_id, [("Page.captureScreenshot", {"format": image_format})]
    )
    data = str(result[0].get("data", ""))
    output = payload.get("output")
    if output:
        Path(str(output)).write_bytes(base64.b64decode(data))
        return {"profile": name, "target_id": target_id, "path": str(output)}
    return {"profile": name, "target_id": target_id, "data": data}


async def _page_query(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: query all Edge page elements matching a CSS selector and describe them.

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, and required ``selector``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, selector, and matched node/tag/attribute records.

    Examples:
        >>> _page_query.__name__
        '_page_query'
        >>> callable(_page_query)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    selector = str(payload.get("selector", ""))
    if not target_id or not selector:
        raise ValueError("target_id and selector are required")
    document = await browser.page_session(target_id, [("DOM.getDocument", {"depth": -1})])
    root_id = document[0].get("root", {}).get("nodeId")
    found = await browser.page_session(
        target_id, [("DOM.querySelectorAll", {"nodeId": root_id, "selector": selector})]
    )
    node_ids = found[0].get("nodeIds", [])
    if not node_ids:
        return {"profile": name, "target_id": target_id, "selector": selector, "matches": []}
    described = await browser.page_session(
        target_id, [("DOM.describeNode", {"nodeId": node_id}) for node_id in node_ids]
    )
    matches = [
        {
            "node_id": node_id,
            "tag": item.get("node", {}).get("nodeName", ""),
            "attributes": item.get("node", {}).get("attributes", []),
        }
        for node_id, item in zip(node_ids, described)
    ]
    return {"profile": name, "target_id": target_id, "selector": selector, "matches": matches}


async def _page_console_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: read (and optionally clear) console messages captured since hook install.

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, and optional ``clear``. Only
            messages emitted after this action first installs its console hook on that page are
            captured (best-effort, no native CDP event listening in this architecture).
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and captured console message records.

    Examples:
        >>> _page_console_list.__name__
        '_page_console_list'
        >>> callable(_page_console_list)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    clear = bool(payload.get("clear", False))
    expression = (
        "(() => { "
        "if (!window.__browserProxyConsole) { "
        "window.__browserProxyConsole = []; "
        "for (const level of ['log', 'warn', 'error', 'info']) { "
        "const original = console[level].bind(console); "
        "console[level] = (...args) => { "
        "window.__browserProxyConsole.push("
        "{level, args: args.map(String), ts: Date.now()}); "
        "original(...args); }; } } "
        "const messages = window.__browserProxyConsole.slice(); "
        f"if ({json.dumps(clear)}) {{ window.__browserProxyConsole.length = 0; }} "
        "return messages; })()"
    )
    result = await browser.page_session(
        target_id, [("Runtime.evaluate", {"expression": expression, "returnByValue": True})]
    )
    messages = result[0].get("result", {}).get("value", [])
    return {"profile": name, "target_id": target_id, "messages": messages}


async def _page_network_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: read page resource/navigation timing entries via the Timing API.

    Args:
        payload (dict[str, Any]): Profile and required ``target_id``. Uses the Resource/Navigation
            Timing API via JS (no CDP ``Network`` domain listener in this architecture), so response
            bodies/headers are unavailable — timing/size/status only.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and timing-derived request records.

    Examples:
        >>> _page_network_list.__name__
        '_page_network_list'
        >>> callable(_page_network_list)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    expression = (
        "[...performance.getEntriesByType('resource'), "
        "...performance.getEntriesByType('navigation')].map(e => ({"
        "name: e.name, initiatorType: e.initiatorType, duration: e.duration, "
        "transferSize: e.transferSize, responseStatus: e.responseStatus ?? null}))"
    )
    result = await browser.page_session(
        target_id, [("Runtime.evaluate", {"expression": expression, "returnByValue": True})]
    )
    requests = result[0].get("result", {}).get("value", [])
    return {"profile": name, "target_id": target_id, "requests": requests}


async def _page_dialog_policy(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: install a page-scoped auto-resolving policy for alert/confirm/prompt dialogs.

    Args:
        payload (dict[str, Any]): Profile, ``target_id``, ``action`` (accept/dismiss), opt.
            ``prompt_text``. Does not survive a future full navigation on this architecture since
            ``Page.addScriptToEvaluateOnNewDocument`` with a persistent session is not used.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and the installed dialog policy action.

    Examples:
        >>> _page_dialog_policy.__name__
        '_page_dialog_policy'
        >>> callable(_page_dialog_policy)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    action = str(payload.get("action", ""))
    if not target_id or action not in ("accept", "dismiss"):
        raise ValueError("target_id is required and action must be accept or dismiss")
    prompt_text = str(payload.get("prompt_text", ""))
    accept = json.dumps(action == "accept")
    expression = (
        "window.alert = () => {}; "
        f"window.confirm = () => {accept}; "
        f"window.prompt = () => ({accept} ? {json.dumps(prompt_text)} : null);"
    )
    await browser.page_session(target_id, [("Runtime.evaluate", {"expression": expression})])
    return {"profile": name, "target_id": target_id, "policy": action}


async def _page_set_download_behavior(
    payload: dict[str, Any], context: DaemonContext
) -> dict[str, Any]:
    """Purpose: configure the browser-level automatic download destination directory.

    Args:
        payload (dict[str, Any]): Profile and required local ``path``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, configured path, and a confirmed configured flag.

    Examples:
        >>> _page_set_download_behavior.__name__
        '_page_set_download_behavior'
        >>> callable(_page_set_download_behavior)
        True
    """
    name, browser = _profile(payload, context)
    path = str(payload.get("path", ""))
    if not path:
        raise ValueError("path is required")
    Path(path).mkdir(parents=True, exist_ok=True)
    await browser.call("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": path})
    return {"profile": name, "path": path, "configured": True}


async def _cookie_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: list browser-level cookies visible to a started Edge profile.

    Args:
        payload (dict[str, Any]): Object containing a started ``profile`` name.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: Profile name and the CDP-reported cookie records.

    Examples:
        >>> _cookie_list.__name__
        '_cookie_list'
        >>> callable(_cookie_list)
        True
    """
    name, browser = _profile(payload, context)
    result = await browser.call("Network.getCookies", {})
    return {"profile": name, "cookies": result.get("cookies", [])}


@require_approval
@require_verification("name")
async def _cookie_set(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: set one browser-level cookie for a started Edge profile.

    Args:
        payload (dict[str, Any]): Profile, ``name``, ``value``, ``domain``, optional ``path``,
            ``secure``, ``http_only``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, cookie name, domain, and a confirmed set flag.

    Examples:
        >>> _cookie_set.__name__
        '_cookie_set'
        >>> callable(_cookie_set)
        True
    """
    name, browser = _profile(payload, context)
    cookie_name = str(payload.get("name", ""))
    domain = str(payload.get("domain", ""))
    if not cookie_name or not domain:
        raise ValueError("name and domain are required")
    await browser.call(
        "Network.setCookie",
        {
            "name": cookie_name,
            "value": str(payload.get("value", "")),
            "domain": domain,
            "path": str(payload.get("path", "/")),
            "secure": bool(payload.get("secure", True)),
            "httpOnly": bool(payload.get("http_only", False)),
        },
    )
    return {"profile": name, "name": cookie_name, "domain": domain, "set": True}


@require_approval
@require_preflight("name", "domain")
async def _cookie_remove(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: remove one browser-level cookie by name and domain.

    Args:
        payload (dict[str, Any]): Profile, required ``name``, and required ``domain``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, cookie name, domain, and a confirmed removed flag.

    Examples:
        >>> _cookie_remove.__name__
        '_cookie_remove'
        >>> callable(_cookie_remove)
        True
    """
    name, browser = _profile(payload, context)
    cookie_name = str(payload.get("name", ""))
    domain = str(payload.get("domain", ""))
    if not cookie_name or not domain:
        raise ValueError("name and domain are required")
    await browser.call("Network.deleteCookies", {"name": cookie_name, "domain": domain})
    return {"profile": name, "name": cookie_name, "domain": domain, "removed": True}


async def _storage_local_get(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: read one or all Edge page ``localStorage`` entries.

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, optional ``key``. WARNING:
            localStorage can contain session/auth tokens — same secrets-exposure caveat as
            ``raw``/``page-evaluate``, caller is responsible.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and the read ``localStorage`` value.

    Examples:
        >>> _storage_local_get.__name__
        '_storage_local_get'
        >>> callable(_storage_local_get)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    key = payload.get("key")
    if key is not None:
        expression = f"localStorage.getItem({json.dumps(str(key))})"
    else:
        expression = "Object.fromEntries(Object.entries(localStorage))"
    result = await browser.page_session(
        target_id, [("Runtime.evaluate", {"expression": expression, "returnByValue": True})]
    )
    return {
        "profile": name,
        "target_id": target_id,
        "value": result[0].get("result", {}).get("value"),
    }


@require_approval
async def _storage_local_set(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: write one Edge page ``localStorage`` entry.

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, ``key``, and ``value``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, key, and a confirmed set flag.

    Examples:
        >>> _storage_local_set.__name__
        '_storage_local_set'
        >>> callable(_storage_local_set)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    key = str(payload.get("key", ""))
    if not target_id or not key:
        raise ValueError("target_id and key are required")
    value = str(payload.get("value", ""))
    expression = f"localStorage.setItem({json.dumps(key)}, {json.dumps(value)})"
    await browser.page_session(target_id, [("Runtime.evaluate", {"expression": expression})])
    return {"profile": name, "target_id": target_id, "key": key, "set": True}


@require_approval
@require_verification("title")
async def _group_create(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: create an Edge tab group through the paired extension.

    Args:
        payload (dict[str, Any]): Profile, ``tab_ids``, ``title``, and optional ``color``.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed created tab-group information.

    Examples:
        >>> _group_create.__name__
        '_group_create'
        >>> callable(_group_create)
        True
    """
    return await _extension(payload, context, "group.create")


@require_approval
@require_preflight("group_id")
async def _group_update(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: update an Edge tab group's title, color, or collapsed state.

    Args:
        payload (dict[str, Any]): Profile, ``group_id``, optional ``title``/``color``/``collapsed``.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed updated tab-group information.

    Examples:
        >>> _group_update.__name__
        '_group_update'
        >>> callable(_group_update)
        True
    """
    return await _extension(payload, context, "group.update")


@require_approval
@require_preflight("group_id")
async def _group_move(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: move an Edge tab group into another window.

    Args:
        payload (dict[str, Any]): Profile, ``group_id``, and destination ``window_id``.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed moved tab-group information.

    Examples:
        >>> _group_move.__name__
        '_group_move'
        >>> callable(_group_move)
        True
    """
    return await _extension(payload, context, "group.move")


@require_preflight("group_id")
async def _group_add_tabs(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: add existing real tabs into an ALREADY-CREATED Edge tab group.

    Args:
        payload (dict[str, Any]): Profile, required ``group_id`` (an existing group — never
            creates a new one, unlike ``group-create``), and required ``tab_ids`` (real numeric
            ``chrome.tabs.Tab.id`` values, never CDP ``target_id`` strings).
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed ``{group_id, tab_ids}`` now inside that group.

    Notes:
        Deliberately NOT ``@require_approval`` — dragging an already-visible tab into an
        already-visible group is directly observable the instant it happens.

    Examples:
        >>> _group_add_tabs.__name__
        '_group_add_tabs'
        >>> callable(_group_add_tabs)
        True
    """
    return await _extension(payload, context, "group.add_tabs")


@require_approval
@require_preflight("tab_ids")
async def _group_remove_tabs(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: remove real tabs from their Edge tab group WITHOUT closing them.

    Args:
        payload (dict[str, Any]): Profile and required ``tab_ids`` (real numeric
            ``chrome.tabs.Tab.id`` values to ungroup).
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed ``{tab_ids, ungrouped: true}``.

    Notes:
        Now ``@require_approval`` (KπX directive, GRAVÉ — a reversal from the original "same
        rationale as ``group-add-tabs``" stance): removing tabs from a named group is treated as a
        reviewable structural change, unlike ``group-add-tabs`` which stays ungated.

    Examples:
        >>> _group_remove_tabs.__name__
        '_group_remove_tabs'
        >>> callable(_group_remove_tabs)
        True
    """
    return await _extension(payload, context, "group.remove_tabs")


@require_approval
@require_preflight("layout")
async def _group_sync(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: reorganize a WHOLE window's tab/group structure in ONE call — absolute flexibility.

    Args:
        payload (dict[str, Any]): Profile and required ``layout``: an ORDERED list processed left
            to right, each entry either ``{"type":"tab","tab_id":N}`` (a standalone, ungrouped tab
            at this position) or ``{"type":"group","group_id":N|omitted,"title":str,"color":str,
            "tab_ids":[N,...]}`` (a whole group at this position — ``group_id`` given reuses/
            renames/recolors/adds-to that EXACT existing group; omitted creates a brand-new group).
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed ``{layout: [...]}`` — one entry per input entry, same
        order, each carrying the real ``tab_id`` or the real (possibly newly created) ``group_id``.

    Notes:
        Now ``@require_approval`` (KπX directive, GRAVÉ — a reversal from the original
        "deliberately not approval-gated" stance): reorganizing a whole window's structure is now
        treated as one deliberate, reviewable command, same as its new sibling ``window-sync``.

    Examples:
        >>> _group_sync.__name__
        '_group_sync'
        >>> callable(_group_sync)
        True
    """
    return await _extension(payload, context, "group.sync")


async def _browser_ask_user(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: ask the human operator a question through the paired extension overlay.

    Args:
        payload (dict[str, Any]): Profile, ``question``, and optional ``input_type``.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed human answer information.

    Examples:
        >>> _browser_ask_user.__name__
        '_browser_ask_user'
        >>> callable(_browser_ask_user)
        True
    """
    return await _extension(payload, context, "user.ask")


async def _browser_dismiss_overlays(
    payload: dict[str, Any], context: DaemonContext
) -> dict[str, Any]:
    """Purpose: dismiss obstructive page overlays through the paired extension.

    Args:
        payload (dict[str, Any]): Object identifying the Edge profile to act on.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed overlay-dismissal information.

    Examples:
        >>> _browser_dismiss_overlays.__name__
        '_browser_dismiss_overlays'
        >>> callable(_browser_dismiss_overlays)
        True
    """
    return await _extension(payload, context, "overlay.dismiss")


async def _browser_solve_captcha(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: detect or drive a CAPTCHA challenge through the paired extension.

    Args:
        payload (dict[str, Any]): Profile, ``action`` (detect/click_checkbox/click_grid), opt.
            ``cells``.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed CAPTCHA interaction information.

    Examples:
        >>> _browser_solve_captcha.__name__
        '_browser_solve_captcha'
        >>> callable(_browser_solve_captcha)
        True
    """
    return await _extension(payload, context, "captcha.solve")


async def _browser_set_date(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: set a difficult native date-widget value through the paired extension.

    Args:
        payload (dict[str, Any]): Profile, ``selector``, and ISO date ``value``.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed date-widget update information.

    Examples:
        >>> _browser_set_date.__name__
        '_browser_set_date'
        >>> callable(_browser_set_date)
        True
    """
    return await _extension(payload, context, "form.set_date")


async def _browser_set_combobox(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: set a difficult native combobox widget value through the paired extension.

    Args:
        payload (dict[str, Any]): Profile, ``selector``, and ``value``.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed combobox update information.

    Examples:
        >>> _browser_set_combobox.__name__
        '_browser_set_combobox'
        >>> callable(_browser_set_combobox)
        True
    """
    return await _extension(payload, context, "form.set_combobox")


async def _browser_drop_file(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: drop an inline-supplied file onto a page file input through the extension.

    Args:
        payload (dict[str, Any]): Profile, ``selector``, ``filename``, ``content_base64``, opt.
            ``mime_type``. Content is supplied inline by the caller; the extension is granted no
            filesystem access.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed file-drop information.

    Examples:
        >>> _browser_drop_file.__name__
        '_browser_drop_file'
        >>> callable(_browser_drop_file)
        True
    """
    return await _extension(payload, context, "form.drop_file")


async def _browser_get_new_tab(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: capture the next Edge tab opened after this call through the extension.

    Args:
        payload (dict[str, Any]): Profile and optional ``timeout_seconds``.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed captured new-tab information.

    Examples:
        >>> _browser_get_new_tab.__name__
        '_browser_get_new_tab'
        >>> callable(_browser_get_new_tab)
        True
    """
    return await _extension(payload, context, "tab.capture_next")


async def _raw(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: execute one browser-level CDP method after daemon policy enforcement.

    Args:
        payload (dict[str, Any]): Profile, CDP ``method``, and object-valued ``params``.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: Profile, CDP method, and its CDP result object.

    Examples:
        >>> _raw.__name__
        '_raw'
        >>> callable(_raw)
        True
    """
    method = str(payload.get("method", ""))
    params = payload.get("params", {})
    if not isinstance(params, dict):
        raise ValueError("params must be a JSON object")
    name, browser = _profile(payload, context)
    return {"profile": name, "method": method, "result": await browser.call(method, params)}


def _action(name: str, group: str, handler: Handler) -> ActionDef:
    """Purpose: construct a registry record from one documented action handler.

    Args:
        name (str): Stable flat public action name.
        group (str): Human-facing registry category.
        handler (Handler): Async action implementation with policy decorators.

    Returns:
        ActionDef: Immutable action definition carrying resolved policy metadata.

    Examples:
        >>> _action('sample', 'Tests', _profile_list).name
        'sample'
        >>> _action('sample', 'Tests', _profile_list).group
        'Tests'
    """
    return ActionDef(name, group, handler, policy_of(handler))


REGISTRY = {
    entry.name: entry
    for entry in (
        _action("profile-list", "Profiles", _profile_list),
        _action("profile-start", "Profiles", _profile_start),
        _action("profile-remove", "Profiles", _profile_remove),
        _action("window-list", "Windows", _window_list),
        _action("window-create", "Windows", _window_create),
        _action("window-close", "Windows", _window_close),
        _action("window-sync", "Windows", _window_sync),
        _action("window-save", "Windows", _window_save),
        _action("window-restore", "Windows", _window_restore),
        _action("window-saved-list", "Windows", _window_saved_list),
        _action("window-saved-remove", "Windows", _window_saved_remove),
        _action("tab-list", "Tabs", _tab_list),
        _action("tab-get", "Tabs", _tab_get),
        _action("tab-create", "Tabs", _tab_create),
        _action("tab-activate", "Tabs", _tab_activate),
        _action("tab-update", "Tabs", _tab_update),
        _action("group-list", "Groups", _group_list),
        _action("bookmark-list", "Bookmarks", _bookmark_list),
        _action("bookmark-get", "Bookmarks", _bookmark_get),
        _action("bookmark-create", "Bookmarks", _bookmark_create),
        _action("bookmark-remove", "Bookmarks", _bookmark_remove),
        _action("bookmark-update", "Bookmarks", _bookmark_update),
        _action("page-navigate", "Navigation", _page_navigate),
        _action("page-reload", "Navigation", _page_reload),
        _action("page-back", "Navigation", _page_back),
        _action("page-forward", "Navigation", _page_forward),
        _action("page-click", "Interaction", _page_click),
        _action("page-hover", "Interaction", _page_hover),
        _action("page-type", "Interaction", _page_type),
        _action("page-fill-form", "Interaction", _page_fill_form),
        _action("page-select-option", "Interaction", _page_select_option),
        _action("page-scroll", "Interaction", _page_scroll),
        _action("page-evaluate", "Inspection", _page_evaluate),
        _action("page-snapshot", "Inspection", _page_snapshot),
        _action("page-screenshot", "Inspection", _page_screenshot),
        _action("page-query", "Inspection", _page_query),
        _action("page-console-list", "Inspection", _page_console_list),
        _action("page-network-list", "Inspection", _page_network_list),
        _action("page-dialog-policy", "Dialogs", _page_dialog_policy),
        _action("page-set-download-behavior", "Downloads", _page_set_download_behavior),
        _action("cookie-list", "Cookies", _cookie_list),
        _action("cookie-set", "Cookies", _cookie_set),
        _action("cookie-remove", "Cookies", _cookie_remove),
        _action("storage-local-get", "Storage", _storage_local_get),
        _action("storage-local-set", "Storage", _storage_local_set),
        _action("group-create", "Groups", _group_create),
        _action("group-update", "Groups", _group_update),
        _action("group-move", "Groups", _group_move),
        _action("group-add-tabs", "Groups", _group_add_tabs),
        _action("group-remove-tabs", "Groups", _group_remove_tabs),
        _action("group-sync", "Groups", _group_sync),
        _action("browser-ask-user", "HumanInTheLoop", _browser_ask_user),
        _action("browser-dismiss-overlays", "HumanInTheLoop", _browser_dismiss_overlays),
        _action("browser-solve-captcha", "HumanInTheLoop", _browser_solve_captcha),
        _action("browser-set-date", "HumanInTheLoop", _browser_set_date),
        _action("browser-set-combobox", "HumanInTheLoop", _browser_set_combobox),
        _action("browser-drop-file", "HumanInTheLoop", _browser_drop_file),
        _action("browser-get-new-tab", "HumanInTheLoop", _browser_get_new_tab),
        _action("raw", "Advanced", _raw),
    )
}

attach_public_docstrings(REGISTRY)


def validate_registry() -> None:
    """Purpose: verify every public action has unique, Tick-style, actionable documentation.

    Args:
        None: This validator reads the module-level ``REGISTRY`` only.

    Returns:
        None: Raises ``RuntimeError`` when registry identity or public documentation is broken.

    Examples:
        >>> validate_registry() is None
        True
        >>> 'raw' in REGISTRY
        True
    """
    for name, action in REGISTRY.items():
        if (
            name != action.name
            or not action.handler.__doc__
            or any(
                section not in action.handler.__doc__ for section in ("Parameters:", "Examples:")
            )
            or action.handler.__doc__.count(f"browser-proxy do {name}") < 3
        ):
            raise RuntimeError(f"invalid action registry entry: {name}")
