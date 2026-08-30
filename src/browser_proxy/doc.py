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
    "window-create": {
        "profile": "default",
        "layout": [{"type": "tab", "url": "https://example.com"}],
    },
    "window-close": {"profile": "default", "target_ids": ["<target-id>"]},
    "window-sync": {
        "profile": "default",
        "window_id": 143985019,
        "state": "maximized",
        "layout": [{"type": "tab", "tab_id": 12}],
    },
    "window-save": {
        "profile": "default",
        "saves": [{"window_id": 143985019, "name": "Research"}],
    },
    "window-restore": {"profile": "default", "names": ["Research"]},
    "window-saved-list": {"profile": "default"},
    "window-saved-remove": {"profile": "default", "names": ["Research"]},
    "tab-list": {"profile": "default"},
    "tab-get": {"profile": "default", "target_id": "<target-id>"},
    "tab-create": {
        "profile": "default",
        "url": "https://example.com",
        "window_id": 143985019,
        "group_id": 1,
    },
    "tab-activate": {"profile": "default", "target_id": "<target-id>"},
    "tab-update": {
        "profile": "default",
        "tab_id": 12,
        "url": "https://example.com",
        "group_id": 1,
        "index": 0,
    },
    "group-list": {"profile": "default"},
    "group-create": {"profile": "default", "tab_ids": [1, 2], "title": "Research"},
    "group-update": {"profile": "default", "group_id": 1, "title": "Research"},
    "group-move": {"profile": "default", "group_id": 1, "window_id": 2},
    "group-add-tabs": {"profile": "default", "group_id": 1, "tab_ids": [3, 4]},
    "group-remove-tabs": {"profile": "default", "tab_ids": [3, 4]},
    "bookmark-list": {"profile": "default", "depth": 1},
    "bookmark-get": {"profile": "default", "id": "42"},
    "bookmark-create": {
        "profile": "default",
        "items": [
            {"type": "folder", "title": "2026", "ref": "y26"},
            {
                "type": "bookmark",
                "title": "Example",
                "url": "https://example.com",
                "parent_ref": "y26",
            },
        ],
    },
    "bookmark-remove": {"profile": "default", "ids": ["7", "42"]},
    "bookmark-update": {
        "profile": "default",
        "items": [{"id": "42", "title": "Renamed", "parent_id": "1"}],
    },
    "extension-list": {"profile": "default"},
    "extension-get": {"profile": "default", "id": "<extension-id>"},
    "extension-enable": {"profile": "default", "ids": ["<extension-id>"]},
    "extension-disable": {"profile": "default", "ids": ["<extension-id>"]},
    "extension-reload": {"profile": "default"},
    "extension-search": {"profile": "default", "store": "edge", "query": "dark reader"},
    "clipboard-read": {"profile": "default"},
    "clipboard-write": {"profile": "default", "text": "hello"},
    "page-navigate": {
        "profile": "default",
        "target_id": "<target-id>",
        "url": "https://example.com",
    },
    "page-reload": {"profile": "default", "target_id": "<target-id>"},
    "page-back": {"profile": "default", "target_id": "<target-id>"},
    "page-forward": {"profile": "default", "target_id": "<target-id>"},
    "page-click": {"profile": "default", "target_id": "<target-id>", "selector": "#submit"},
    "page-click-eval": {"profile": "default", "target_id": "<target-id>", "selector": "#submit"},
    "page-click-coordinates": {
        "profile": "default",
        "target_id": "<target-id>",
        "x": 91.5,
        "y": 312.3,
        "button": "left",
        "click_count": 2,
    },
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
    "storage-local-remove": {
        "profile": "default",
        "target_id": "<target-id>",
        "keys": ["theme"],
    },
    "storage-local-clear": {"profile": "default", "target_id": "<target-id>"},
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
    "raw": {
        "profile": "default",
        "protocol": "cdp-browser",
        "method": "Target.getTargets",
        "params": {},
    },
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
                "chrome_layout": {
                    "tabs": [
                        {
                            "chrome_tab_id": 12,
                            "index": 0,
                            "url": "https://example.com",
                            "title": "Example",
                            "group_id": None,
                            "active": True,
                            "pinned": False,
                            "target_id": "<target-id>",
                        }
                    ],
                    "groups": {},
                    "order": [{"kind": "tab", "chrome_tab_id": 12}],
                },
            }
        ],
    },
    "window-create": {
        "profile": "default",
        "window_id": 143985019,
        "layout": [{"type": "tab", "url": "https://example.com", "target_id": "<target-id>"}],
    },
    "window-close": {"profile": "default", "target_ids": ["<target-id>"], "closed": True},
    "window-sync": {
        "window_id": 143985019,
        "state": "maximized",
        "focused": True,
        "layout": [{"type": "tab", "tab_id": 12}],
    },
    "window-save": {
        "profile": "default",
        "saved": [{"name": "Research", "window_id": 143985019, "tab_count": 3}],
    },
    "window-restore": {
        "profile": "default",
        "restored": [
            {
                "name": "Research",
                "window_id": 143985340,
                "layout": [
                    {"type": "tab", "url": "https://example.com", "target_id": "<target-id>"}
                ],
            }
        ],
    },
    "window-saved-list": {
        "profile": "default",
        "windows": [
            {
                "name": "Research",
                "saved_at": "2026-08-30T11:40:00+00:00",
                "bounds": {"left": 0, "top": 0, "width": 1928, "height": 912},
                "tab_count": 3,
                "layout": [{"type": "tab", "url": "https://example.com"}],
            }
        ],
    },
    "window-saved-remove": {"profile": "default", "removed": ["Research"]},
    "tab-list": {
        "profile": "default",
        "tabs": [
            {
                "targetId": "<target-id>",
                "type": "page",
                "window_id": 143985019,
                "group_id": None,
                "group_title": None,
            }
        ],
    },
    "tab-get": {
        "profile": "default",
        "tab": {
            "targetId": "<target-id>",
            "type": "page",
            "title": "Example Domain",
            "url": "https://example.com",
            "window_id": 143985019,
            "group_id": 1,
            "group_title": "Research",
        },
    },
    "tab-create": {
        "profile": "default",
        "target_id": "<target-id>",
        "url": "https://example.com",
        "ready_state": "complete",
        "tab_id": 12,
        "window_id": 143985019,
        "group_id": 1,
    },
    "tab-activate": {"profile": "default", "target_id": "<target-id>", "active": True},
    "tab-update": {
        "tab_id": 12,
        "url": "https://example.com",
        "index": 0,
        "window_id": 143985019,
        "group_id": 1,
    },
    "group-list": {
        "groups": [
            {
                "id": 1,
                "window_id": 143985019,
                "title": "Research",
                "color": "blue",
                "collapsed": False,
                "tabs": [{"id": 12, "url": "https://example.com", "title": "Example"}],
            }
        ]
    },
    "group-create": {"group_id": 1, "title": "Research"},
    "group-update": {"id": 1, "title": "Research"},
    "group-move": {"group_id": 1, "window_id": 2},
    "group-add-tabs": {"group_id": 1, "tab_ids": [3, 4]},
    "group-remove-tabs": {"tab_ids": [3, 4], "ungrouped": True},
    "bookmark-list": {
        "depth": 1,
        "roots": [
            {
                "id": "1",
                "title": "Bookmarks bar",
                "type": "folder",
                "url": None,
                "parent_id": "0",
                "index": 0,
                "children": [
                    {
                        "id": "42",
                        "title": "Example",
                        "type": "bookmark",
                        "url": "https://example.com",
                        "parent_id": "1",
                        "index": 0,
                    }
                ],
            }
        ],
    },
    "bookmark-get": {
        "id": "42",
        "title": "Example",
        "type": "bookmark",
        "url": "https://example.com",
        "parent_id": "1",
        "parent_title": "Bookmarks bar",
        "index": 0,
        "date_added": 1700000000000,
        "date_last_used": 1700000500000,
    },
    "bookmark-create": {
        "created": [
            {
                "ref": "y26",
                "id": "101",
                "type": "folder",
                "title": "2026",
                "url": None,
                "parent_id": "1",
                "index": 0,
            },
            {
                "ref": None,
                "id": "102",
                "type": "bookmark",
                "title": "Example",
                "url": "https://example.com",
                "parent_id": "101",
                "index": 0,
            },
        ]
    },
    "bookmark-remove": {
        "removed": [
            {"id": "7", "type": "folder", "title": "Old project", "url": None},
            {"id": "42", "type": "bookmark", "title": "Example", "url": "https://example.com"},
        ]
    },
    "bookmark-update": {
        "updated": [
            {
                "id": "42",
                "title": "Renamed",
                "url": "https://example.com",
                "parent_id": "1",
                "index": 0,
            }
        ]
    },
    "extension-list": {
        "extensions": [
            {
                "id": "<extension-id>",
                "name": "Browser Proxy Bridge for Microsoft Edge",
                "short_name": "Browser Proxy Bridge",
                "version": "0.2.0",
                "description": "A local, approval-gated bridge between Microsoft Edge and browser-proxyd.",
                "type": "extension",
                "enabled": True,
                "may_disable": True,
                "install_type": "development",
                "offline_enabled": False,
                "homepage_url": None,
                "update_url": None,
                "options_url": "chrome-extension://<extension-id>/options.html",
                "permissions": [
                    "bookmarks",
                    "tabs",
                    "tabGroups",
                    "storage",
                    "alarms",
                    "management",
                ],
                "host_permissions": [],
                "permission_warnings": [
                    "Read and change your bookmarks",
                    "Manage your apps, extensions, and themes",
                ],
                "icons": [],
            }
        ]
    },
    "extension-get": {
        "id": "<extension-id>",
        "name": "Browser Proxy Bridge for Microsoft Edge",
        "short_name": "Browser Proxy Bridge",
        "version": "0.2.0",
        "description": "A local, approval-gated bridge between Microsoft Edge and browser-proxyd.",
        "type": "extension",
        "enabled": True,
        "may_disable": True,
        "install_type": "development",
        "offline_enabled": False,
        "homepage_url": None,
        "update_url": None,
        "options_url": "chrome-extension://<extension-id>/options.html",
        "permissions": ["bookmarks", "tabs", "tabGroups", "storage", "alarms", "management"],
        "host_permissions": [],
        "permission_warnings": [
            "Read and change your bookmarks",
            "Manage your apps, extensions, and themes",
        ],
        "icons": [],
    },
    "extension-enable": {
        "updated": [{"id": "<extension-id>", "name": "Some Extension", "enabled": True}]
    },
    "extension-disable": {
        "updated": [{"id": "<extension-id>", "name": "Some Extension", "enabled": False}]
    },
    "extension-reload": {
        "reloading": True,
        "id": "<extension-id>",
        "name": "Browser Proxy Bridge for Microsoft Edge",
        "version": "0.2.0",
    },
    "extension-search": {
        "profile": "default",
        "store": "edge",
        "query": "dark reader",
        "results": [
            {
                "id": "ifoakfbpdcdoeenechcleahebpibofpc",
                "slug": "dark-reader",
                "text_block": ["Dark Reader", "Extension", "(1.4K) · alexanderby", "Get"],
            }
        ],
    },
    "clipboard-read": {
        "text": "Copied text from OS clipboard",
    },
    "clipboard-write": {
        "written": True,
    },
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
    "page-click-eval": {
        "profile": "default",
        "target_id": "<target-id>",
        "selector": "#submit",
        "clicked": True,
    },
    "page-click-coordinates": {
        "profile": "default",
        "target_id": "<target-id>",
        "x": 91.5,
        "y": 312.3,
        "button": "left",
        "click_count": 2,
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
    "storage-local-remove": {
        "profile": "default",
        "target_id": "<target-id>",
        "keys": ["theme"],
        "removed": True,
    },
    "storage-local-clear": {"profile": "default", "target_id": "<target-id>", "cleared": True},
    "browser-ask-user": {"answer": "yes"},
    "browser-dismiss-overlays": {"dismissed": 1},
    "browser-solve-captcha": {"detected": False, "clicked": False},
    "browser-set-date": {"applied": True},
    "browser-set-combobox": {"matched": True},
    "browser-drop-file": {"dropped": True},
    "browser-get-new-tab": {"tab_id": 12, "url": "https://example.com"},
    "raw": {
        "profile": "default",
        "protocol": "cdp-browser",
        "method": "Target.getTargets",
        "result": {"targetInfos": []},
    },
}


