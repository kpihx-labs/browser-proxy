"""Render the registry-driven, human-facing ``browser-proxy do`` documentation.

The implementation docstrings remain source-level implementation documentation. This module is the
single user-facing help renderer: it turns one action definition into concise usage, payload, and
three command-to-result examples without exposing handler internals such as ``DaemonContext``.
"""

import inspect
import json
import re
from collections.abc import Callable, Mapping
from typing import Any


EXAMPLE_PAYLOADS: dict[str, dict[str, Any]] = {
    "profile-list": {},
    "profile-start": {"profile": "default"},
    "profile-remove": {"profile": "test"},
    "window-list": {"profile": "default"},
    "window-create": {"profile": "default", "url": "https://example.com"},
    "window-close": {"profile": "default", "target_id": "<target-id>"},
    "tab-list": {"profile": "default"},
    "tab-create": {"profile": "default", "url": "https://example.com"},
    "tab-activate": {"profile": "default", "target_id": "<target-id>"},
    "page-list": {"profile": "default"},
    "page-get": {"profile": "default", "target_id": "<target-id>"},
    "workspace-list": {"profile": "default"},
    "group-list": {"profile": "default"},
    "group-create": {"profile": "default", "tab_ids": [1, 2], "title": "Research"},
    "group-update": {"profile": "default", "group_id": 1, "title": "Research"},
    "group-move": {"profile": "default", "group_id": 1, "window_id": 2},
    "bookmark-list": {"profile": "default"},
    "bookmark-create": {"profile": "default", "title": "Example", "url": "https://example.com"},
    "bookmark-remove": {"profile": "default", "id": "42"},
    "page-navigate": {
        "profile": "default",
        "target_id": "<target-id>",
        "url": "https://example.com",
    },
    "page-reload": {"profile": "default", "target_id": "<target-id>"},
    "page-back": {"profile": "default", "target_id": "<target-id>"},
    "page-forward": {"profile": "default", "target_id": "<target-id>"},
    "page-click": {"profile": "default", "target_id": "<target-id>", "selector": "#submit"},
    "page-hover": {"profile": "default", "target_id": "<target-id>", "selector": "#menu"},
    "page-type": {
        "profile": "default",
        "target_id": "<target-id>",
        "selector": "#query",
        "text": "Browser Proxy",
    },
    "page-fill-form": {
        "profile": "default",
        "target_id": "<target-id>",
        "fields": {"#name": "KpihX"},
    },
    "page-select-option": {
        "profile": "default",
        "target_id": "<target-id>",
        "selector": "#country",
        "value": "FR",
    },
    "page-scroll": {"profile": "default", "target_id": "<target-id>", "selector": "footer"},
    "page-evaluate": {
        "profile": "default",
        "target_id": "<target-id>",
        "expression": "document.title",
    },
    "page-snapshot": {"profile": "default", "target_id": "<target-id>"},
    "page-screenshot": {
        "profile": "default",
        "target_id": "<target-id>",
        "output": "/tmp/browser-proxy-shot.png",
    },
    "page-query": {"profile": "default", "target_id": "<target-id>", "selector": "a"},
    "page-console-list": {"profile": "default", "target_id": "<target-id>"},
    "page-network-list": {"profile": "default", "target_id": "<target-id>"},
    "page-dialog-policy": {"profile": "default", "target_id": "<target-id>", "action": "dismiss"},
    "page-set-download-behavior": {"profile": "default", "path": "/tmp/browser-proxy-downloads"},
    "cookie-list": {"profile": "default"},
    "cookie-set": {"profile": "default", "name": "theme", "value": "dark", "domain": "example.com"},
    "cookie-remove": {"profile": "default", "name": "theme", "domain": "example.com"},
    "storage-local-get": {"profile": "default", "target_id": "<target-id>", "key": "theme"},
    "storage-local-set": {
        "profile": "default",
        "target_id": "<target-id>",
        "key": "theme",
        "value": "dark",
    },
    "browser-ask-user": {"profile": "default", "question": "Continue?"},
    "browser-dismiss-overlays": {"profile": "default"},
    "browser-solve-captcha": {"profile": "default", "action": "detect"},
    "browser-set-date": {"profile": "default", "selector": "#date", "value": "2026-08-29"},
    "browser-set-combobox": {"profile": "default", "selector": "#country", "value": "France"},
    "browser-drop-file": {
        "profile": "default",
        "selector": "#upload",
        "filename": "note.txt",
        "content_base64": "SGVsbG8=",
        "mime_type": "text/plain",
    },
    "browser-get-new-tab": {"profile": "default", "timeout_seconds": 15},
    "raw": {"profile": "default", "method": "Target.getTargets", "params": {}},
}

