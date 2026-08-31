"""Registry of documented, flat Edge-only browser actions."""

import asyncio
import base64
import json
import math
import time
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from browser_proxy.cdp import CdpBrowser, CdpParams, ConsoleCapture
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

    async def console_capture(self, profile: str, target_id: str) -> "ConsoleCapture":
        """Purpose: fetch or create the persistent per-target console capture for one profile.

        Args:
            profile (str): Managed Edge profile name.
            target_id (str): CDP page target ID whose console output to capture.

        Returns:
            ConsoleCapture: Started persistent capture with a session that survives reloads.

        Examples:
            >>> DaemonContext.console_capture.__name__
            'console_capture'
            >>> asyncio.iscoroutinefunction(DaemonContext.console_capture)
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
            as ``profile-list``/``admin profile status``/``admin status``, never a private ad hoc check.

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
    return name, CdpBrowser(edge_cdp_port(name), profile=name)


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


_VISIBLE_TARGET_TYPES = frozenset({"page", "browser_ui"})
"""`window-list`/`tab-list`/`tab-get`'s target-type net — root-caused live (KπX, GRAVÉ): closing
every `type=="page"` tab in a window can leave Edge showing ONE surviving internal page
(`edge://settings/profiles` observed live — an Edge Workspace window falling back to Settings once
its last real content tab closed) whose CDP `type` is `"browser_ui"`, NOT `"page"`. The former
`type=="page"`-only filter made that entire, still genuinely VISIBLE window silently disappear from
every listing — a real "0 Trust · 100% Control" violation (real browser state hidden from KπX).
Widening to `{"page","browser_ui"}` restores visibility; `_window_id_for_target`'s pre-existing
`None`-bucket behavior (see its own docstring) already cleanly separates genuine windowed
`browser_ui` tabs (Settings, New Tab) from ephemeral, window-less UI panels (`TabSearch`,
`discover-chat-v2`) that resolve no real window at all — no extra heuristic needed here."""


async def _page_targets(browser: CdpBrowser) -> list[dict[str, Any]]:
    """Purpose: list every REAL, potentially window-visible CDP target — the one shared fact
    behind every action needing it.

    Args:
        browser (CdpBrowser): Client bound to one managed Edge debugging port.

    Returns:
        list[dict[str, Any]]: Every ``Target.getTargets`` entry with ``type`` in
        ``_VISIBLE_TARGET_TYPES`` (``"page"`` or ``"browser_ui"`` — see that constant's own
        rationale). Used by ``window-list`` (``tab-list``/``tab-get`` reuse ``window-list``'s full
        computation instead — see ``_tabs_with_context`` — for window/group context; ``page-list``/
        ``page-get`` were purged and merged into ``tab-list``/``tab-get``, KπX directive: "tab =
        page... je ne veux pas de duplication inutile") — never a private re-implementation of this
        exact filter in each handler.

    Examples:
        >>> asyncio.iscoroutinefunction(_page_targets)
        True
        >>> callable(_page_targets)
        True
    """
    return [
        target for target in await browser.targets() if target.get("type") in _VISIBLE_TARGET_TYPES
    ]


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
    incognito: bool = False,
    browser_context_id: str | None = None,
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
        incognito (bool): Create the tab in a disposable InPrivate browser context instead of the
            profile's default context.  Uses ``Target.createBrowserContext`` with
            ``private: true`` so the context is created as incognito.  Mutually exclusive with
            ``window_id`` — caller validates before calling.
        browser_context_id (str | None): Pre-existing browser context ID to create the tab in.
            When provided, ``incognito`` is ignored (the caller already created the context).
            Used by ``_window_create`` to place all layout tabs in the same incognito context.

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
    elif browser_context_id is not None:
        options["browserContextId"] = browser_context_id
    elif incognito:
        ctx = await browser.call(
            "Target.createBrowserContext",
            {"private": True},
        )
        context_id = ctx.get("browserContextId")
        if not isinstance(context_id, str):
            raise RuntimeError(
                "CDP_ERROR: Target.createBrowserContext returned no browserContextId"
            )
        options["browserContextId"] = context_id
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
    layout: Any,
    browser: CdpBrowser,
    context: DaemonContext,
    profile: str,
    window_id: int | None,
    *,
    incognito: bool = False,
    browser_context_id: str | None = None,
) -> list[dict[str, Any]]:
    """Purpose: create a whole ordered tab/group layout inside one window from a flat JSON list.

    Args:
        layout (Any): Untrusted ``window-create``/``layout`` payload value — REQUIRED, non-empty
            (a window with zero tabs cannot exist). The SAME shape and field name as
            ``window-sync``'s ``layout`` — one uniform vocabulary for "describe a window's tab/group
            content", never a second, differently-shaped way to say the same thing.
        browser (CdpBrowser): Client bound to one managed Edge debugging port.
        context (DaemonContext): Daemon state exposing the paired extension (needed for groups).
        profile (str): Target browser-proxy profile.
        window_id (int | None): Real Edge window every created tab/group must land in.
        incognito (bool): When True, every tab is created in a disposable InPrivate context
            (forwarded to ``_create_window_tab``).
        browser_context_id (str | None): Pre-existing browser context ID. When provided, all tabs
            are created in this context (used by ``_window_create`` to keep all layout tabs in the
            same incognito context).

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
                browser,
                context,
                profile,
                window_id,
                url,
                capture_chrome_id=False,
                incognito=incognito,
                browser_context_id=browser_context_id,
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
                    browser,
                    context,
                    profile,
                    window_id,
                    tab_url,
                    capture_chrome_id=True,
                    incognito=incognito,
                    browser_context_id=browser_context_id,
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
            to describe a window's content, the SAME field name and entry shape as ``window-sync``
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
    incognito = bool(payload.get("incognito", False))
    browser_context_id: str | None = None
    options: dict[str, Any] = {"url": "about:blank", "newWindow": True}
    if incognito:
        ctx = await browser.call(
            "Target.createBrowserContext",
            {"private": True},
        )
        context_id = ctx.get("browserContextId")
        if not isinstance(context_id, str):
            raise RuntimeError(
                "CDP_ERROR: Target.createBrowserContext returned no browserContextId"
            )
        options["browserContextId"] = context_id
        browser_context_id = context_id
    for key in ("left", "top", "width", "height", "windowState", "focus"):
        if key in payload:
            options[key] = payload[key]
    result = await browser.call("Target.createTarget", options)
    placeholder_target_id = result["targetId"]
    window_id, _bounds = await _window_id_for_target(browser, placeholder_target_id)
    # When incognito, do NOT pass window_id to _apply_window_layout — Target.createTarget with
    # windowId creates tabs in the default context, not the incognito context of that window.
    # Instead, pass only browser_context_id so all layout tabs are created directly in the
    # incognito context (Edge places them in the same window automatically).
    layout_result = await _apply_window_layout(
        payload.get("layout"),
        browser,
        context,
        name,
        None if incognito else window_id,
        browser_context_id=browser_context_id,
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
    ONE call, "très complet et flexible" (KπX).

    Args:
        payload (dict[str, Any]): Profile, required ``window_id`` (the REAL Edge window to target),
            optional ``bounds`` (``{"left","top","width","height"}``, any subset), optional
            ``state`` (``"normal"|"maximized"|"minimized"|"fullscreen"``), optional ``focused``
            (bool), optional ``layout`` (the SAME ordered tab/group schema ``window-create`` uses
            — reused unchanged, never a second, differently-shaped vocabulary for "describe a
            window's content").
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: ``window_id``, the extension-confirmed window properties after any
        requested ``bounds``/``state``/``focused`` change, and ``layout`` (present only if a
        ``layout`` was given).

    Raises:
        ValueError: No field beyond ``profile``/``window_id`` given at all (a no-op call).

    Notes:
        HITL-gated (KπX directive, GRAVÉ): reorganizing a whole window's structure — its own
        bounds/state and/or its entire tab/group content — is treated as one deliberate, reviewable
        command. The standalone ``group-sync`` public action was purged (KπX, GRAVÉ: "purge
        group-sync vu que inclus dans window-sync") since ``window-sync`` without ``bounds``/
        ``state``/``focused`` is a strict superset of what it did — the underlying bridge kind
        ``group.sync``/``handleGroupSync`` still exists internally, called directly here, never
        publicly exposed as its own top-level ``do`` action anymore.

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
        list[dict[str, Any]]: Same shape `window-create`/`window-sync` accept: `{"type":"tab",
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
            EXISTING window instead — mutually exclusive with ``new_window`` and ``incognito``),
            optional ``incognito`` (bool — create in a disposable InPrivate context instead of the
            profile's default; mutually exclusive with ``window_id``), optional ``group_id``
            (add the new tab into that EXISTING group/folder the instant it is created), optional
            ``wait_seconds`` (defaults to `10`, same convention as ``page-navigate``/``page-reload``
            — how long to wait for the newly created tab's OWN initial load to reach
            ``document.readyState === "complete"``), and at most one position hint
            (``index``/``before_tab_id``/``after_tab_id``, same convention as ``tab-update``).
        context (DaemonContext): Daemon state used to resolve the profile and, when ``group_id`` or
            a position is requested, the paired extension (to capture the real chrome tab id and
            apply the placement via the SAME ``tab.update`` mechanism ``tab-update`` uses).

    Returns:
        dict[str, Any]: Profile, CDP ``target_id``, requested ``url``, and the last observed
        ``ready_state`` for that new tab's own initial load (KπX, GRAVÉ: "asymétrie corrigée" —
        ``tab-create`` used to return before confirming its own tab had even started rendering,
        unlike ``page-navigate``/``page-reload`` which always do) — plus, whenever a real chrome
        tab id was resolved, ``tab_id`` and (when a placement was requested) the extension-confirmed
        ``window_id``/``group_id``/``index`` after that placement.

    Raises:
        ValueError: Both ``new_window`` and ``window_id`` given (ambiguous intent), or
            ``incognito`` combined with ``window_id`` (cannot add an incognito tab to an
            existing normal window).

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
    incognito = bool(payload.get("incognito", False))
    new_window = bool(payload.get("new_window", False))
    raw_window_id = payload.get("window_id")
    if new_window and raw_window_id is not None:
        raise ValueError("new_window and window_id are mutually exclusive")
    if incognito and raw_window_id is not None:
        raise ValueError(
            "incognito and window_id are mutually exclusive "
            "(cannot add an incognito tab to an existing normal window)"
        )
    group_id = payload.get("group_id")
    position_fields = ("index", "before_tab_id", "after_tab_id")
    wants_position = any(payload.get(field) is not None for field in position_fields)
    needs_chrome_id = group_id is not None or wants_position
    wait_seconds = int(payload.get("wait_seconds", 10))

    target_id, chrome_tab_id = await _create_window_tab(
        browser,
        context,
        name,
        int(raw_window_id) if raw_window_id is not None else None,
        url,
        capture_chrome_id=needs_chrome_id,
        new_window=new_window,
        incognito=incognito,
    )
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
    outcome: dict[str, Any] = {
        "profile": name,
        "target_id": target_id,
        "url": url,
        "ready_state": ready_state,
    }
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


async def _extension_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: list EVERY installed extension/app/theme in this Edge profile with full detail —
    absolute finesse over the WHOLE extension ecosystem, not just this one (KπX, GRAVÉ: "gérer les
    extensions... implémente full ce qui est possible").

    Args:
        payload (dict[str, Any]): Profile only.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed ``{extensions: [...]}`` — one fully-detailed entry per
        installed item, including real ``permissions``/``host_permissions`` AND their
        human-readable ``permission_warnings`` (never raw permission strings alone).

    Examples:
        >>> _extension_list.__name__
        '_extension_list'
        >>> callable(_extension_list)
        True
    """
    return await _extension(payload, context, "extension.list")


async def _extension_get(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: read ALL available detail about ONE installed extension/app/theme — same
    "everything about ONE X" philosophy as ``tab-get``/``bookmark-get``, extended to extensions.

    Args:
        payload (dict[str, Any]): Profile and required ``id`` (a real ``chrome.management``
            extension id).
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed full detail for that one extension.

    Examples:
        >>> _extension_get.__name__
        '_extension_get'
        >>> callable(_extension_get)
        True
    """
    return await _extension(payload, context, "extension.get")


@require_preflight("ids")
async def _extension_enable(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: enable one or MORE installed extensions, batch, in ONE call — deliberately NOT
    approval-gated (KπX directive): re-enabling an already-installed extension is a low-risk,
    directly observable, reversible action (unlike `extension-disable`, which stays approval-gated
    since it can silently turn off a security-relevant extension the user still trusts).

    Args:
        payload (dict[str, Any]): Profile and required, non-empty ``ids`` (real extension ids).
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed ``{updated: [{id, name, enabled}, ...]}``.

    Examples:
        >>> _extension_enable.__name__
        '_extension_enable'
        >>> callable(_extension_enable)
        True
    """
    return await _extension(payload, context, "extension.enable")


@require_approval
@require_preflight("ids")
async def _extension_disable(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: disable one or MORE installed extensions, batch, in ONE call.

    Args:
        payload (dict[str, Any]): Profile and required, non-empty ``ids`` (real extension ids).
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed ``{updated: [{id, name, enabled}, ...]}``.

    Examples:
        >>> _extension_disable.__name__
        '_extension_disable'
        >>> callable(_extension_disable)
        True
    """
    return await _extension(payload, context, "extension.disable")


async def _extension_reload(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: restart the paired extension's OWN service worker to pick up newly deployed code —
    "répondre avant de couper": the response reaches the daemon BEFORE the reload happens, never
    after (KπX, GRAVÉ: this exact reload used to require a manual click in ``edge://extensions/``
    after every code change this session).

    Args:
        payload (dict[str, Any]): Profile only.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed ``{reloading: true, id, name, version}`` (the paired
        extension's own identity) — never approval-gated: it only restarts OUR OWN plumbing, no
        user data at risk, matching ``window-create``/``tab-create``'s "directly observable, safe"
        rationale. The extension's own `chrome.runtime.reload()` is deliberately scheduled 200ms
        AFTER this response is sent, so the daemon never sees a dropped connection before getting
        its answer.

    Examples:
        >>> _extension_reload.__name__
        '_extension_reload'
        >>> callable(_extension_reload)
        True
    """
    return await _extension(payload, context, "extension.reload")


_EXTENSION_STORE_SEARCH_URLS: dict[str, str] = {
    "edge": "https://microsoftedge.microsoft.com/addons/search/{query}",
    "chrome": "https://chromewebstore.google.com/search/{query}",
}
"""Public, human-facing search result pages — NOT an official API (researched live: neither
Microsoft nor Google publishes one; Chrome Web Store's own Developer Program Policies explicitly
flag automated scraping of store metadata as a policy risk). This action reads the same page a
human would see in a real tab, via the exact same CDP primitives ``page-navigate``/``page-evaluate``
already expose — never a private/internal JSON endpoint."""

_EXTENSION_SEARCH_RESULT_LIMIT = 20
"""Default cap on returned search results — bounds the size of a best-effort DOM scrape."""

_EXTENSION_SEARCH_EXTRACT_JS = """
(function () {
  const seen = new Map();
  const anchors = document.querySelectorAll('a[href*="/detail/"]');
  for (const a of anchors) {
    const href = a.getAttribute('href') || '';
    const match = href.match(/\\/detail\\/([^/]+)\\/([a-p]{32})/);
    if (!match) continue;
    const [, slug, id] = match;
    if (seen.has(id)) continue;
    let node = a;
    for (let i = 0; i < 2 && node.parentElement; i++) node = node.parentElement;
    const lines = node.innerText.trim().split("\\n").map((line) => line.trim()).filter(Boolean);
    seen.set(id, { id, slug, text_block: lines.slice(0, 6) });
  }
  return Array.from(seen.values());
})()
"""
"""Best-effort DOM extraction shared by both stores — real `/detail/<slug>/<32-char-id>` links are
the one stable, structural signal on either page; everything else (name, rating, developer,
description) is read as raw nearby text, never guaranteed to map to the same field across a page
markup change on either store's side."""


async def _extension_search(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: search a real Edge Add-ons or Chrome Web Store search results page for installable
    extensions — live-verified (KπX, GRAVÉ: "il faut un extension search qui fouille chrome
    webstore edge addons... vu que edge compatible avec chrome").

    Args:
        payload (dict[str, Any]): Profile, required ``store`` (``"edge"`` or ``"chrome"``),
            required non-empty ``query``, and optional ``limit`` (defaults to 20).
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: ``{profile, store, query, results: [{id, slug, text_block}, ...]}`` — a
        temporary tab is created, navigated, scraped, and closed within this ONE call, leaving no
        trace. On Chrome Web Store specifically, Google's own consent interstitial is dismissed
        first (`Reject all`) since it otherwise blocks the real results entirely.

    Raises:
        ValueError: An unknown ``store``, or an empty ``query``.

    Examples:
        >>> _extension_search.__name__
        '_extension_search'
        >>> callable(_extension_search)
        True
    """
    name, browser = _profile(payload, context)
    store = str(payload.get("store", "")).strip().lower()
    if store not in _EXTENSION_STORE_SEARCH_URLS:
        raise ValueError(f"store must be one of {sorted(_EXTENSION_STORE_SEARCH_URLS)}")
    query = str(payload.get("query", "")).strip()
    if not query:
        raise ValueError("query is required")
    limit = int(payload.get("limit", _EXTENSION_SEARCH_RESULT_LIMIT))
    url = _EXTENSION_STORE_SEARCH_URLS[store].format(query=urllib.parse.quote(query))
    target_id, _ = await _create_window_tab(browser, context, name, None, url, False)
    try:
        await _wait_for_search_results(browser, target_id)
        if store == "chrome":
            await browser.page_session(
                target_id,
                [
                    (
                        "Runtime.evaluate",
                        {
                            "expression": (
                                "(function(){const b=Array.from("
                                "document.querySelectorAll('button')).find("
                                "x=>x.textContent.trim()==='Reject all'); "
                                "if (b) b.click(); return !!b;})()"
                            ),
                            "returnByValue": True,
                        },
                    )
                ],
            )
            await _wait_for_search_results(browser, target_id)
        extracted = await browser.page_session(
            target_id,
            [
                (
                    "Runtime.evaluate",
                    {"expression": _EXTENSION_SEARCH_EXTRACT_JS, "returnByValue": True},
                )
            ],
        )
        results = extracted[0].get("result", {}).get("value", []) or []
    finally:
        await browser.call("Target.closeTarget", {"targetId": target_id})
    return {"profile": name, "store": store, "query": query, "results": results[:limit]}


async def _wait_for_search_results(
    browser: CdpBrowser, target_id: str, wait_seconds: float = 15.0
) -> None:
    """Purpose: poll a search results page until it has rendered at least one real detail link.

    Args:
        browser (CdpBrowser): Attached CDP session used to poll the page.
        target_id (str): CDP page target ID hosting the search results.
        wait_seconds (float): Maximum total polling time, in seconds.

    Returns:
        None: Returns as soon as a real result link is found, or once ``wait_seconds`` elapses —
        never raises on a genuinely empty (zero-result) search.

    Examples:
        >>> asyncio.iscoroutinefunction(_wait_for_search_results)
        True
        >>> callable(_wait_for_search_results)
        True
    """
    for _ in range(max(1, int(wait_seconds / 0.3))):
        probe = await browser.page_session(
            target_id,
            [
                (
                    "Runtime.evaluate",
                    {
                        "expression": "document.querySelectorAll('a[href*=\"/detail/\"]').length",
                        "returnByValue": True,
                    },
                )
            ],
        )
        if int(probe[0].get("result", {}).get("value", 0) or 0) > 0:
            return
        await asyncio.sleep(0.3)


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
    # All three DOM-domain calls MUST share the exact same attached CDP session (a single
    # `page_session()` invocation) — root-caused live (KπX, GRAVÉ): each `page_session()` call
    # attaches a brand-new session then detaches it; `DOM.getDocument`'s `nodeId`s are scoped to
    # the DOM agent of the session that minted them, so resolving `querySelector`/`getBoxModel`
    # against them from a LATER, separately-attached session deterministically fails with
    # `Could not find node with given id` — reproduced on EVERY call, not intermittent, confirmed
    # on a freshly navigated, unmutated page. Splitting these 3 calls across 3 separate
    # `page_session()` calls was the exact bug; chaining them within ONE call (one attach, one
    # detach), each later call's params resolved from the earlier calls' real results via
    # `CdpParams`'s callable form, is the fix.
    if selector.startswith("xpath="):
        xpath_query = selector[6:]
        expr = f"document.evaluate({json.dumps(xpath_query)}, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue"
        _, _evaluate, req_node, box = await browser.page_session(
            target_id,
            [
                ("DOM.getDocument", {"depth": -1}),
                ("Runtime.evaluate", {"expression": expr}),
                (
                    "DOM.requestNode",
                    lambda results: {"objectId": results[1].get("result", {}).get("objectId", "")},
                ),
                ("DOM.getBoxModel", lambda results: {"nodeId": results[2].get("nodeId", 0)}),
            ],
        )
        node_id = req_node.get("nodeId", 0)
        if not node_id:
            raise ValueError(f"element not found: {selector}")
    else:
        _, node, box = await browser.page_session(
            target_id,
            [
                ("DOM.getDocument", {"depth": -1}),
                (
                    "DOM.querySelector",
                    lambda results: {
                        "nodeId": results[0].get("root", {}).get("nodeId"),
                        "selector": selector,
                    },
                ),
                ("DOM.getBoxModel", lambda results: {"nodeId": results[1].get("nodeId", 0)}),
            ],
        )
        node_id = node.get("nodeId", 0)
        if not node_id:
            raise ValueError(f"element not found: {selector}")

    quad = box.get("model", {}).get("content")
    if not quad:
        raise ValueError(f"element box model not available: {selector}")
    return (quad[0] + quad[4]) / 2, (quad[1] + quad[5]) / 2


async def _resolve_box_with_fallback(
    browser: CdpBrowser, target_id: str, selector: str, fallback_selector: str = ""
) -> tuple[float, float, str]:
    """Purpose: resolve a selector with a short retry loop, falling back to a secondary selector.

    Args:
        browser (CdpBrowser): Client.
        target_id (str): Target.
        selector (str): Primary selector.
        fallback_selector (str): Fallback.

    Returns:
        tuple[float, float, str]: The coordinates and used selector.

    Examples:
        >>> asyncio.iscoroutinefunction(_resolve_box_with_fallback)
        True
        >>> callable(_resolve_box_with_fallback)
        True
    """
    start = time.monotonic()
    last_err = None
    while time.monotonic() - start < 2.0:
        try:
            x, y = await _resolve_box(browser, target_id, selector)
            return x, y, selector
        except (ValueError, RuntimeError) as e:
            last_err = e
            await asyncio.sleep(0.5)

    if fallback_selector:
        try:
            x, y = await _resolve_box(browser, target_id, fallback_selector)
            return x, y, fallback_selector
        except (ValueError, RuntimeError) as e:
            raise ValueError(
                f"element not found: {selector} (fallback {fallback_selector} also failed: {e})"
            )
    raise ValueError(f"element not found: {selector} (timeout 2.0s, last error: {last_err})")


async def _resolve_box_eval(
    browser: CdpBrowser, target_id: str, selector: str
) -> tuple[float, float]:
    """Purpose: resolve a CSS selector to its element center via a single atomic ``Runtime.evaluate`` call.

    Args:
        browser (CdpBrowser): Client.
        target_id (str): Target page.
        selector (str): CSS selector expected to match exactly one element.

    Returns:
        tuple[float, float]: Viewport ``(x, y)`` center of the matched element's content box.

    Notes:
        Unlike ``_resolve_box`` (which chains ``DOM.getDocument`` → ``DOM.querySelector`` →
        ``DOM.getBoxModel`` in three separate CDP calls — a race on dynamic pages where the node
        can vanish between ``querySelector`` and ``getBoxModel``), this helper executes the entire
        query-and-measure sequence inside a single ``Runtime.evaluate`` — fully atomic from the
        page's perspective.

    Examples:
        >>> asyncio.iscoroutinefunction(_resolve_box_eval)
        True
        >>> callable(_resolve_box_eval)
        True
    """
    escaped = json.dumps(selector)
    expr = (
        f"(() => {{"
        f"  const el = document.querySelector({escaped});"
        f"  if (!el) return null;"
        f"  const r = el.getBoundingClientRect();"
        f"  return {{x: r.x + r.width / 2, y: r.y + r.height / 2}};"
        f"}})()"
    )
    results = await browser.page_session(
        target_id,
        [("Runtime.evaluate", {"expression": expr, "returnByValue": True})],
    )
    value = results[0].get("result", {}).get("value") if results else None
    if not value or not isinstance(value, dict):
        raise ValueError(f"element not found: {selector}")
    cx = value.get("x")
    cy = value.get("y")
    if cx is None or cy is None:
        raise ValueError(f"element not found: {selector}")
    return float(cx), float(cy)


async def _page_click(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: click an Edge page element resolved by CSS selector via DOM CDP calls.

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, and required ``selector``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, selector, clicked coordinates, and click flag.

    Notes:
        Uses ``DOM.getDocument`` → ``DOM.querySelector`` → ``DOM.getBoxModel`` (three separate
        CDP calls). On dynamic pages (React, Vue, SPAs), a race condition can cause
        ``CDP_ERROR: Could not find node with given id`` when the element is removed between
        ``querySelector`` and ``getBoxModel``. In that case, **use ``page-click-eval``** instead
        — it resolves the bounding box atomically via ``Runtime.evaluate``, eliminating the race.
        ``page-click`` remains necessary for: (1) ``xpath=`` selectors, (2) elements inside
        Shadow DOM, (3) pages with strict CSP blocking ``eval()``.

    Examples:
        >>> _page_click.__name__
        '_page_click'
        >>> callable(_page_click)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    selector = str(payload.get("selector", ""))
    fallback_selector = str(payload.get("fallback_selector", ""))
    if not target_id or not selector:
        raise ValueError("target_id and selector are required")
    center_x, center_y, used_selector = await _resolve_box_with_fallback(
        browser, target_id, selector, fallback_selector
    )
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
        "selector": used_selector,
        "x": center_x,
        "y": center_y,
        "clicked": True,
    }


async def _page_click_eval(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: click an Edge page element resolved by CSS selector — **default choice** for page clicks.

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, and required ``selector``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, selector, clicked coordinates, and click flag.

    Notes:
        **Default for CSS-selector page clicks.** Resolves the element's bounding box inside a
        single ``Runtime.evaluate`` call (``querySelector`` + ``getBoundingClientRect`` execute
        atomically from the page's perspective), eliminating the race condition that affects
        ``page-click`` on dynamic pages (React, Vue, SPAs). Use ``page-click`` instead only when
        you need: (1) ``xpath=`` selectors, (2) elements inside Shadow DOM, (3) pages with
        strict CSP blocking ``eval()``.

    Examples:
        >>> _page_click_eval.__name__
        '_page_click_eval'
        >>> callable(_page_click_eval)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    selector = str(payload.get("selector", ""))
    if not target_id or not selector:
        raise ValueError("target_id and selector are required")
    center_x, center_y = await _resolve_box_eval(browser, target_id, selector)
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


async def _page_click_coordinates(
    payload: dict[str, Any], context: DaemonContext
) -> dict[str, Any]:
    """Purpose: click an Edge page at explicit viewport coordinates via page-scoped CDP.

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, numeric ``x``/``y``, and
            optional ``button``/``click_count``.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, clicked coordinates, button, click count, and flag.

    Notes:
        Unlike ``page-click`` (which resolves a CSS selector to its element box via the DOM
        domain), this action dispatches the ``Input.dispatchMouseEvent`` sequence directly at
        caller-provided viewport coordinates — the same page-scoped CDP events ``page-click``
        issues, at a location no selector can address (e.g. a cross-origin iframe such as a
        reCAPTCHA widget, whose inner document is intentionally unreachable from the parent
        page's DOM). ``x`` and ``y`` are viewport-relative CSS pixels, as returned by
        ``getBoundingClientRect()`` from the parent page perspective. ``button`` (one of
        ``left``/``middle``/``right``/``back``/``forward``) and ``click_count`` (1 for a plain
        click, 2+ for double/triple clicks) mirror the CDP ``Input.dispatchMouseEvent`` fields —
        a double-click is emitted as pressed/released pairs whose ``clickCount`` escalates 1, 2,
        … exactly as a real mouse would. Like its selector-based twin, the click is directly
        observable on an always-visible managed window and is therefore deliberately NOT
        approval-gated.

    Examples:
        >>> _page_click_coordinates.__name__
        '_page_click_coordinates'
        >>> callable(_page_click_coordinates)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    raw_x = payload.get("x")
    raw_y = payload.get("y")
    if raw_x is None or raw_y is None:
        raise ValueError("x and y must be numbers")
    try:
        x = float(raw_x)
        y = float(raw_y)
    except (TypeError, ValueError) as error:
        raise ValueError("x and y must be numbers") from error
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("x and y must be finite numbers")
    if not target_id:
        raise ValueError("target_id is required")
    button = str(payload.get("button", "left"))
    if button not in ("left", "middle", "right", "back", "forward"):
        raise ValueError("button must be one of left, middle, right, back, forward")
    raw_count = payload.get("click_count", 1)
    try:
        click_count = int(raw_count)
    except (TypeError, ValueError) as error:
        raise ValueError("click_count must be a positive integer") from error
    if isinstance(raw_count, float) and not raw_count.is_integer():
        raise ValueError("click_count must be a positive integer")
    if click_count < 1:
        raise ValueError("click_count must be a positive integer")
    calls: list[tuple[str, CdpParams]] = [
        ("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    ]
    for count in range(1, click_count + 1):
        calls.append(
            (
                "Input.dispatchMouseEvent",
                {"type": "mousePressed", "x": x, "y": y, "button": button, "clickCount": count},
            )
        )
        calls.append(
            (
                "Input.dispatchMouseEvent",
                {"type": "mouseReleased", "x": x, "y": y, "button": button, "clickCount": count},
            )
        )
    await browser.page_session(target_id, calls)
    return {
        "profile": name,
        "target_id": target_id,
        "x": x,
        "y": y,
        "button": button,
        "click_count": click_count,
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
    fallback_selector = str(payload.get("fallback_selector", ""))
    if not target_id or not selector:
        raise ValueError("target_id and selector are required")
    center_x, center_y, used_selector = await _resolve_box_with_fallback(
        browser, target_id, selector, fallback_selector
    )
    await browser.page_session(
        target_id,
        [("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": center_x, "y": center_y})],
    )
    return {
        "profile": name,
        "target_id": target_id,
        "selector": used_selector,
        "x": center_x,
        "y": center_y,
        "hovered": True,
    }


async def _page_press(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: press and release a keyboard key on an Edge page via CDP Input domain.

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, required ``key`` (e.g.
            ``"Space"``, ``"Enter"``, ``"ArrowDown"``, ``"a"``), optional ``text`` (character
            to insert on keyDown), optional ``modifier`` (bitfield: 1=Alt, 2=Ctrl/Cmd,
            4=Meta/Cmd, 8=Shift).
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, key pressed, and a confirmed flag.

    Notes:
        Uses ``Input.dispatchKeyEvent`` (``keyDown`` + ``keyUp``), which is the real CDP
        input pipeline — unlike ``page-evaluate`` + ``new KeyboardEvent().dispatchEvent()``
        which only fires in the JS event system and may be ignored by the page's internal
        handlers. This is the correct primitive for play/pause (``Space``), navigation
        (``ArrowDown``/``ArrowUp``/``Enter``), and any keyboard shortcut on complex SPAs
        like YouTube Music.

    Examples:
        >>> _page_press.__name__
        '_page_press'
        >>> callable(_page_press)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    key = str(payload.get("key", ""))
    if not target_id or not key:
        raise ValueError("target_id and key are required")

    # Key-to-virtual-keycode map for common keys
    _KEY_CODES: dict[str, int] = {
        "Space": 32,
        "Enter": 13,
        "Tab": 9,
        "Escape": 27,
        "Backspace": 8,
        "Delete": 46,
        "ArrowDown": 40,
        "ArrowUp": 38,
        "ArrowLeft": 37,
        "ArrowRight": 39,
        "Home": 36,
        "End": 35,
        "PageUp": 33,
        "PageDown": 34,
        "F1": 112,
        "F2": 113,
        "F3": 114,
        "F4": 115,
        "F5": 116,
        "F6": 117,
        "F7": 118,
        "F8": 119,
        "F9": 120,
        "F10": 121,
        "F11": 122,
        "F12": 123,
    }
    modifier = int(payload.get("modifier", 0))
    text = payload.get("text", "")

    key_code = _KEY_CODES.get(key, 0)
    # For single printable characters, derive the keycode from the character
    if not key_code and len(key) == 1:
        key_code = ord(key.upper())

    key_down: dict[str, Any] = {
        "type": "keyDown",
        "key": key,
        "code": key,
        "windowsVirtualKeyCode": key_code,
        "nativeVirtualKeyCode": key_code,
    }
    key_up: dict[str, Any] = {
        "type": "keyUp",
        "key": key,
        "code": key,
        "windowsVirtualKeyCode": key_code,
        "nativeVirtualKeyCode": key_code,
    }
    if modifier:
        key_down["modifiers"] = modifier
        key_up["modifiers"] = modifier
    if text:
        key_down["text"] = text

    await browser.page_session(
        target_id,
        [
            ("Input.dispatchKeyEvent", key_down),
            ("Input.dispatchKeyEvent", key_up),
        ],
    )
    return {
        "profile": name,
        "target_id": target_id,
        "key": key,
        "pressed": True,
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
    fallback_selector = str(payload.get("fallback_selector", ""))
    text = str(payload.get("text", ""))
    if not target_id or not selector:
        raise ValueError("target_id and selector are required")
    center_x, center_y, used_selector = await _resolve_box_with_fallback(
        browser, target_id, selector, fallback_selector
    )
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
        clear_expression = f"document.querySelector({json.dumps(used_selector)}).value=''"
        calls.append(("Runtime.evaluate", {"expression": clear_expression}))
    calls.append(("Input.insertText", {"text": text}))
    await browser.page_session(target_id, calls)
    return {"profile": name, "target_id": target_id, "selector": used_selector, "typed": True}


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
        JS runs with full page privileges. An expression evaluating to a value CDP cannot
        JSON-serialize (e.g. ``window.open(...)`` returns a ``Window`` object with circular
        references — live-verified: always fails with ``CDP_ERROR: Object reference chain is too
        long`` under ``returnByValue: true``) is retried once without ``returnByValue``, returning
        a safe textual ``description``/``type`` instead of crashing the whole call (KπX, GRAVÉ).

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
    try:
        result = await browser.page_session(
            target_id,
            [
                (
                    "Runtime.evaluate",
                    {
                        "expression": expression,
                        "returnByValue": True,
                        "awaitPromise": await_promise,
                    },
                )
            ],
        )
    except RuntimeError as error:
        if "Object reference chain is too long" not in str(error):
            raise
        result = await browser.page_session(
            target_id,
            [("Runtime.evaluate", {"expression": expression, "awaitPromise": await_promise})],
        )
        remote = result[0].get("result", {})
        return {
            "profile": name,
            "target_id": target_id,
            "result": remote.get("description") or remote.get("type"),
        }
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
        payload (dict[str, Any]): Profile, ``target_id``, optional ``format``, optional ``output``,
            optional ``force_repaint`` (default False — applying a CSS transform to force
            repaint can break complex SPAs like YouTube Music by modifying computed styles).
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and either a saved ``path`` or base64 ``data``.

    Notes:
        ``Page.captureScreenshot`` already forces an internal repaint. The old
        ``translateZ(0)`` body-transform hack was removed because it modifies the
        page's computed style and breaks SPAs that depend on stable CSS state
        (e.g. YouTube Music — the player bar disappears and the layout shatters).
        Pass ``force_repaint: true`` only as a last resort on pages that genuinely
        need a style nudge.

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

    calls: list[tuple[str, Any]] = []
    if payload.get("force_repaint"):
        calls.append(
            ("Runtime.evaluate", {"expression": "document.body.style.transform = 'translateZ(0)';"})
        )
    calls.append(("Page.captureScreenshot", {"format": image_format}))

    result = await browser.page_session(target_id, calls)
    data = str(result[-1].get("data", ""))

    output = payload.get("output")
    if not output and not payload.get("base64"):
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        output = f"/tmp/browser-proxy-results/screenshot_{timestamp}.png"

    if output:
        out_path = Path(str(output))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(data))
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
    # ONE bundled session for the whole chain (KπX, GRAVÉ — same root cause as `_resolve_box`):
    # `DOM.getDocument`'s `nodeId`s never survive a separate `page_session()` attach/detach cycle.
    # `depth: -1` already returns the FULL recursive subtree, so tag/attributes are read directly
    # from that ONE tree client-side — never a per-match `DOM.describeNode` round trip, which would
    # have reintroduced the exact same cross-session bug for a variable-length batch of node ids.
    calls: list[tuple[str, CdpParams]] = [
        ("DOM.getDocument", {"depth": -1}),
        (
            "DOM.querySelectorAll",
            lambda results: {
                "nodeId": results[0].get("root", {}).get("nodeId"),
                "selector": selector,
            },
        ),
    ]
    document, found = await browser.page_session(target_id, calls)
    node_ids = found.get("nodeIds", [])
    if not node_ids:
        return {"profile": name, "target_id": target_id, "selector": selector, "matches": []}
    by_node_id: dict[int, dict[str, Any]] = {}
    _index_dom_tree(document.get("root", {}), by_node_id)
    matches = [
        {
            "node_id": node_id,
            "tag": by_node_id.get(node_id, {}).get("nodeName", ""),
            "attributes": by_node_id.get(node_id, {}).get("attributes", []),
        }
        for node_id in node_ids
    ]
    return {"profile": name, "target_id": target_id, "selector": selector, "matches": matches}


def _index_dom_tree(node: dict[str, Any], by_node_id: dict[int, dict[str, Any]]) -> None:
    """Purpose: flatten one `DOM.getDocument(depth=-1)` recursive subtree into a `nodeId` lookup.

    Args:
        node (dict[str, Any]): One CDP DOM node (with an optional ``children`` list).
        by_node_id (dict[int, dict[str, Any]]): Accumulator mutated in place — ``nodeId`` to the
            real node object, for every node in the subtree.

    Returns:
        None: Mutates ``by_node_id`` in place; recurses into ``children`` and ``shadowRoots``.

    Examples:
        >>> acc: dict[int, dict[str, Any]] = {}
        >>> _index_dom_tree({"nodeId": 1, "nodeName": "DIV", "children": []}, acc)
        >>> acc[1]["nodeName"]
        'DIV'
    """
    node_id = node.get("nodeId")
    if isinstance(node_id, int):
        by_node_id[node_id] = node
    for child in node.get("children", []) or []:
        _index_dom_tree(child, by_node_id)
    for shadow_root in node.get("shadowRoots", []) or []:
        _index_dom_tree(shadow_root, by_node_id)


async def _page_console_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: read (and optionally clear) console messages captured for an Edge page — now via
    a PERSISTENT boot-time hook, so logs are captured from the page's OWN loading sequence, not
    only after this action first runs (KπX, GRAVÉ: "comment avoir les logs meme quand deja
    charge ?" — the old per-call `Runtime.evaluate` override died with its detached session;
    a `Page.addScriptToEvaluateOnNewDocument` injection survives reloads only while its session
    stays attached, which the daemon's `console_capture()` registry now guarantees).

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, and optional ``clear``.
        context (DaemonContext): Daemon state used to resolve the profile and hold the persistent
            capture.

    Returns:
        dict[str, Any]: Profile, target ID, and captured console message records
        (``[{level, args, ts}, ...]``, bounded at 2000, real console methods kept in passthrough —
        the page keeps working normally).

    Notes:
        Practically: the FIRST call installs the persistent hook (and captures any live messages
        from that moment in the current document); a subsequent ``page-reload`` then produces the
        FULL boot log sequence on the next read — no more permanently-empty first reads.

    Examples:
        >>> _page_console_list.__name__
        '_page_console_list'
        >>> callable(_page_console_list)
        True
    """
    name, _browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    clear = bool(payload.get("clear", False))
    capture = await context.console_capture(name, target_id)
    messages = await capture.entries(clear=clear)
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
            ``prompt_text``. **Correct sequence (KπX, GRAVÉ):** navigate/reload to target page
            FIRST, then install policy, then trigger alert. Installing before reload loses the
            override because ``Runtime.evaluate`` does not survive a page load (no persistent
            ``Page.addScriptToEvaluateOnNewDocument`` session).
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
    result = await browser.call("Storage.getCookies", {})
    return {"profile": name, "cookies": result.get("cookies", [])}


@require_approval
@require_verification("name")
async def _cookie_set(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: set one browser-level cookie for a started Edge profile — profile-wide, not
    page-scoped (KπX, GRAVÉ: live-verified the old `Network.setCookie` backend always failed with
    `CDP_ERROR: 'Network.setCookie' wasn't found` because `Network.*` requires a per-page session;
    the correct browser-level backend is `Storage.setCookies`, whose plural method accepts a list
    and REQUIRES the cookie's `url` for categorization — the singular `Storage.setCookie` does not
    exist at the browser level, probed live method by method).

    Args:
        payload (dict[str, Any]): Profile, ``name``, ``value``, ``domain``, optional ``path``,
            ``secure``, ``http_only``. The scheme for the required CDP ``url`` is derived: an
            ``secure`` cookie implies `https://`, otherwise `http://`.
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
    secure = bool(payload.get("secure", True))
    cookie_url = f"https://{domain}/" if secure else f"http://{domain}/"
    await browser.call(
        "Storage.setCookies",
        {
            "cookies": [
                {
                    "name": cookie_name,
                    "value": str(payload.get("value", "")),
                    "domain": domain,
                    "path": str(payload.get("path", "/")),
                    "secure": secure,
                    "httpOnly": bool(payload.get("http_only", False)),
                    "url": cookie_url,
                }
            ]
        },
    )
    return {"profile": name, "name": cookie_name, "domain": domain, "set": True}


@require_approval
@require_preflight("name", "domain")
async def _cookie_remove(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: remove one browser-level cookie by name and domain. `Network.deleteCookies` needs a
    per-page session (the browser level exposes neither `Network.*` nor `Storage.deleteCookies`,
    probed live), so one real page target receives the call — the default profile's first live
    page target. Cookies are profile-shared, so deleting through any of the profile's pages removes
    the cookie for the whole profile.

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
    page_targets = [t for t in await browser.targets() if t.get("type") == "page"]
    if not page_targets:
        raise RuntimeError("CDP_ERROR: no live page target available for cookie deletion")
    # KπX, GRAVÉ: found live while wiring browser-solve-captcha's own target lookup — raw
    # `Target.getTargets` entries key the CDP target id as `targetId`, never `id` (confirmed live
    # via `do raw` earlier this session); the previous `["id"]` here always raised `KeyError` on
    # real CDP data and was masked because its own unit test mocked `{"id": ...}` (matching the bug,
    # not real CDP shape) and the one live attempt failed earlier at the extension-approval step,
    # never reaching this line.
    target_id = str(page_targets[0]["targetId"])
    await browser.page_session(
        target_id, [("Network.deleteCookies", {"name": cookie_name, "domain": domain})]
    )
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
@require_preflight("keys")
async def _storage_local_remove(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: remove one or MORE Edge page ``localStorage`` entries by key, batch, in ONE call —
    completes the symmetric storage/cookie action pair (KπX: live-verified `localStorage.getItem`/
    `setItem` had no `removeItem`/`clear` counterpart; probed live via `page-evaluate` before
    building this dedicated action).

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, and ``keys`` — a non-empty list
            of localStorage keys to remove in ONE call, never one call per key.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, the removed keys, and a confirmed removed flag.

    Examples:
        >>> _storage_local_remove.__name__
        '_storage_local_remove'
        >>> callable(_storage_local_remove)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    keys = payload.get("keys")
    if not target_id or not isinstance(keys, list) or not keys:
        raise ValueError("target_id is required and keys must be a non-empty list")
    key_list = [str(key) for key in keys]
    expression = "; ".join(f"localStorage.removeItem({json.dumps(key)})" for key in key_list)
    await browser.page_session(target_id, [("Runtime.evaluate", {"expression": expression})])
    return {"profile": name, "target_id": target_id, "keys": key_list, "removed": True}


@require_approval
async def _storage_local_clear(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: wipe EVERY Edge page ``localStorage`` entry for one page's origin in ONE call —
    completes the symmetric storage/cookie action pair, same rationale as `_storage_local_remove`.

    Args:
        payload (dict[str, Any]): Profile and required ``target_id``. No identity field to
            preflight beyond the always-validated `target_id` — same shape as other whole-origin
            wipes in this registry.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and a confirmed cleared flag.

    Examples:
        >>> _storage_local_clear.__name__
        '_storage_local_clear'
        >>> callable(_storage_local_clear)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    await browser.page_session(
        target_id, [("Runtime.evaluate", {"expression": "localStorage.clear()"})]
    )
    return {"profile": name, "target_id": target_id, "cleared": True}


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
    """Purpose: detect or drive a CAPTCHA challenge, escalating ``click_checkbox`` to a REAL
    compositor-level CDP click when the extension reports a detected iframe (KπX, GRAVÉ —
    live-verified against the official Google reCAPTCHA demo: a content-script click on a
    genuinely cross-origin iframe — `www.google.com`, confirmed live via the iframe's own `src`,
    which is how EVERY real deployment serves it — never reaches the checkbox rendered inside it).
    The extension reports the iframe's own bounding rect + the tab's `url` (never attempts the
    ineffective same-origin click itself any more); this daemon correlates that `url` against
    `browser.targets()` to resolve the CDP `target_id` (the two id systems have no first-class
    mapping — same correlation need as `_correlate_cdp_targets()`, here by exact URL match since a
    single active-tab lookup is unambiguous), computes the checkbox's stable click point (its icon
    sits a fixed ~30px from the anchor iframe's left edge, vertically centered, regardless of
    overall widget width/theme — live-verified), and dispatches the exact same
    ``Input.dispatchMouseEvent`` sequence ``page-click-coordinates`` uses for a location no CSS
    selector can address.

    Args:
        payload (dict[str, Any]): Profile, ``action`` (detect/click_checkbox/click_grid), opt.
            ``cells``.
        context (DaemonContext): Daemon state exposing the paired extension and CDP browser.

    Returns:
        dict[str, Any]: Extension-confirmed CAPTCHA interaction information; for a successfully
        escalated ``click_checkbox``, also the real coordinates clicked (nested ``click`` key).

    Examples:
        >>> _browser_solve_captcha.__name__
        '_browser_solve_captcha'
        >>> callable(_browser_solve_captcha)
        True
    """
    reply = await _extension(payload, context, "captcha.solve")
    action = str(payload.get("action", ""))
    rect = reply.get("rect")
    tab_url = reply.get("url")
    if action != "click_checkbox" or not isinstance(rect, dict) or not tab_url:
        return reply
    _, browser = _profile(payload, context)
    matches = [
        target
        for target in await browser.targets()
        if target.get("type") == "page" and target.get("url") == tab_url
    ]
    if not matches:
        return reply
    target_id = str(matches[0]["targetId"])
    click_x = float(rect["left"]) + 30.0
    click_y = float(rect["top"]) + float(rect["height"]) / 2.0
    click_result = await _page_click_coordinates(
        {
            "profile": payload.get("profile", "default"),
            "target_id": target_id,
            "x": click_x,
            "y": click_y,
        },
        context,
    )
    return {
        **reply,
        "clicked": True,
        "reason": "clicked via CDP-level coordinate dispatch (cross-origin iframe checkbox)",
        "click": click_result,
    }


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
    """Purpose: execute ONE operation across any of the three protocol families — absolute
    flexibility, never freezing the protocol (KπX, GRAVÉ: "il devrait permettre de faire tout ce
    que les autres do permettent et en plus d'autres choses... sans figer le protocole").

    Args:
        payload (dict[str, Any]): Profile, ``method``, optional ``params``, optional
            ``protocol`` (``"cdp-browser"`` default | ``"cdp-page"`` | ``"ext"``), and, for
            ``cdp-page``, required ``target_id`` plus optional ``calls`` (an ORDERED list of
            ``[method, params]`` pairs executed sequentially within ONE attached page session —
            later calls may reference earlier results via callables, same ``CdpParams`` mechanism
            every ``page-*`` action uses). For ``ext``, ``params`` may be an object (single
            argument) OR a list (positional arguments), e.g.
            ``{"protocol":"ext","method":"storage.local.get","params":[null]}``.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: ``{profile, protocol, method, result}`` — ``result`` is the raw backend
        response, never re-wrapped: CDP browser-level result object, CDP page-level result list
        (one entry per ``calls`` entry, or a single result for a bare ``method``), or the chrome
        API result for ``ext``. Any JSON value is returned faithfully (dict/list/str/bool), never
        coerced.

    Notes:
        The dynamic read-only allowlist (``daemon.dispatch``) applies; every other method is
        fail-closed approval-gated, identically across all three families.

    Examples:
        >>> _raw.__name__
        '_raw'
        >>> callable(_raw)
        True
    """
    protocol = str(payload.get("protocol", "cdp-browser")).strip().lower()
    if protocol == "cdp-page" and payload.get("calls") is not None:
        # A `calls` bundle carries its own per-call methods — no top-level `method` needed.
        return await _raw_page_bundle(payload, context)
    method = str(payload.get("method", ""))
    if not method:
        raise ValueError("method is required")
    params = payload.get("params", {})
    name, browser = _profile(payload, context)
    if protocol == "cdp-browser":
        if not isinstance(params, dict):
            raise ValueError("params must be a JSON object for cdp-browser")
        return {
            "profile": name,
            "protocol": protocol,
            "method": method,
            "result": await browser.call(method, params),
        }
    if protocol == "cdp-page":
        target_id = str(payload.get("target_id", ""))
        if not target_id:
            raise ValueError("target_id is required for cdp-page")
        if not isinstance(params, dict):
            raise ValueError("params must be a JSON object for a single cdp-page method")
        return {
            "profile": name,
            "protocol": protocol,
            "target_id": target_id,
            "method": method,
            "result": (await browser.page_session(target_id, [(method, params)]))[0],
        }
    if protocol == "ext":
        return {
            "profile": name,
            "protocol": protocol,
            "method": method,
            "result": await _extension(payload, context, "chrome.call"),
        }
    raise ValueError(f"unknown protocol: {protocol}")


async def _raw_page_bundle(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: run an ordered `calls` bundle within ONE attached CDP page session (`cdp-page` raw).

    Args:
        payload (dict[str, Any]): Profile, required ``target_id``, and required non-empty
            ``calls`` — an ordered list of ``[method, params]`` pairs.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: ``{profile, protocol, target_id, calls: [methods...], result: [...]}`` —
        one result entry per call, same order, from a single attach/detach (node ids from earlier
        calls stay valid for later ones within the same session).

    Notes:
        This is the only raw form that does NOT require a top-level ``method`` — each ``calls``
        entry carries its own.

    Examples:
        >>> asyncio.iscoroutinefunction(_raw_page_bundle)
        True
        >>> callable(_raw_page_bundle)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required for cdp-page")
    raw_calls = payload.get("calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        raise ValueError("calls must be a non-empty ordered list of [method, params] pairs")
    calls: list[tuple[str, CdpParams]] = []
    for pair in raw_calls:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not isinstance(pair[0], str)
            or not isinstance(pair[1], dict)
        ):
            raise ValueError("each call must be [method, params] with object params")
        calls.append((pair[0], pair[1]))
    return {
        "profile": name,
        "protocol": "cdp-page",
        "target_id": target_id,
        "calls": [call[0] for call in calls],
        "result": await browser.page_session(target_id, calls),
    }


async def _clipboard_read(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: read text from the system clipboard using the extension's background access.

    Args:
        payload (dict[str, Any]): Profile.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: The clipboard text content.

    Examples:
        >>> _clipboard_read.__name__
        '_clipboard_read'
        >>> callable(_clipboard_read)
        True
    """
    return await _extension(payload, context, "clipboard.read")


@require_approval
async def _clipboard_write(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: write text to the system clipboard using the extension's background access.

    Args:
        payload (dict[str, Any]): Profile, required ``text``.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Confirmed written flag.

    Examples:
        >>> _clipboard_write.__name__
        '_clipboard_write'
        >>> callable(_clipboard_write)
        True
    """
    if "text" not in payload:
        raise ValueError("clipboard-write requires 'text'")
    return await _extension(payload, context, "clipboard.write")


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
        _action("extension-list", "Extensions", _extension_list),
        _action("extension-get", "Extensions", _extension_get),
        _action("extension-enable", "Extensions", _extension_enable),
        _action("extension-disable", "Extensions", _extension_disable),
        _action("extension-reload", "Extensions", _extension_reload),
        _action("extension-search", "Extensions", _extension_search),
        _action("clipboard-read", "Advanced", _clipboard_read),
        _action("clipboard-write", "Advanced", _clipboard_write),
        _action("page-navigate", "Navigation", _page_navigate),
        _action("page-reload", "Navigation", _page_reload),
        _action("page-back", "Navigation", _page_back),
        _action("page-forward", "Navigation", _page_forward),
        _action("page-click", "Interaction", _page_click),
        _action("page-click-eval", "Interaction", _page_click_eval),
        _action("page-click-coordinates", "Interaction", _page_click_coordinates),
        _action("page-hover", "Interaction", _page_hover),
        _action("page-press", "Interaction", _page_press),
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
        _action("cookie-list", "Cookies", _cookie_list),
        _action("cookie-set", "Cookies", _cookie_set),
        _action("cookie-remove", "Cookies", _cookie_remove),
        _action("storage-local-get", "Storage", _storage_local_get),
        _action("storage-local-set", "Storage", _storage_local_set),
        _action("storage-local-remove", "Storage", _storage_local_remove),
        _action("storage-local-clear", "Storage", _storage_local_clear),
        _action("group-create", "Groups", _group_create),
        _action("group-update", "Groups", _group_update),
        _action("group-move", "Groups", _group_move),
        _action("group-add-tabs", "Groups", _group_add_tabs),
        _action("group-remove-tabs", "Groups", _group_remove_tabs),
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