FIELD_NOTES: dict[str, str] = {
    "profile": "Managed Edge profile name. Defaults to `default` when omitted.",
    "target_id": "CDP page target ID returned by `window-list`/`tab-list`/`tab-get`.",
    "target_ids": (
        "One or more CDP page target IDs (returned by `window-list`/`tab-list`/`tab-get`) to "
        "close in this SAME call — never one separate call per target."
    ),
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
    "keys": "Non-empty list of localStorage keys to remove in ONE call, never one call per key.",
    "tab_ids": "Real numeric Edge tab IDs to group.",
    "title": "Human-visible tab-group or bookmark title.",
    "color": "Edge tab-group color.",
    "group_id": "Real numeric Edge tab-group ID.",
    "window_id": "Real numeric destination Edge window ID.",
    "question": "Question rendered in the visible extension overlay.",
    "input_type": "`text` (default) or `password` input in the overlay.",
    "cells": "Optional CAPTCHA grid cell indexes; grid solving is not implemented.",
    "filename": "Name assigned to the inline file.",
    "content_base64": "Base64-encoded file bytes supplied inline; no extension filesystem access.",
    "mime_type": "File MIME type. Defaults to `application/octet-stream`.",
    "timeout_seconds": "Maximum wait for the next created tab. Defaults to `15`.",
    "method": "Browser-level Chrome DevTools Protocol method — or, for `ext`, a dotted chrome.* path (`bookmarks.getTree`); for `cdp-page`, a page-domain method (`Runtime.evaluate`).",
    "params": "Object-valued protocol parameters (or, for `ext`, an ARRAY of positional arguments).",
    "protocol": "Protocol family: `cdp-browser` (default), `cdp-page`, or `ext`.",
    "calls": "Ordered list of `[method, params]` pairs for `cdp-page` — executed sequentially within ONE attached page session.",
    "tab_id": "Real numeric chrome.tabs.Tab ID (never a CDP target_id) to update/reposition.",
    "index": "Absolute destination position: `0` is first, `-1` is last.",
    "before_tab_id": "Move immediately before this real tab ID; its index is resolved server-side.",
    "after_tab_id": "Move immediately after this real tab ID; its index is resolved server-side.",
    "layout": (
        'Ordered list to reorganize a whole window in one call: {"type":"tab","tab_id":N} or '
        '{"type":"group","group_id":N (optional),"title":str,"color":str,"tab_ids":[N,...]}.'
    ),
    "new_window": "Whether to open a genuinely new Edge window instead of the current one.",
    "bounds": 'Window bounds, any subset of {"left","top","width","height"} (real pixels).',
    "state": '`"normal"`, `"maximized"`, `"minimized"`, `"fullscreen"`, or `"locked-fullscreen"`.',
    "focused": "Whether the window should be given input focus.",
    "saves": 'Non-empty list of {"window_id":N,"name":str} — several windows saved in ONE call.',
    "names": "Non-empty list of saved window names — several restored/removed in ONE call.",
    "ids": (
        "Non-empty list of real bookmark/folder ids — several removed in ONE call, mixing "
        "folders (removed WITH their subtree) and leaf bookmarks freely."
    ),
    "depth": (
        "Non-negative integer capping how many levels below the top-level roots are "
        "included; omitted or `null` returns the full tree, unbounded."
    ),
    "root_id": (
        "Existing real bookmark folder id scoping the whole call to just that ONE subfolder "
        "instead of the top-level roots; `depth` then counts from THAT folder."
    ),
    "items": "Ordered batch list of per-item objects — several created/updated in ONE call.",
    "store": '`"edge"` (Microsoft Edge Add-ons) or `"chrome"` (Chrome Web Store — Edge can install from both).',
    "query": "Free-text search query, exactly as typed into the store's own search box.",
    "limit": "Maximum number of search results returned. Defaults to `20`.",
}