EXAMPLE_RESULTS: dict[str, dict[str, Any]] = {
    "profile-list": {
        "profiles": [
            {
                "name": "default",
                "profile_dir": "/home/user/.local/share/browser-proxy/profiles/default",
                "state": "initialized",
                "cdp_port": 38049,
                "systemd_active": True,
                "cdp_reachable": True,
                "extension_connected": True,
            }
        ]
    },
    "profile-start": {"profile": "default", "cdp_port": 38049},
    "profile-remove": {
        "profile": "test",
        "removed": True,
        "was_active": False,
        "trashed_path": "/home/user/.local/share/browser-proxy/profiles/test",
    },
    "window-list": {
        "profile": "default",
        "windows": [
            {
                "window_id": 143985019,
                "bounds": {
                    "left": 0,
                    "top": 0,
                    "width": 1920,
                    "height": 1168,
                    "windowState": "normal",
                },
                "tabs": [{"id": "<target-id>", "type": "page"}],
            }
        ],
    },
    "window-create": {
        "profile": "default",
        "url": "https://example.com",
        "target_id": "<target-id>",
        "window_id": 143985019,
    },
    "window-close": {"profile": "default", "target_id": "<target-id>", "closed": True},
    "tab-list": {"profile": "default", "tabs": [{"id": "<target-id>", "type": "page"}]},
    "tab-create": {"profile": "default", "target_id": "<target-id>", "url": "https://example.com"},
    "tab-activate": {"profile": "default", "target_id": "<target-id>", "active": True},
    "page-list": {
        "profile": "default",
        "pages": [{"id": "<target-id>", "url": "https://example.com"}],
    },
    "page-get": {"profile": "default", "page": {"targetId": "<target-id>", "type": "page"}},
    "workspace-list": {
        "profile": "default",
        "heuristic": True,
        "authority": "none",
        "workspaces": [],
    },
    "group-list": {"groups": []},
    "group-create": {"group_id": 1, "title": "Research"},
    "group-update": {"id": 1, "title": "Research"},
    "group-move": {"group_id": 1, "window_id": 2},
    "bookmark-list": {"bookmarks": [{"id": "1", "title": "Favorites bar", "url": None}]},
    "bookmark-create": {"id": "42", "title": "Example", "url": "https://example.com"},
    "bookmark-remove": {"id": "42", "removed": True},
    "page-navigate": {
        "profile": "default",
        "target_id": "<target-id>",
        "url": "https://example.com",
        "ready_state": "complete",
    },
    "page-reload": {"profile": "default", "target_id": "<target-id>", "reloaded": True},
    "page-back": {"profile": "default", "target_id": "<target-id>", "navigated": True},
    "page-forward": {"profile": "default", "target_id": "<target-id>", "navigated": True},
    "page-click": {
        "profile": "default",
        "target_id": "<target-id>",
        "selector": "#submit",
        "clicked": True,
    },
    "page-hover": {
        "profile": "default",
        "target_id": "<target-id>",
        "selector": "#menu",
        "hovered": True,
    },
    "page-type": {
        "profile": "default",
        "target_id": "<target-id>",
        "selector": "#query",
        "typed": True,
    },
    "page-fill-form": {"profile": "default", "target_id": "<target-id>", "filled": 1},
    "page-select-option": {
        "profile": "default",
        "target_id": "<target-id>",
        "selector": "#country",
        "value": "FR",
        "selected": True,
    },
    "page-scroll": {"profile": "default", "target_id": "<target-id>", "scrolled": True},
    "page-evaluate": {"profile": "default", "target_id": "<target-id>", "result": "Example"},
    "page-snapshot": {"profile": "default", "target_id": "<target-id>", "nodes": []},
    "page-screenshot": {
        "profile": "default",
        "target_id": "<target-id>",
        "path": "/tmp/browser-proxy-shot.png",
    },
    "page-query": {
        "profile": "default",
        "target_id": "<target-id>",
        "selector": "a",
        "matches": [],
    },
    "page-console-list": {"profile": "default", "target_id": "<target-id>", "messages": []},
    "page-network-list": {"profile": "default", "target_id": "<target-id>", "requests": []},
    "page-dialog-policy": {"profile": "default", "target_id": "<target-id>", "policy": "dismiss"},
    "page-set-download-behavior": {
        "profile": "default",
        "path": "/tmp/browser-proxy-downloads",
        "configured": True,
    },
    "cookie-list": {"profile": "default", "cookies": []},
    "cookie-set": {"profile": "default", "name": "theme", "domain": "example.com", "set": True},
    "cookie-remove": {
        "profile": "default",
        "name": "theme",
        "domain": "example.com",
        "removed": True,
    },
    "storage-local-get": {"profile": "default", "target_id": "<target-id>", "value": "dark"},
    "storage-local-set": {
        "profile": "default",
        "target_id": "<target-id>",
        "key": "theme",
        "set": True,
    },
    "browser-ask-user": {"answer": "yes"},
    "browser-dismiss-overlays": {"dismissed": 1},
    "browser-solve-captcha": {"detected": False, "clicked": False},
    "browser-set-date": {"applied": True},
    "browser-set-combobox": {"matched": True},
    "browser-drop-file": {"dropped": True},
    "browser-get-new-tab": {"tab_id": 12, "url": "https://example.com"},
    "raw": {"profile": "default", "method": "Target.getTargets", "result": {"targetInfos": []}},
}


