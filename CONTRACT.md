# browser-proxy — Architecture Contract

> **Status:** 🟢 ACTIVE — v0.5.0. Edge-only. Single binary, namespaced CLI: `do`/`admin`. 50 registered
> `do` actions. Direct CDP is the default transport; the paired Edge extension is a privileged
> fallback + human-in-the-loop surface, multiplexed per profile (see `## Extension bridge
> identity`). Every managed Edge instance is always visible (no headless mode exists — see
> `## Edge lifecycle`). Profile lifecycle (state, systemd, CDP) is computed by ONE canonical
> function, `profile_state.describe_edge_profile()` — never duplicated per caller (see
> `## Profile lifecycle management`).

---

## Mantras

- **0 Hardcoding · 100% Flexibility:** No hardcoded ports and no hardcoded profile inventory. CDP
  ports are derived deterministically from the profile name (`edge_cdp_port()`); the disk profile
  root is the sole source of truth for profile identity.
- **0 Magic · 100% Transparency:** Every command in this document states, in the same table row,
  the exact CDP method(s) or extension `kind` it issues — nothing "just happens" without a named
  underlying call. Hidden/internal entrypoints are enumerated explicitly, never left implicit.
  Every managed Edge window is always real and visible — never headless, never hidden from KπX.
- **0 Trust · 100% Control:** Mutating/destructive/sensitive actions are gated by declared policy
  decorators (`@require_approval`/`@require_preflight`/`@require_verification`) enforced centrally
  in `Daemon.dispatch()`, never left to individual handlers to self-police.

---

## Command Structure — Single Binary, Namespaced CLI

**ONE binary**, two public command groups, plus two internal-only entrypoints (see
`## Internal entrypoints`, never part of this public contract):

```
browser-proxy do <action> ['<one-json-object>'|payload.json] [-o FILE] [-f json|table]   # flat actions, JSON payload
browser-proxy admin <command>                                              # lifecycle/health, ALWAYS JSON
```

### `browser-proxy admin` — lifecycle & health (ALWAYS JSON, no `--format`)