OPTIONAL_FIELDS: dict[str, tuple[str, ...]] = {
    "page-navigate": ("wait_seconds",),
    "page-reload": ("ignore_cache", "wait_seconds"),
    "page-type": ("clear",),
    "page-click-coordinates": ("button", "click_count"),
    "page-scroll": ("selector", "x", "y"),
    "page-evaluate": ("await_promise",),
    "page-screenshot": ("format", "output"),
    "page-console-list": ("clear",),
    "page-dialog-policy": ("prompt_text",),
    "cookie-set": ("path", "secure", "http_only"),
    "storage-local-get": ("key",),
    "tab-create": (
        "new_window",
        "window_id",
        "group_id",
        "index",
        "before_tab_id",
        "after_tab_id",
        "wait_seconds",
    ),
    "tab-update": ("url", "window_id", "group_id", "index", "before_tab_id", "after_tab_id"),
    "window-sync": ("bounds", "state", "focused", "layout"),
    "group-create": ("color",),
    "group-update": ("title", "color", "collapsed"),
    "browser-ask-user": ("input_type",),
    "browser-solve-captcha": ("cells",),
    "browser-drop-file": ("mime_type",),
    "browser-get-new-tab": ("timeout_seconds",),
    "bookmark-list": ("depth", "root_id"),
    "extension-search": ("limit",),
}

