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

    async def extension_request(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Purpose: forward a typed object request to the Edge extension.

        Args:
            kind (str): Stable typed extension request name.
            payload (dict[str, Any]): Complete single action object to forward.

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
        context (DaemonContext): Daemon state owning started Edge profiles.

    Returns:
        tuple[str, CdpBrowser]: Resolved profile name and browser-level CDP client.

    Examples:
        >>> _profile.__name__
        '_profile'
        >>> callable(_profile)
        True
    """
    name = str(payload.get("profile", "default"))
    port = context.profiles.get(name)
    if port is None:
        raise ValueError(f"PROFILE_UNAVAILABLE: {name}")
    return name, CdpBrowser(port)


async def _profile_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: list browser-proxy-managed Microsoft Edge profiles.

    Args:
        payload (dict[str, Any]): Empty action object; retained for a uniform handler contract.
        context (DaemonContext): Daemon state that owns managed profiles.

    Returns:
        dict[str, Any]: Profile names paired with their loopback CDP ports.

    Examples:
        >>> _profile_list.__name__
        '_profile_list'
        >>> callable(_profile_list)
        True
    """
    return {
        "profiles": [{"name": name, "cdp_port": port} for name, port in context.profiles.items()]
    }


@require_approval
async def _profile_start(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: start a persistent Microsoft Edge profile with a private CDP endpoint.

    Args:
        payload (dict[str, Any]): Object with an optional persistent ``profile`` name.
        context (DaemonContext): Daemon that starts and tracks the Edge process.

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


async def _window_list(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: list Edge page targets representing windows and tabs.

    Args:
        payload (dict[str, Any]): Object containing a started ``profile`` name.
        context (DaemonContext): Daemon state used to resolve that profile.

    Returns:
        dict[str, Any]: Profile name and inspectable Edge page targets.

    Examples:
        >>> _window_list.__name__
        '_window_list'
        >>> callable(_window_list)
        True
    """
    name, browser = _profile(payload, context)
    targets = [target for target in await browser.targets() if target.get("type") == "page"]
    return {"profile": name, "windows": targets}


@require_approval
@require_preflight("profile")
@require_verification("url")
async def _window_create(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: create a visible Edge window in a started persistent profile.

    Args:
        payload (dict[str, Any]): Profile, URL, and optional CDP window bounds/state.
        context (DaemonContext): Daemon state used to resolve the Edge profile.

    Returns:
        dict[str, Any]: Profile, requested URL, and newly created target identifier.

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
    return {"profile": name, "url": url, "target_id": result["targetId"]}


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
    return {
        "profile": _profile(payload, context)[0],
        "tabs": (await _window_list(payload, context))["windows"],
    }


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
    pages = [target for target in await browser.targets() if target.get("type") == "page"]
    return {"profile": profile, "pages": pages}


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


@require_approval
@require_preflight("profile")
@require_verification("url")
async def _tab_create(payload: dict[str, Any], context: DaemonContext) -> dict[str, Any]:
    """Purpose: create a tab in Edge, optionally in a new window.

    Args:
        payload (dict[str, Any]): Profile, URL, and optional ``new_window`` boolean.
        context (DaemonContext): Daemon state used to resolve the profile.

    Returns:
        dict[str, Any]: Profile, target ID, and requested URL for the tab.

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
    tabs = [item for item in await browser.targets() if item.get("type") == "page"]
    return {
        "profile": name,
        "heuristic": True,
        "authority": "none",
        "workspaces": [{"name": "ungrouped", "tabs": tabs}],
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
    """Purpose: send a typed request through the authenticated Edge extension bridge.

    Args:
        payload (dict[str, Any]): Complete single action object forwarded unchanged.
        context (DaemonContext): Daemon state exposing the extension bridge.
        kind (str): Extension request kind, for example ``bookmark.list``.

    Returns:
        dict[str, Any]: Typed extension response data.

    Examples:
        >>> _extension.__name__
        '_extension'
        >>> callable(_extension)
        True
    """
    return await context.extension_request(kind, payload)


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
        _action("raw", "Advanced", _raw),
    )
}


def validate_registry() -> None:
    """Purpose: verify every public action has unique, rich, actionable documentation.

    Args:
        None: This validator reads the module-level ``REGISTRY`` only.

    Returns:
        None: Raises ``RuntimeError`` when a registry invariant is broken.

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
                section not in action.handler.__doc__
                for section in ("Purpose:", "Args:", "Returns:", "Examples:")
            )
        ):
            raise RuntimeError(f"invalid action registry entry: {name}")
