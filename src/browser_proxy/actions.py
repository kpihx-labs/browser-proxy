"""Registry of documented, flat Edge-only browser actions."""

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from browser_proxy.cdp import CdpBrowser
from browser_proxy.doc import attach_public_docstrings
from browser_proxy.paths import edge_cdp_port, edge_profile_dir, edge_profile_state
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
    """Purpose: list real page-type CDP targets — the one shared fact behind 4 actions.

    Args:
        browser (CdpBrowser): Client bound to one managed Edge debugging port.

    Returns:
        list[dict[str, Any]]: Every ``Target.getTargets`` entry with ``type == "page"``. Used
        identically by ``window-list``, ``tab-list``, ``page-list``, and ``workspace-list`` —
        never a private re-implementation of this exact filter in each handler.

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


async def _window_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: list Edge windows, each with its own real bounds and the tabs living inside it.

    Args:
        payload (dict[str, Any]): Object containing a started ``profile`` name.
        context (DaemonContext): Daemon state used to resolve that profile.

    Returns:
        dict[str, Any]: Profile name and one entry per REAL Edge window (``window_id``, ``bounds``,
        ``tabs``) — never a flat target list. Grouping is resolved via one genuine
        ``Browser.getWindowForTarget`` call per tab (see ``_window_id_for_target``), not guessed:
        ``Target.getTargets`` alone carries no window-grouping field, so a flat list (the previous
        implementation, byte-identical to ``tab-list``) could never answer "which tab is in which
        window" once more than one real window is open.

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
    return {"profile": name, "windows": [grouped[window_id] for window_id in order]}


async def _create_window_tab(
    browser: CdpBrowser,
    context: DaemonContext,
    profile: str,
    window_id: int | None,
    url: str,
    capture_chrome_id: bool,
) -> tuple[str, int | None]:
    """Purpose: create one tab inside an existing window, optionally capturing its REAL chrome tab id.

    Args:
        browser (CdpBrowser): Client bound to one managed Edge debugging port.
        context (DaemonContext): Daemon state exposing the paired extension.
        profile (str): Target browser-proxy profile (routes the capture request correctly).
        window_id (int | None): Real Edge window to open the tab in, from ``Browser.getWindowForTarget``.
        url (str): Absolute page URL for the new tab.
        capture_chrome_id (bool): Whether a real ``chrome.tabs.Tab.id`` must be resolved — only
            needed when this tab will be grouped afterwards (``chrome.tabs.group`` requires the
            extension's own numeric id, never the CDP ``targetId`` string).

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


async def _apply_window_items(
    items: Any, browser: CdpBrowser, context: DaemonContext, profile: str, window_id: int | None
) -> list[dict[str, Any]] | None:
    """Purpose: create a whole ordered tab/group layout inside one window from a flat JSON list.

    Args:
        items (Any): Untrusted ``window-create``/``items`` payload value; ``None`` means no layout.
        browser (CdpBrowser): Client bound to one managed Edge debugging port.
        context (DaemonContext): Daemon state exposing the paired extension (needed for groups).
        profile (str): Target browser-proxy profile.
        window_id (int | None): Real Edge window every created tab/group must land in.

    Returns:
        list[dict[str, Any]] | None: One entry per item in the exact order given (``None`` when
        ``items`` was omitted entirely — the pre-existing single-``url`` behavior is unaffected).
        Each entry is ``{"type": "tab", "url", "target_id"}`` or ``{"type": "group", "title",
        "tabs": [...], "group": <extension-confirmed result>}``. Reusable by any future action that
        needs to populate an existing window (not only a freshly created one).

    Raises:
        ValueError: If ``items``, or any entry inside it, is not shaped as documented.

    Examples:
        >>> asyncio.iscoroutinefunction(_apply_window_items)
        True
        >>> callable(_apply_window_items)
        True
    """
    if items is None:
        return None
    if not isinstance(items, list):
        raise ValueError("items must be a list of {type: 'tab'|'group', ...} objects")
    created: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or "type" not in item:
            raise ValueError(f"items[{index}] must be an object with a 'type' field")
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
                raise ValueError(f"items[{index}] of type 'group' requires a non-empty 'tabs' list")
            chrome_tab_ids: list[int] = []
            tab_entries: list[dict[str, Any]] = []
            for tab_index, tab_item in enumerate(tabs_spec):
                if isinstance(tab_item, dict):
                    tab_url = str(tab_item.get("url", "about:blank"))
                elif isinstance(tab_item, str):
                    tab_url = tab_item
                else:
                    raise ValueError(
                        f"items[{index}].tabs[{tab_index}] must be a URL or {{'url': ...}}"
                    )
                target_id, chrome_tab_id = await _create_window_tab(
                    browser, context, profile, window_id, tab_url, capture_chrome_id=True
                )
                if chrome_tab_id is None:
                    raise RuntimeError(
                        f"EXTENSION_UNAVAILABLE: {profile} (could not capture a real tab id for "
                        f"items[{index}].tabs[{tab_index}] — grouping requires the paired extension)"
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
            raise ValueError(f"items[{index}].type must be 'tab' or 'group', got {kind!r}")
    return created


@require_preflight("profile")
@require_verification("url")
async def _window_create(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: create a visible Edge window in a started persistent profile, optionally laying out
    a whole ordered tab/group structure inside it in one call.

    Args:
        payload (dict[str, Any]): Profile, URL, optional CDP window bounds/state, and an optional
            ``items`` list — e.g. ``[{"type":"tab","url":"..."}, {"type":"group","title":"...",
            "tabs":["...","..."]}]`` — created in that exact order after the window opens, so a
            whole tab/group setup (one tab, then a group of 3 tabs, then another tab, ...) is a
            single deliberate command instead of many separate approval-free but still sequential
            ``tab-create``/``group-create`` calls.
        context (DaemonContext): Daemon state used to resolve the Edge profile and, only when
            ``items`` contains a group, the paired extension.

    Returns:
        dict[str, Any]: Profile, requested URL, newly created target identifier, the window's real
        ``window_id`` (from ``Browser.getWindowForTarget`` — see ``## Window grouping``), and, only
        when ``items`` was supplied, the ordered ``items`` creation result.

    Notes:
        Deliberately NOT ``@require_approval`` (KπX directive): every managed Edge window is
        already always real and visible (never headless — see ``## Edge lifecycle``), so opening
        one is directly observable the instant it happens; it carries no hidden side effect an
        approval overlay would meaningfully gate. Because the parent action is itself
        approval-free, the tabs/groups created via ``items`` bypass ``tab-create``'s/
        ``group-create``'s own individual approval gates too — this whole layout is one single
        deliberate command, not a series of separately-approved ones. Still preflight-``profile``
        and verify-``url``.

    Examples:
        >>> _window_create.__name__
        '_window_create'
        >>> callable(_window_create)
        True
    """
    name, browser = _profile(payload, context)
    url = str(payload.get("url", "about:blank"))
    options: dict[str, Any] = {"url": url, "newWindow": True}
    for key in ("left", "top", "width", "height", "windowState", "focus"):
        if key in payload:
            options[key] = payload[key]
    result = await browser.call("Target.createTarget", options)
    target_id = result["targetId"]
    window_id, _bounds = await _window_id_for_target(browser, target_id)
    response: dict[str, Any] = {
        "profile": name,
        "url": url,
        "target_id": target_id,
        "window_id": window_id,
    }
    items_result = await _apply_window_items(
        payload.get("items"), browser, context, name, window_id
    )
    if items_result is not None:
        response["items"] = items_result
    return response


@require_approval
@require_preflight("profile", "target_id")
async def _window_close(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: close an Edge target window by its CDP target identifier.

    Args:
        payload (dict[str, Any]): Profile and required ``target_id`` to close.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, target ID, and confirmed close intent.

    Examples:
        >>> _window_close.__name__
        '_window_close'
        >>> callable(_window_close)
        True
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    await browser.call("Target.closeTarget", {"targetId": target_id})
    return {"profile": name, "target_id": target_id, "closed": True}


async def _tab_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: list all page tabs in a persistent Edge profile.

    Args:
        payload (dict[str, Any]): Object containing a started ``profile`` name.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: Profile name and its Edge page targets as tabs.

    Examples:
        >>> _tab_list.__name__
        '_tab_list'
        >>> callable(_tab_list)
        True
    """
    name, browser = _profile(payload, context)
    return {"profile": name, "tabs": await _page_targets(browser)}


async def _page_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: list inspectable Edge page targets and CDP metadata.

    Args:
        payload (dict[str, Any]): Object containing a started ``profile`` name.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: Profile name and inspectable CDP page target records.

    Examples:
        >>> _page_list.__name__
        '_page_list'
        >>> callable(_page_list)
        True
    """
    profile, browser = _profile(payload, context)
    return {"profile": profile, "pages": await _page_targets(browser)}


async def _page_get(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: read one Edge page target's public CDP metadata.

    Args:
        payload (dict[str, Any]): Profile and required CDP ``target_id``.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: Profile name and the selected page target metadata.

    Examples:
        >>> _page_get.__name__
        '_page_get'
        >>> callable(_page_get)
        True
    """
    profile, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    result = await browser.call("Target.getTargetInfo", {"targetId": target_id})
    return {"profile": profile, "page": result.get("targetInfo", {})}


@require_preflight("profile")
@require_verification("url")
async def _tab_create(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: create a tab in Edge, optionally in a new window.

    Args:
        payload (dict[str, Any]): Profile, URL, and optional ``new_window`` boolean.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: Profile, target ID, and requested URL for the tab.

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
    result = await browser.call(
        "Target.createTarget", {"url": url, "newWindow": bool(payload.get("new_window", False))}
    )
    return {"profile": name, "target_id": result["targetId"], "url": url}


@require_approval
@require_preflight("profile", "target_id")
async def _tab_activate(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: activate an existing Edge tab target.

    Args:
        payload (dict[str, Any]): Profile and required CDP ``target_id``.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: Profile, activated target ID, and activation state.

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


async def _workspace_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: return a heuristic Workspace view derived from Edge tab targets.

    Args:
        payload (dict[str, Any]): Object containing a started ``profile`` name.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: Explicitly non-authoritative Workspace-like tab container.

    Examples:
        >>> _workspace_list.__name__
        '_workspace_list'
        >>> callable(_workspace_list)
        True
    """
    name, browser = _profile(payload, context)
    return {
        "profile": name,
        "heuristic": True,
        "authority": "none",
        "workspaces": [{"name": "ungrouped", "tabs": await _page_targets(browser)}],
    }


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
    """Purpose: list Edge profile bookmarks through the paired privileged extension.

    Args:
        payload (dict[str, Any]): Object identifying the Edge profile to inspect.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Profile-scoped bookmark tree supplied by the extension.

    Examples:
        >>> _bookmark_list.__name__
        '_bookmark_list'
        >>> callable(_bookmark_list)
        True
    """
    return await _extension(payload, context, "bookmark.list")


@require_approval
@require_verification("url")
async def _bookmark_create(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: create an Edge profile bookmark through the paired extension.

    Args:
        payload (dict[str, Any]): Profile bookmark data including a URL to save.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed created bookmark information.

    Examples:
        >>> _bookmark_create.__name__
        '_bookmark_create'
        >>> callable(_bookmark_create)
        True
    """
    return await _extension(payload, context, "bookmark.create")


@require_approval
@require_preflight("id")
async def _bookmark_remove(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: remove an Edge profile bookmark through the paired extension.

    Args:
        payload (dict[str, Any]): Profile bookmark data including required bookmark ``id``.
        context (DaemonContext): Daemon state exposing the paired extension.

    Returns:
        dict[str, Any]: Extension-confirmed bookmark removal information.

    Examples:
        >>> _bookmark_remove.__name__
        '_bookmark_remove'
        >>> callable(_bookmark_remove)
        True
    """
    return await _extension(payload, context, "bookmark.remove")


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
        _action("tab-list", "Tabs", _tab_list),
        _action("page-list", "Pages", _page_list),
        _action("page-get", "Pages", _page_get),
        _action("tab-create", "Tabs", _tab_create),
        _action("tab-activate", "Tabs", _tab_activate),
        _action("workspace-list", "Workspaces", _workspace_list),
        _action("group-list", "Groups", _group_list),
        _action("bookmark-list", "Bookmarks", _bookmark_list),
        _action("bookmark-create", "Bookmarks", _bookmark_create),
        _action("bookmark-remove", "Bookmarks", _bookmark_remove),
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
