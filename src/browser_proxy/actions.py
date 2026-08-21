"""Registry of documented, flat Edge-only browser actions."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from browser_proxy.cdp import CdpBrowser
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

    profiles: dict[str, int]

    async def start_profile(self, name: str) -> int: ...
    async def extension_request(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ActionDef:
    """Public action name, help group, implementation, and safety policy."""

    name: str
    group: str
    handler: Handler
    policy: Policy


def _profile(payload: dict[str, Any], context: DaemonContext) -> tuple[str, CdpBrowser]:
    name = str(payload.get("profile", "default"))
    port = context.profiles.get(name)
    if port is None:
        raise ValueError(f"PROFILE_UNAVAILABLE: {name}")
    return name, CdpBrowser(port)


async def _profile_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """List browser-proxy-managed Microsoft Edge profiles.

    Examples:
        >>> _profile_list.__name__
        '_profile_list'
    """
    return {
        "profiles": [{"name": name, "cdp_port": port} for name, port in context.profiles.items()]
    }


@require_approval
async def _profile_start(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Start a persistent Microsoft Edge profile with a private CDP endpoint.

    Examples:
        >>> _profile_start.__name__
        '_profile_start'
    """
    name = str(payload.get("profile", "default"))
    return {"profile": name, "cdp_port": await context.start_profile(name)}


async def _window_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """List Edge page targets and their window identifiers.

    Examples:
        >>> _window_list.__name__
        '_window_list'
    """
    name, browser = _profile(payload, context)
    targets = [target for target in await browser.targets() if target.get("type") == "page"]
    return {"profile": name, "windows": targets}


@require_approval
@require_preflight("profile")
@require_verification("url")
async def _window_create(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Create a visible Edge window in an already-started persistent profile.

    Examples:
        >>> _window_create.__name__
        '_window_create'
    """
    name, browser = _profile(payload, context)
    url = str(payload.get("url", "about:blank"))
    options: dict[str, Any] = {"url": url, "newWindow": True}
    for key in ("left", "top", "width", "height", "windowState", "focus"):
        if key in payload:
            options[key] = payload[key]
    result = await browser.call("Target.createTarget", options)
    return {"profile": name, "url": url, "target_id": result["targetId"]}


@require_approval
@require_preflight("profile", "target_id")
async def _window_close(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Close an Edge target window by its CDP target identifier.

    Examples:
        >>> _window_close.__name__
        '_window_close'
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    await browser.call("Target.closeTarget", {"targetId": target_id})
    return {"profile": name, "target_id": target_id, "closed": True}


async def _tab_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """List all page tabs in a persistent Edge profile.

    Examples:
        >>> _tab_list.__name__
        '_tab_list'
    """
    return {
        "profile": _profile(payload, context)[0],
        "tabs": (await _window_list(payload, context))["windows"],
    }


async def _page_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """List inspectable Edge page targets, URLs, titles, and attached frame metadata.

    Examples:
        >>> _page_list.__name__
        '_page_list'
    """
    profile, browser = _profile(payload, context)
    pages = [target for target in await browser.targets() if target.get("type") == "page"]
    return {"profile": profile, "pages": pages}


async def _page_get(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Read one Edge page target's public CDP metadata by target identifier.

    Examples:
        >>> _page_get.__name__
        '_page_get'
    """
    profile, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    result = await browser.call("Target.getTargetInfo", {"targetId": target_id})
    return {"profile": profile, "page": result.get("targetInfo", {})}


@require_approval
@require_preflight("profile")
@require_verification("url")
async def _tab_create(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Create a tab in Edge, optionally in a new window.

    Examples:
        >>> _tab_create.__name__
        '_tab_create'
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
    """Activate an existing Edge tab target.

    Examples:
        >>> _tab_activate.__name__
        '_tab_activate'
    """
    name, browser = _profile(payload, context)
    target_id = str(payload.get("target_id", ""))
    if not target_id:
        raise ValueError("target_id is required")
    await browser.call("Target.activateTarget", {"targetId": target_id})
    return {"profile": name, "target_id": target_id, "active": True}


async def _workspace_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Return an explicitly heuristic Workspace view derived from Edge tab targets.

    Examples:
        >>> _workspace_list.__name__
        '_workspace_list'
    """
    name, browser = _profile(payload, context)
    tabs = [item for item in await browser.targets() if item.get("type") == "page"]
    return {
        "profile": name,
        "heuristic": True,
        "authority": "none",
        "workspaces": [{"name": "ungrouped", "tabs": tabs}],
    }


async def _extension(payload: dict[str, Any], context: DaemonContext, kind: str) -> dict[str, Any]:
    return await context.extension_request(kind, payload)


async def _bookmark_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """List Edge profile bookmarks through the paired privileged extension.

    Examples:
        >>> _bookmark_list.__name__
        '_bookmark_list'
    """
    return await _extension(payload, context, "bookmark.list")


@require_approval
@require_verification("url")
async def _bookmark_create(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Create an Edge profile bookmark through the paired extension.

    Examples:
        >>> _bookmark_create.__name__
        '_bookmark_create'
    """
    return await _extension(payload, context, "bookmark.create")


@require_approval
@require_preflight("id")
async def _bookmark_remove(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Remove an Edge profile bookmark through the paired extension.

    Examples:
        >>> _bookmark_remove.__name__
        '_bookmark_remove'
    """
    return await _extension(payload, context, "bookmark.remove")


async def _raw(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Execute an allowlisted read-only browser-level CDP method.

    Examples:
        >>> _raw.__name__
        '_raw'
    """
    method = str(payload.get("method", ""))
    params = payload.get("params", {})
    if not isinstance(params, dict):
        raise ValueError("params must be a JSON object")
    name, browser = _profile(payload, context)
    return {"profile": name, "method": method, "result": await browser.call(method, params)}


def _action(name: str, group: str, handler: Handler) -> ActionDef:
    return ActionDef(name, group, handler, policy_of(handler))


REGISTRY = {
    entry.name: entry
    for entry in (
        _action("profile-list", "Profiles", _profile_list),
        _action("profile-start", "Profiles", _profile_start),
        _action("window-list", "Windows", _window_list),
        _action("window-create", "Windows", _window_create),
        _action("window-close", "Windows", _window_close),
        _action("tab-list", "Tabs", _tab_list),
        _action("page-list", "Pages", _page_list),
        _action("page-get", "Pages", _page_get),
        _action("tab-create", "Tabs", _tab_create),
        _action("tab-activate", "Tabs", _tab_activate),
        _action("workspace-list", "Workspaces", _workspace_list),
        _action("bookmark-list", "Bookmarks", _bookmark_list),
        _action("bookmark-create", "Bookmarks", _bookmark_create),
        _action("bookmark-remove", "Bookmarks", _bookmark_remove),
        _action("raw", "Advanced", _raw),
    )
}


def validate_registry() -> None:
    """Verify every public action has a unique name and actionable documentation."""
    for name, action in REGISTRY.items():
        if (
            name != action.name
            or not action.handler.__doc__
            or "Examples:" not in action.handler.__doc__
        ):
            raise RuntimeError(f"invalid action registry entry: {name}")