FIELD_NOTES: dict[str, str] = {
    "profile": "Managed Edge profile name. Defaults to `default` when omitted.",
    "target_id": "CDP page target ID returned by `page-list`, `window-list`, or `tab-list`.",
    "url": "Absolute page URL.",
    "selector": "CSS selector resolved inside the selected page.",
    "text": "Text inserted into the focused element.",
    "fields": "Object mapping CSS selectors to string values.",
    "value": "Value written or selected by the action.",
    "expression": "JavaScript expression evaluated with the page's privileges.",
    "wait_seconds": "Maximum readiness wait in seconds. Defaults to `10`.",
    "ignore_cache": "Whether reload bypasses the browser cache. Defaults to `false`.",
    "clear": "Whether existing input text is cleared before typing. Defaults to `false`.",
    "x": "Horizontal viewport coordinate. Defaults to `0`.",
    "y": "Vertical viewport coordinate. Defaults to `0`.",
    "format": "Screenshot format: `png` (default) or `jpeg`.",
    "output": "Local screenshot output path; returns base64 when omitted.",
    "action": "Requested operation for this action.",
    "prompt_text": "Text returned by an accepted JavaScript prompt.",
    "path": "Local directory used for downloaded files.",
    "name": "Cookie name.",
    "domain": "Cookie domain.",
    "secure": "Whether the cookie requires HTTPS. Defaults to `true`.",
    "http_only": "Whether JavaScript cannot read the cookie. Defaults to `false`.",
    "key": "localStorage key.",
    "tab_ids": "Real numeric Edge tab IDs to group.",
    "title": "Human-visible tab-group or bookmark title.",
    "color": "Edge tab-group color.",
    "group_id": "Real numeric Edge tab-group ID.",
    "window_id": "Real numeric destination Edge window ID.",
    "id": "Bookmark ID returned by `bookmark-list`.",
    "question": "Question rendered in the visible extension overlay.",
    "input_type": "`text` (default) or `password` input in the overlay.",
    "cells": "Optional CAPTCHA grid cell indexes; grid solving is not implemented.",
    "filename": "Name assigned to the inline file.",
    "content_base64": "Base64-encoded file bytes supplied inline; no extension filesystem access.",
    "mime_type": "File MIME type. Defaults to `application/octet-stream`.",
    "timeout_seconds": "Maximum wait for the next created tab. Defaults to `15`.",
    "method": "Browser-level Chrome DevTools Protocol method.",
    "params": "Object-valued Chrome DevTools Protocol parameters.",
}