ACTION_FIELD_NOTES: dict[tuple[str, str], str] = {
    (
        "page-console-list",
        "clear",
    ): "Whether the captured console buffer is cleared after reading. The capture is PERSISTENT — injected via `Page.addScriptToEvaluateOnNewDocument` so it survives reloads and captures the page's own boot logs; the daemon holds one long-lived attached session per (profile, target_id) (`DaemonContext.console_capture`).",
    ("page-evaluate", "await_promise"): "Whether an expression returning a Promise is awaited.",
    (
        "page-click-coordinates",
        "button",
    ): 'Mouse button: `"left"` (default), `"middle"`, `"right"`, `"back"`, or `"forward"`.',
    (
        "page-click-coordinates",
        "click_count",
    ): "Number of press/release pairs: `1` (default) for a plain click, `2` for a double-click, etc.",
    ("cookie-set", "path"): "Cookie path. Defaults to `/`.",
    ("group-update", "collapsed"): "Whether the Edge tab group is collapsed.",
    (
        "tab-create",
        "window_id",
    ): "Open directly in this EXISTING window instead of a new one — mutually exclusive with `new_window`.",
    (
        "tab-create",
        "group_id",
    ): "Add the newly created tab into this EXISTING group/folder the instant it is created.",
    (
        "tab-update",
        "window_id",
    ): "Optional destination window (real numeric ID) to also move the tab across windows.",
    (
        "tab-update",
        "group_id",
    ): "Real numeric Edge tab-group ID to move this tab into, or explicit `null` to remove it from its group.",
    (
        "group-remove-tabs",
        "tab_ids",
    ): "Real numeric Edge tab IDs to remove from their group (never closed).",
    (
        "page-dialog-policy",
        "action",
    ): "`accept` or `dismiss` for alert, confirm, and prompt dialogs.",
    (
        "browser-solve-captcha",
        "action",
    ): "`detect`, `click_checkbox`, or `click_grid` (grid solving is not implemented).",
    ("bookmark-create", "items"): (
        'Ordered batch list: {"type":"folder"|"bookmark","title","url"? (bookmark only),'
        '"parent_id"? (existing folder),"parent_ref"? (an earlier folder item\'s ref, same '
        'batch — mutually exclusive with parent_id),"ref"? (name later items may target),'
        '"index"?} — several bookmarks/folders created in ONE call, never one call per item.'
    ),
    ("bookmark-update", "items"): (
        'Ordered batch list: {"id","title"?,"url"? (bookmark only),"parent_id"?,"index"?} — at '
        "least one field beyond id per item — several updates applied in ONE call, never one "
        "call per item."
    ),
    ("bookmark-get", "id"): "Real bookmark or folder id to read ALL available information about.",
    (
        "extension-get",
        "id",
    ): "Real chrome.management extension id to read ALL available detail about.",
    ("extension-enable", "ids"): "Non-empty list of real extension ids to enable in ONE call.",
    ("extension-disable", "ids"): "Non-empty list of real extension ids to disable in ONE call.",
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
        >>> _type_name(91.5)
        'number'
    """

    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "number"
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