| Command | Role | Backend — what actually runs | HITL |
|---|---|---|---|
| `admin status` | Persistent profile/socket/extension state | RPC `admin.status` → `{profiles, socket: str(socket_path()), extension_connected: bridge.connected, extension_connected_profiles: [...]}`; profiles are freshly discovered from disk via `profile_state.describe_edge_profile()`, each carrying `state`, `systemd_active`, `cdp_reachable`, and `extension_connected` (see `## Profile lifecycle management`) | ❌ |
| `admin install` | Register the daemon as a user service | `systemctl --user link` + `enable` on `systemd/browser-proxy.service` | ❌ |
| `admin start` | Start the daemon, verify with a ping | `systemctl --user start browser-proxy.service` then RPC `ping` | ❌ |
| `admin stop` | Graceful daemon shutdown | `systemctl --user stop browser-proxy.service` | ❌ |
| `admin doctor` | Redacted health snapshot | RPC `ping` + `shutil.which("microsoft-edge")` + `ExtensionBridge()._token() != ""` — no secret values ever included | ❌ |
| `admin extension pair` | Store the extension-generated pairing secret | hidden terminal prompt (`typer.prompt(..., hide_input=True)`) → `ExtensionBridge().pair(secret)` → mode-0600 local file write; secret never prints, enters shell history, logs, or chat | ❌ |
| `admin edge install` | Register the Edge unit template | `systemctl --user link` on `systemd/browser-proxy-edge@.service` (once per machine, not per profile) | ❌ |
| `admin edge start <profile>` | Materialize then start one profile's Edge instance | `materialize_edge_profile(profile)` then `systemctl --user start browser-proxy-edge@<profile>.service` — **always opens a real, visible window** | ❌ |
| `admin edge stop <profile>` | Stop one profile's Edge instance | `systemctl --user stop browser-proxy-edge@<profile>.service` | ❌ |
| `admin edge status <profile>` | Live profile health, all 4 axes | `profile_state.describe_edge_profile(profile)` (disk `state` + `systemctl is-active` + a real `CdpBrowser(port).call("Browser.getVersion", {})`, unconditionally — never gated on systemd's reported state) **plus** a best-effort daemon RPC for `extension_connected` (`null` if the daemon is genuinely unreachable; the other 3 axes never depend on it) | ❌ |

### `browser-proxy do` — actions (JSON default, `-f table` for display only)

**Meta options (only for `do`):** `-o/--output-file <path>` (write full envelope), `-f/--format json|table`, `--help/-h` (user-facing action guide). Payload is one inline JSON object, a path to a JSON file containing one object, or omitted for `{}`.

**Output envelope (every action, identical shape):**
```json
{"meta": {"status": "ok|approved|rejected|error", "comment": "", "edited": false}, "data": {}}
```

**Result persistence and terminal preview (`do` only; never applies to `do --help`, `do <action> --help`, or any `admin` command):**

- Every success **and error** envelope is written in full before any terminal output.
- Without `-o`: `$BROWSER_PROXY_AUTOSAVE_DIR` or `/tmp/browser-proxy-autosave/<action>_<UTC timestamp>.json`.
- With `-o PATH`: exactly `PATH`; no duplicate default autosave.
- JSON results up to `BROWSER_PROXY_PREVIEW_LINES` (default `config.PREVIEW_LINES_DEFAULT` = `100`, minimum `4`) print in full. Larger JSON prints N/2 first lines, an explicit `… lines omitted; full envelope: PATH …` marker, then N/2 final lines. The saved file is always complete.
- `-f table` renders the requested table in full; the same complete JSON envelope is still persisted first.

#### Profiles

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `profile-list` | scans non-symlink directories under the profile root, then `profile_state.describe_edge_profile()` per entry, plus `extension_connected` from the live bridge | ❌ | Read-only: never creates directories or starts Edge; returns persistent path, `state` (`declared`/`initialized` — see `## Profile lifecycle management`), deterministic port, `systemd_active`, `cdp_reachable`, `extension_connected` |
| `profile-start` | materializes `<profile root>/<name>`, then `systemctl --user is-active`/`start browser-proxy-edge@<profile>.service`, then polls `Browser.getVersion` until CDP answers | ❌ | Always opens a real, visible window if not already running; no daemon-memory shortcut |
| `profile-remove` | stops `browser-proxy-edge@<profile>.service` if active, then `trash-put <profile_dir>` (resolved by absolute path via `shutil.which`, never the `rm` shell wrapper — a daemon subprocess is not guaranteed the same `$PATH`) | ❌ preflight `profile` only | **Never a permanent delete** — the profile directory (bookmarks, cookies, sessions) is moved to the KpihX trash, recoverable with `trash-restore`. Deliberately admin-tier, not extension-approval-gated: see `## Profile lifecycle management` for why. Fails closed (`PROFILE_UNAVAILABLE`) if the profile was never declared, or if `trash-put` is missing — never silently falls back to permanent deletion |

#### Windows

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `window-list` | `Target.getTargets` (filtered to `type=="page"`, via shared `_page_targets()`) then ONE `Browser.getWindowForTarget` per tab, grouped by the real returned `windowId` | ❌ | Returns `{"windows": [{"window_id", "bounds", "tabs": [...]}]}` — never a flat target list (see `## Window grouping`); `tab-list`/`page-list`/`workspace-list` use the shared `_page_targets()` flat helper directly, independent of this grouping |
| `window-create` | `Target.createTarget` (`newWindow: true`), then `Browser.getWindowForTarget` for the real `window_id`, then (optional) `items` layout — see `## Window layout` | ❌ preflight `profile` + verify `url` | Deliberately NOT approval-gated — every managed Edge window is already always real and visible (never headless), so opening one is directly observable the instant it happens; no hidden side effect for an overlay to meaningfully gate |
| `window-close` | `Target.closeTarget` | ✅ preflight `profile`,`target_id` | |

### Window grouping — `window-list` reports real windows, not a flat target list

**Root-caused bug (fixed):** `window-list` used to be byte-identical to `tab-list` — both ran the
identical `[t for t in await browser.targets() if t["type"]=="page"]` filter with zero window
information. `Target.getTargets` alone carries no window-grouping field at all (verified live: two
real tabs shared the exact same `browserContextId` while both genuinely sitting in the SAME real
window — `browserContextId` identifies the profile's browsing context, not a specific window). With
more than one real Edge window open, `window-list` could never have answered "which tab is in which
window." Fixed by calling `Browser.getWindowForTarget` once per tab (`_window_id_for_target()`) —
the one real CDP signal for window identity — and grouping by its returned `windowId`:

```json
{"profile": "default", "windows": [
  {"window_id": 143985019, "bounds": {"left": 0, "top": 0, "width": 1920, "height": 1168, "windowState": "maximized"}, "tabs": [...]}
]}
```

`tab-list`, `page-list`, and `workspace-list` never depended on `window-list`'s shape in the first
place except `tab-list` (which used to literally call `_window_list()` and rename its key) — all 4
actions now share one flat-listing helper, `_page_targets(browser)`, so the SAME
`Target.getTargets`-filtering logic is never duplicated a 4th time, and `tab-list` stays flat and
independent of `window-list`'s grouping.

### Window layout — build a whole tab/group setup in one `window-create` call

`window-create`'s optional `items` field is an ordered list, each entry either:

```json
{"type": "tab", "url": "https://a.example"}
{"type": "group", "title": "Research", "color": "blue", "tabs": ["https://b.example", "https://c.example"]}
```

Processed strictly in the given order — one tab, then a group of N tabs, then another tab, and so
on — every one landing in the SAME new window via its real `window_id` (`Target.createTarget`'s
`windowId` parameter, resolved once via `Browser.getWindowForTarget` right after the window opens
— see `## Window grouping`). A `group` entry's tabs are created first, then `chrome.tabs.group` is
called once with their **real chrome tab ids** — never CDP `target_id` strings, which
`chrome.tabs.group` cannot accept. Those real ids are captured via the SAME extension-mediated
mechanism `browser-get-new-tab` already exposes standalone (`tab.capture_next`): the extension's
`chrome.tabs.onCreated` listener is armed concurrently with the CDP tab creation
(`_create_window_tab()`), so the daemon never guesses which numeric id belongs to which target.

Grouping therefore still requires the paired extension (fails closed with `EXTENSION_UNAVAILABLE`
if it cannot capture a real id) — plain `tab` items never do, since they only need direct CDP.
**No individual approval per item**: because `window-create` itself is approval-free (see above),
every tab/group created through `items` bypasses `tab-create`'s/`group-create`'s own standalone
approval gates too — the whole layout is one single deliberate command, not a series of separately
approved ones. Response shape: `{"profile", "url", "target_id", "window_id", "items": [...]}` —
`items` is present only when the payload supplied one.

#### Tabs

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `tab-list` | `Target.getTargets` via shared `_page_targets()` — flat, independent of `window-list`'s grouping | ❌ | |
| `tab-create` | `Target.createTarget` | ❌ preflight `profile` + verify `url` | optional `new_window`; deliberately NOT approval-gated, same rationale as `window-create` — opening a tab in an always-visible window is directly observable |
| `tab-activate` | `Target.activateTarget` | ✅ preflight `profile`,`target_id` | |

#### Pages

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `page-list` | `Target.getTargets` | ❌ | |
| `page-get` | `Target.getTargetInfo` | ❌ | |

#### Workspaces

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `workspace-list` | `Target.getTargets`, grouped client-side | ❌ | `heuristic: true, authority: "none"` — Edge exposes no public Workspace API |

#### Groups (extension-mediated — `_extension(payload, context, kind)` → `Daemon.extension_request`)

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `group-list` | bridge kind `group.list` | ❌ | heuristic, non-authoritative unless the extension returns a real `chrome.tabGroups` id |
| `group-create` | bridge kind `group.create` | ✅ verify `title` | |
| `group-update` | bridge kind `group.update` | ✅ preflight `group_id` | |
| `group-move` | bridge kind `group.move` | ✅ preflight `group_id` | |

#### Bookmarks (extension-mediated)

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `bookmark-list` | bridge kind `bookmark.list` | ❌ | |
| `bookmark-create` | bridge kind `bookmark.create` | ✅ verify `url` | |
| `bookmark-remove` | bridge kind `bookmark.remove` | ✅ preflight `id` | |

#### Navigation

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `page-navigate` | `Page.navigate` then polls `Runtime.evaluate("document.readyState")` every 0.2s up to `wait_seconds` (default 10) | ❌ | |
| `page-reload` | `Page.reload` (`ignoreCache` optional) + same readyState poll | ❌ | |
| `page-back` | `Page.getNavigationHistory` → `Page.navigateToHistoryEntry` at `currentIndex-1` | ❌ | raises if no entry |
| `page-forward` | same, at `currentIndex+1` | ❌ | raises if no entry |

#### Interaction

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `page-click` | `DOM.getDocument`→`DOM.querySelector`→`DOM.getBoxModel` (resolves selector to a center point) then `Input.dispatchMouseEvent` ×3 (moved/pressed/released) | ❌ | one attached `page_session`, not 3 separate CDP round trips |
| `page-hover` | same box resolution + `Input.dispatchMouseEvent` (moved only) | ❌ | |
| `page-type` | box resolution + click dispatch + optional `Runtime.evaluate` (clear) + `Input.insertText` | ❌ | |
| `page-fill-form` | one `Runtime.evaluate` running a batched JS loop over the `fields` map (`el.value=...` + `input`/`change` events) | ❌ | returns matched-field count |
| `page-select-option` | `Runtime.evaluate` (`el.value=...` + `change` event) | ❌ | |
| `page-scroll` | `Runtime.evaluate` (`scrollIntoView` or `window.scrollTo`) | ❌ | |

#### Inspection

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `page-evaluate` | `Runtime.evaluate` (arbitrary JS, `returnByValue`) | ❌ | same trust model as `raw`'s pre-existing `Runtime.evaluate` allowlist entry |
| `page-snapshot` | `Accessibility.enable` + `Accessibility.getFullAXTree` in ONE attached session (why `page_session` exists — enable-state cannot survive `page_call`'s per-call detach) | ❌ | |
| `page-screenshot` | `Page.captureScreenshot` | ❌ | `output` path writes decoded bytes instead of returning base64 |
| `page-query` | `DOM.getDocument`→`DOM.querySelectorAll`→`DOM.describeNode` (batched per match) | ❌ | |
| `page-console-list` | `Runtime.evaluate` — lazily installs a `console.*` override writing to `window.__browserProxyConsole`, then reads (+optionally clears) it | ❌ | best-effort: only captures messages emitted after first install on that page, no native CDP `Log`/`Runtime` event listener exists in this architecture |
| `page-network-list` | `Runtime.evaluate` — `performance.getEntriesByType('resource'|'navigation')` | ❌ | Timing API only: no response bodies/headers (no CDP `Network` domain listener) |

#### Dialogs

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `page-dialog-policy` | `Runtime.evaluate` — overrides `window.alert/confirm/prompt` to auto-resolve | ❌ | does not survive a future full navigation (no `Page.addScriptToEvaluateOnNewDocument` persistent session) |

#### Downloads

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `page-set-download-behavior` | `Browser.setDownloadBehavior` (browser-level, no `target_id`) | ❌ | creates the local directory first |

#### Cookies (browser-level, no `target_id`)

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `cookie-list` | `Network.getCookies` | ❌ | |
| `cookie-set` | `Network.setCookie` | ✅ verify `name` | |
| `cookie-remove` | `Network.deleteCookies` | ✅ preflight `name`,`domain` | |

#### Storage

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `storage-local-get` | `Runtime.evaluate` (`localStorage.getItem`/`Object.fromEntries`) | ❌ | ⚠️ localStorage can hold session/auth tokens — caller responsible, same caveat as `page-evaluate`/`raw` |
| `storage-local-set` | `Runtime.evaluate` (`localStorage.setItem`) | ✅ | |

#### Human-in-the-loop (extension-mediated — no `@require_approval`: the extension overlay itself IS the human gate)

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `browser-ask-user` | bridge kind `user.ask` | none needed | overlay text/password input |
| `browser-dismiss-overlays` | bridge kind `overlay.dismiss` | none needed | heuristic cookie-banner/modal dismissal |
| `browser-solve-captcha` | bridge kind `captcha.solve` | none needed | best-effort: checkbox click only, image-grid solving explicitly not implemented |
| `browser-set-date` | bridge kind `form.set_date` | none needed | native `<input type=date>` only, no MUI/AntD |
| `browser-set-combobox` | bridge kind `form.set_combobox` | none needed | heuristic, no MUI/AntD |
| `browser-drop-file` | bridge kind `form.drop_file` | none needed | content supplied inline (base64) by the caller — extension has no filesystem access |
| `browser-get-new-tab` | bridge kind `tab.capture_next` | none needed | one-shot `chrome.tabs.onCreated` listener with timeout |

#### Advanced

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `raw` | any browser-level CDP `method`+`params`, verbatim | dynamic: `is_read_only_method()` allowlist (`Browser.getVersion`, `Target.getTargets`, `Target.getTargetInfo`, `Browser.getWindowForTarget`, `Browser.getWindowBounds`, `Page.getNavigationHistory`, `Runtime.evaluate`) bypasses approval; every other method is fail-closed extension-approved | escape hatch for anything not yet a first-class action |

---

## Internal entrypoints (never part of the public `do`/`admin` contract)

Two more top-level commands exist in the same binary, both `hidden=True` (never in `--help`, never
meant to be typed by a human or an agent) — they exist solely as `ExecStart=` targets for systemd:

| Hidden command | Invoked by (`ExecStart=`) | Wraps `admin`/`do`? | What it actually does |
|---|---|---|---|
| `daemon` | `browser-proxy.service` | No — it **is** the server | Runs `Daemon().serve()`: opens the Unix socket, holds the exclusive lock, dispatches every `do`/`admin` request. `admin start`/every `do <action>` are thin clients talking to *this* process over the socket. |
| `edge-launch <profile>` | `browser-proxy-edge@<profile>.service` | No — it **becomes** Edge | Resolves the deterministic port + profile dir, builds the Edge argv, then `os.execvp`s into the real `microsoft-edge` binary (replacing itself — systemd tracks Edge's real PID). `admin edge start/stop/status` and `Daemon.start_profile()` only ever `systemctl start/stop/is-active` this unit; neither calls `edge_launch()`'s logic directly. |

---

## Config — where state actually lives (`config.py` + `paths.py`, no hardcoded values)

`config.py` is the single source of truth for every default value and every overriding
environment variable NAME (`ENV_*`/`*_DEFAULT` constants) — `paths.py`/`daemon.py`/`bridge.py`/
`cli.py` all read from it instead of inlining literals. Two durability classes matter and must
never be confused:

| Directory | Durability | Holds |
|---|---|---|
| `runtime_dir()` — `$XDG_RUNTIME_DIR/browser-proxy` (`BROWSER_PROXY_STATE_DIR`) | **Ephemeral** — tmpfs, wiped at logout/reboot | Unix socket, daemon lock only |
| `persistent_state_dir()` — `$XDG_STATE_HOME/browser-proxy` or `~/.local/state/browser-proxy` (`BROWSER_PROXY_PERSISTENT_STATE_DIR`) | **Persistent** — survives reboot/logout | Extension pairing secret |
| `edge_profile_root()` — `~/.local/share/browser-proxy/profiles/` (`BROWSER_PROXY_PROFILE_ROOT`) | **Persistent** | One `--user-data-dir` per profile |

**Root-caused bug (fixed):** the pairing secret used to live under `runtime_dir()` (tmpfs). The
extension's own secret (`chrome.storage.local`) is genuinely persistent, so every logout/reboot
silently wiped only the daemon's half of the pair, forcing a perpetual, avoidable
`admin extension pair` re-run even though nothing was actually wrong with the pairing itself. The
secret now lives in `persistent_state_dir()` — a durability class identical to the Edge profile
root, never `$XDG_RUNTIME_DIR`.

| What | Path (default, all overridable by env var) | Notes |
|---|---|---|
| Daemon runtime dir | `$XDG_RUNTIME_DIR/browser-proxy` (`BROWSER_PROXY_STATE_DIR`) | ephemeral — see table above |
| Unix socket | `<runtime dir>/browser-proxy.sock` | |
| Daemon lock | `<runtime dir>/browser-proxy.lock` | |
| Extension pairing secret | `<persistent state dir>/extension.token` (mode 0600, never printed) | generated visibly once by the extension Options page; operator transfers it through `admin extension pair`'s hidden terminal prompt; **persistent**, not tmpfs |
| Extension bridge port | `37291` (`BROWSER_PROXY_EXTENSION_PORT`) | loopback only |
| Edge profile root | `~/.local/share/browser-proxy/profiles/` (`BROWSER_PROXY_PROFILE_ROOT`) | |
| One Edge profile dir | `<profile root>/<name>` (`edge_profile_dir()`) | **Source of truth** for one persistent Chromium `--user-data-dir`; survives Edge, daemon, and machine restarts — see `## Profile identity` for the declared/initialized distinction |
| Edge CDP port | `33000..41999`, sha256-derived from the profile name (`edge_cdp_port()`, override `BROWSER_PROXY_EDGE_PORT`) | deterministic — daemon/CLI/manual start always agree |
| Edge executable | `shutil.which("microsoft-edge")` (override `BROWSER_PROXY_EDGE_PATH`) | |
| Terminal JSON preview threshold | `100` lines (`BROWSER_PROXY_PREVIEW_LINES`, minimum `4`) | `config.PREVIEW_LINES_DEFAULT` |
| Daemon idle TTL | `1800`s (`BROWSER_PROXY_IDLE_SECONDS`) | **suspended while an extension is connected** — see `## Idle lifecycle` |
| Daemon hard lifetime cap | `28800`s (`BROWSER_PROXY_MAX_LIFETIME_SECONDS`) | always applies, even while connected |

### Idle lifecycle (fixed)

**Root-caused bug (fixed):** `Daemon._lifecycle()` used to self-stop after `idle_seconds` purely
from the absence of a `do`/`admin` CLI call — even while an authenticated extension stayed
connected and useful, force-closing a healthy bridge for no functional reason. The idle timer is
now suspended entirely while `self.bridge.connected` is true; `max_lifetime_seconds` is the only
unconditional ceiling left.

---

## Architecture

```
browser-proxy
   │
   ├── do <action> '<json>' [-o/-f]     # flat actions, Unix-socket client
   └── admin <command>                  # lifecycle/health, Unix-socket client (+ systemctl for edge/install/start/stop)
        │
        ▼
┌───────────────────────────────┐        ┌──────────────────────────────────┐
│ browser-proxy.service          │        │ browser-proxy-edge@<profile>     │
│  ExecStart: browser-proxy daemon│        │  .service (one per profile,      │
│  = Daemon().serve()            │──CDP──▶│  ALWAYS visible)                 │
│  Unix socket + policy engine   │  WS    │  ExecStart: browser-proxy         │
│                                 │◀──────│  edge-launch %i → execvp msedge   │
└───────────────────────────────┘        └──────────────────────────────────┘
   │
   └──authenticated loopback WS──▶ browser-proxy-ext (paired Edge extension)
```

### Doc system (`doc.py` → user-facing `--help`)

Every handler in `actions.py` (and every helper anywhere in `src/browser_proxy/`) carries a
structured docstring with mandatory `Purpose`/`Args`/`Returns`/`Examples` sections
(`tests/test_docstrings.py` enforces this on the whole source tree, not just registered actions).
Implementation docstrings remain code-level documentation and are enforced by
`tests/test_docstrings.py`. Before CLI registration, `attach_public_docstrings(REGISTRY)` replaces
each registered handler's **runtime docstring** with its Tick-style public form: description,
`Parameters:`, and at least three command → result `Examples:`. `doc.py` then consumes those actual
runtime docstrings exactly like tick-proxy: `get_compact_help()` removes the Examples section for
the grouped `do --help` catalog, while `get_full_help()` renders the full action docstring and
wraps every `→ {json}` result in the real meta/data envelope. A single canonical payload/result and
field-description map generates those docstrings, preventing 50 driftable hand-maintained copies.
Internal `DaemonContext`/implementation documentation is intentionally excluded from public help.

## Profile lifecycle management

A profile has **4 independent axes** — never conflated, never computed twice differently:

| Axis | Meaning | Computed by |
|---|---|---|
| disk identity | `not_declared`/`declared`/`initialized` (see states table below) | `paths.edge_profile_state()` |
| systemd activation | is the templated unit currently `active`? | `profile_state.is_edge_unit_active()` |
| CDP reachability | a REAL `Browser.getVersion` round-trip, attempted unconditionally (never gated on systemd's reported state — a real network probe is more trustworthy than trusting systemd as a proxy for it) | `profile_state.describe_edge_profile()` |
| extension bridge | is THIS profile's extension currently handshaken? (per-profile, see `## Extension bridge identity`) | `Daemon.bridge.connected_profiles()` |

**Root-caused bug (fixed twice over):** first, `discover_edge_profiles()`/`_profile()` used to
treat "the directory exists" as "this is a real Edge profile" (see states table below). Second,
the systemd+CDP probe logic for the first 3 axes was hand-duplicated in BOTH `daemon.py` (used by
`profile-list`/`admin status`) AND `cli.py` (used by `admin edge status`) — two independently
maintained copies that could silently drift apart. `profile_state.py` is now the single canonical
module: `describe_edge_profile(name)` is the ONE implementation, imported by both call sites, so
there is exactly one place to fix if the systemd/CDP contract ever changes.

### Profile identity — the `not_declared` / `declared` / `initialized` states (no cache, no magic)

```text
Disk profile directory = persistent profile identity
systemd unit + CDP endpoint = current observable execution state
daemon memory = transport/policy only; never an inventory
workspace = heuristic organization inside one profile; never a profile name
```

**Root-caused bug (fixed):** `discover_edge_profiles()`/`_profile()` used to treat "the directory
exists" as "this is a real Edge profile." But `materialize_edge_profile()` (called by
`admin edge start`/`profile-start` before launching systemd) only runs a bare `mkdir` — it
**declares** a directory, it does not make Edge treat it as initialized. A directory left behind by
a failed `systemctl start`, or reserved ahead of time, is not a genuine Edge profile: only Edge
itself, by actually booting against that `--user-data-dir` at least once, stamps it as real —
verified live: every one of 3 real browser-proxy profile directories contains a `Local State` file
at its root the instant Edge has run there, and a bare `mkdir`'d directory never does.

`paths.edge_profile_state(path)` is now the **one** predicate, used identically everywhere
(`profile-list`, `admin edge status`, `admin status`, `_profile()`):

| State | Meaning |
|---|---|
| `not_declared` | No such directory — `profile-start`/`admin edge start` has never run for this name |
| `declared` | Directory exists (browser-proxy `mkdir`'d it) but Edge has never actually booted there (no `Local State`) — a failed start, or a reservation ahead of time |
| `initialized` | Edge has genuinely started against this directory at least once (`Local State` present) — a real Edge profile |

`profile-list` scans the profile root without creating it and reports every entry's `state`. Any
direct-CDP action (`_profile()`) requires `initialized` — a `not_declared` or merely `declared`
profile fails closed with a distinct, actionable `PROFILE_UNAVAILABLE` message naming
`profile-start` as the fix, rather than a generic CDP connection failure. Restarting the daemon
cannot erase this disk-backed inventory (see `## Config` for the durability guarantee).

### Collision guard — case-insensitive names refused

**Root-caused bug (fixed):** `Default` and `default` previously coexisted as two unrelated
top-level profile directories — nobody intended to create two separate isolated Edge installs, the
name was just typed with a different case once. The filesystem is case-sensitive; humans routinely
are not. `materialize_edge_profile()` now refuses (`ValueError`, surfaced as `VALIDATION_ERROR` via
`do profile-start`/`typer.BadParameter` via `admin edge start`) any name that collides
case-insensitively with a DIFFERENT already-declared profile. Re-declaring the SAME profile again
(e.g. restarting `default`) is never treated as a collision with itself.

### Removal — `profile-remove` never permanently deletes

Stops the systemd unit if active, then moves the profile directory to the KpihX trash by calling
`trash-put` (`trash-cli`) **by its resolved absolute path** (`shutil.which("trash-put")`) — never
the interactive `rm` shell wrapper, because a systemd-spawned daemon subprocess is not guaranteed
the same `$PATH` ordering an interactive login shell has. If `trash-put` is missing, the action
fails closed (`PROFILE_UNAVAILABLE`) rather than silently falling back to a permanent
`shutil.rmtree` — a "safe delete" feature must never quietly become an unsafe one. Deliberately
**not** `@require_approval`: the most likely removal candidates (an orphaned/never-initialized
profile, exactly like the `Default`/`default` collision above) have no reachable extension to ask
for approval in the first place. Safety instead comes from the mandatory named
`@require_preflight("profile")` identity plus the trash-not-delete guarantee — the same admin-tier
trust level as `admin edge start`/`stop`, not a content-mutation-inside-a-live-page action.

```
1. browser-proxy admin edge start default
   └─ materialize ~/.local/share/browser-proxy/profiles/default/
       └─ systemctl --user start browser-proxy-edge@default.service
      └─ edge-launch default → execvp real msedge, port 38049, real window opens

2. (manual, once) load browser-proxy-ext/ unpacked in that window. In its Options page click
   **Generate pairing secret**; copy it once; run `admin extension pair` and paste into its hidden
   terminal prompt; return to Options and click **Save**. The secret is never printed, logged, or
   placed in shell history.

3. browser-proxy do profile-start '{"profile":"default"}'
   └─ systemctl --user is-active browser-proxy-edge@default.service → already active → skip start
      └─ poll Browser.getVersion on port 38049 → ready → {"profile":"default","cdp_port":38049}

4. browser-proxy do tab-create '{"profile":"default","url":"https://example.com"}'
   └─ @require_approval → Daemon._approve() → bridge.request("approval", {...}, timeout_seconds=600)
      └─ extension shows the overlay → KπX clicks Approve
      └─ Target.createTarget({"url":"https://example.com","newWindow":true}) → {"target_id":"..."}

5. browser-proxy do page-navigate '{"profile":"default","target_id":"...","url":"https://x.com"}'
   └─ Page.navigate, then poll Runtime.evaluate("document.readyState") until "complete"
```

---

## Extension bridge identity — one profile, one connection, never confused

**Root-caused bug (fixed):** every profile's action (`bookmark-list`, `group-list`,
`browser-ask-user`, …) is `_extension()`-mediated, and `ExtensionBridge` used to hold exactly ONE
WebSocket connection (`self._connection`), regardless of how many Edge profiles had the extension
loaded. `bookmark-list` ignored the payload's `profile` field entirely — **verified live**: 3
different profile names returned the byte-identical bookmark tree, because there was only ever one
physical slot for "the extension," last-connect-wins.

Each browser-proxy profile is a fully separate `--user-data-dir` = a fully separate Chromium
install = a fully separate extension install with its own `chrome.storage.local`. The Options page
(`browser-proxy-ext/options.html`) now has an explicit **Browser-proxy profile** field (defaults to
`default`) the operator sets once per profile, alongside the shared secret. The handshake carries
it: `{"type":"handshake","token":"...","extension_id":"...","profile":"<declared name>"}`.
`ExtensionBridge` keys connections by that declared name (`_connections: dict[str, ServerConnection]`)
— `bridge.request(kind, payload, profile)` and `Daemon._approve(action, payload)` (which extracts
`payload["profile"]`) both route to that one exact connection, never any other. A request for a
profile with no matching connection fails closed: `EXTENSION_UNAVAILABLE: <profile>`, naming the
exact profile instead of a bare boolean. `_extension()` echoes `"profile"` in every response,
confirming which profile actually answered. `admin status`/`admin doctor`/`ping` expose
`extension_connected_profiles: [...]` (list of every currently-connected profile) alongside the
pre-existing coarse `extension_connected: bool` — the same "one precise predicate, never a hiding
aggregate" discipline as `paths.edge_profile_state()`.

---

## HITL design — extension-mediated approval overlay

**`@require_approval` is required for every mutating/destructive/sensitive `do` action** (see the
per-action tables above for the exact list). The flow, matching `Daemon._approve()` and
`Daemon.dispatch()` exactly:

```
1. Agent runs: browser-proxy do tab-create '{"profile":"default","url":"https://example.com"}'
2. dispatch() sees action.policy.approval == True
3. preflight_fields checked first (payload must already contain them, e.g. "profile")
4. _approve("tab-create", payload) → profile = payload["profile"] → bridge.request("approval",
     {"action": "tab-create", "payload": {...}, "timeout_seconds": 600}, profile)
     — routed exclusively to THAT profile's connection, so the overlay only ever appears in the
     correct Edge window, never a different profile's
5. Extension shows a closed-shadow-root overlay in the paired Edge window:
   scopes only (never raw payload/secrets) — Approve once / Deny
6. Extension replies {"decision": "approved"|"rejected", "comment": "...", "payload": <possibly edited>}
7. "rejected" (or the 600s timeout) → PermissionError("APPROVAL_REJECTED") → fail closed
8. "approved" → action.handler(edited_payload, self) actually runs the real CDP/extension call
9. If action.policy.verification is set, the result is read back and checked against the payload
10. Envelope: {"meta":{"status":"ok","comment":"...","edited":true|false},"data":{...}}
```

**Fail-closed always:** no approval reply, a bridge disconnect, or a timeout are all treated as
rejection — never as an implicit approval.

**Human-in-the-loop actions (`browser-ask-user`, `browser-solve-captcha`, …) carry no
`@require_approval` decorator** — they are not "approved before running", they ARE the mechanism
that puts a human in the loop (the overlay itself is the action's entire purpose), so there is
nothing separate to gate.

---

## Error codes

`DAEMON_UNAVAILABLE`, `DAEMON_ALREADY_RUNNING`, `PROFILE_UNAVAILABLE`, `CDP_UNAVAILABLE`,
`EXTENSION_UNAVAILABLE`, `LEASE_CONFLICT`, `APPROVAL_REQUIRED`, `APPROVAL_REJECTED`,
`VALIDATION_ERROR`, and `RAW_METHOD_DENIED` are stable machine-readable failures
(`Daemon.dispatch()`'s exception handling maps every raised error to exactly one of these).

---

## Status

- See `CHANGELOG.md` for version history.
- See `TODO.md` for pending work.
- See `README.md` for user-facing documentation.

*Architecture contract fully rewritten 2026-08-29 to match the `tg-proxy` CONTRACT.md standard of
exhaustive, table-driven transparency on both usage and underlying functioning, after KπX flagged
the prior version as incomplete/opaque.*