OPTIONAL_FIELDS: dict[str, tuple[str, ...]] = {
    "page-navigate": ("wait_seconds",),
    "page-reload": ("ignore_cache", "wait_seconds"),
    "page-type": ("clear",),
    "page-scroll": ("selector", "x", "y"),
    "page-evaluate": ("await_promise",),
    "page-screenshot": ("format", "output"),
    "page-console-list": ("clear",),
    "page-dialog-policy": ("prompt_text",),
    "cookie-set": ("path", "secure", "http_only"),
    "storage-local-get": ("key",),
    "window-create": ("items",),
    "group-create": ("color",),
    "group-update": ("title", "color", "collapsed"),
    "browser-ask-user": ("input_type",),
    "browser-solve-captcha": ("cells",),
    "browser-drop-file": ("mime_type",),
    "browser-get-new-tab": ("timeout_seconds",),
}

ACTION_FIELD_NOTES: dict[tuple[str, str], str] = {
    (
        "window-create",
        "items",
    ): (
        "Ordered layout to build inside the new window in one call: a list of "
        '{"type":"tab","url":"..."} or {"type":"group","title":"...","color":"...",'
        '"tabs":["url1","url2",...]} objects, created in that exact order.'
    ),
    ("page-console-list", "clear"): "Whether the captured console buffer is cleared after reading.",
    ("page-evaluate", "await_promise"): "Whether an expression returning a Promise is awaited.",
    ("cookie-set", "path"): "Cookie path. Defaults to `/`.",
    ("group-update", "collapsed"): "Whether the Edge tab group is collapsed.",
    (
        "page-dialog-policy",
        "action",
    ): "`accept` or `dismiss` for alert, confirm, and prompt dialogs.",
    (
        "browser-solve-captcha",
        "action",
    ): "`detect`, `click_checkbox`, or `click_grid` (grid solving is not implemented).",
}


def _field_note(action: str, field: str) -> str:
    """Purpose: return an action-specific parameter explanation before falling back to the shared one.

    Args:
        action (str): Flat public action name.
        field (str): JSON payload field name.

    Returns:
        str: Exact action-specific or reusable public parameter explanation.

    Examples:
        >>> _field_note('cookie-set', 'path')
        'Cookie path. Defaults to `/`.'
        >>> 'profile' in _field_note('profile-start', 'profile')
        True
    """

    return ACTION_FIELD_NOTES.get(
        (action, field), FIELD_NOTES.get(field, "Action-specific JSON field.")
    )


def _purpose(func: Callable[..., Any]) -> str:
    """Purpose: extract the first implementation purpose sentence before public docs replace it.

    Args:
        func (Callable[..., Any]): Action handler carrying a structured implementation docstring.

    Returns:
        str: One concise action description without internal Args/Returns sections.

    Examples:
        >>> _purpose(lambda: None)
        'No description available.'
        >>> isinstance(_purpose(_purpose), str)
        True
    """

    doc = (func.__doc__ or "").strip()
    if not doc:
        return "No description available."
    first = doc.splitlines()[0].removeprefix("Purpose:").strip()
    return first or "No description available."


def _type_name(value: Any) -> str:
    """Purpose: map one representative JSON value to its concise public parameter type.

    Args:
        value (Any): Representative JSON value from an action's canonical example payload.

    Returns:
        str: Public JSON type label such as ``str``, ``int``, ``bool``, or ``dict``.

    Examples:
        >>> _type_name("default")
        'str'
        >>> _type_name({"key": "value"})
        'dict'
    """

    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "str"


def public_docstring(name: str, func: Callable[..., Any]) -> str:
    """Purpose: build the Tick-proxy-style public docstring for one registered browser action.

    Args:
        name (str): Flat public action name with canonical payload/result examples.
        func (Callable[..., Any]): Action handler carrying the implementation purpose.

    Returns:
        str: Description, Parameters, and at least three command-to-result examples.

    Examples:
        >>> 'Parameters:' in public_docstring('profile-list', _purpose)
        True
        >>> 'Examples:' in public_docstring('bookmark-list', _purpose)
        True
    """

    payload = EXAMPLE_PAYLOADS[name]
    optional = set(OPTIONAL_FIELDS.get(name, ()))
    parameter_lines = [
        f"    - {field} ({_type_name(value)}{'|null' if field in optional else ''}): "
        f"{_field_note(name, field)}"
        for field, value in payload.items()
    ]
    for field in OPTIONAL_FIELDS.get(name, ()):
        if field not in payload:
            parameter_lines.append(f"    - {field} (optional): {_field_note(name, field)}")
    if not parameter_lines:
        parameter_lines.append("    - No payload fields. This action accepts `{}` or no payload.")
    encoded = json.dumps(payload, separators=(",", ":"))
    result = json.dumps(EXAMPLE_RESULTS[name], separators=(",", ":"))
    file_path = f"/tmp/browser-proxy-payloads/{name}.json"
    output_path = f"/tmp/browser-proxy-results/{name}.json"
    return (
        f"{_purpose(func)[:1].upper()}{_purpose(func)[1:]}\n\n"
        "Parameters:\n"
        + "\n".join(parameter_lines)
        + "\n\nExamples:\n"
        + f"    - Inline JSON:\n        `browser-proxy do {name} '{encoded}'`\n        → {result}\n\n"
        + f"    - Same payload from a JSON file:\n        `browser-proxy do {name} {file_path}`\n        → {result}\n\n"
        + f"    - Persist the complete envelope at a chosen path:\n        `browser-proxy do {name} '{encoded}' -o {output_path}`\n        → {result}\n\n"
        "Implementation:\n"
        "    Args:\n"
        "        payload (dict): Validated action JSON supplied by the CLI.\n"
        "        context (DaemonContext): Daemon state providing CDP, policy, and extension access.\n"
        "    Returns:\n"
        "        dict: Action-specific data wrapped by the CLI in the standard meta/data envelope.\n"
    )


def get_compact_help(func: Callable[..., Any]) -> str:
    """Purpose: return a public action docstring without its Examples or implementation appendix.

    Args:
        func (Callable[..., Any]): Handler whose runtime docstring is public action documentation.

    Returns:
        str: Description and Parameters section for the grouped ``do --help`` catalog.

    Examples:
        >>> 'Parameters:' in get_compact_help(_purpose)
        True
        >>> 'Examples:' not in get_compact_help(_purpose)
        True
    """

    doc = inspect.getdoc(func) or ""
    return re.split(r"(?m)^\s*Examples:\s*$", doc, maxsplit=1)[0].strip()


def get_full_help(func: Callable[..., Any]) -> str:
    """Purpose: render public examples as real meta/data envelopes, matching tick-proxy's doc renderer.

    Args:
        func (Callable[..., Any]): Handler whose runtime docstring is public action documentation.

    Returns:
        str: Public description, Parameters, and Examples with every JSON arrow wrapped in an envelope.

    Examples:
        >>> '"meta"' in get_full_help(_purpose)
        True
        >>> 'Implementation:' not in get_full_help(_purpose)
        True
    """

    doc = (inspect.getdoc(func) or "").split("\nImplementation:\n", maxsplit=1)[0]
    lines: list[str] = []
    for line in doc.splitlines():
        match = re.match(r"^(\s*→\s*)(.*)$", line)
        if not match:
            lines.append(line)
            continue
        try:
            rendered = json.dumps(
                {
                    "meta": {"status": "ok", "comment": "", "edited": False},
                    "data": json.loads(match.group(2)),
                },
                indent=2,
            )
        except json.JSONDecodeError:
            lines.append(line)
        else:
            lines.append(f"{match.group(1)}{rendered}")
    return "\n".join(lines)


def attach_public_docstrings(registry: Mapping[str, Any]) -> None:
    """Purpose: replace registered handlers' runtime docs with one Tick-style public documentation source.

    Args:
        registry (Mapping[str, Any]): Browser action registry containing ``handler`` objects.

    Returns:
        None: Assigns each action's public docstring before CLI command registration.

    Examples:
        >>> callable(attach_public_docstrings)
        True
        >>> len(EXAMPLE_PAYLOADS) == len(EXAMPLE_RESULTS)
        True
    """

    for name, action in registry.items():
        action.handler.__doc__ = public_docstring(name, action.handler)
