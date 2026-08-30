# browser-proxy

`browser-proxy` is a local-first, profile-aware **Microsoft Edge-only** automation CLI. It replaces per-agent browser MCP processes with one on-demand daemon, systemd-templated Edge instances (one per profile), and a KπX-owned Edge extension bridge.

## Contract

The public interface is deliberately identical in philosophy to `tick-proxy`:

```text
browser-proxy do <flat-domain-first-action> '<one-json-object>' [-o FILE] [-f json|table]
browser-proxy admin <command>
```

The positional payload is always **exactly one JSON object** or a path to a file containing
exactly one JSON object. Options only control presentation or output location; they never carry
business data. This applies to every action, including `raw`.

```bash
browser-proxy do profile-list '{}'
browser-proxy do profile-remove '{"profile":"test"}'
browser-proxy do window-create '{"profile":"default","url":"https://example.com"}'
browser-proxy do window-create '{"profile":"default","url":"https://a.example","items":[
  {"type":"tab","url":"https://b.example"},
  {"type":"group","title":"Research","tabs":["https://c.example","https://d.example"]},
  {"type":"tab","url":"https://e.example"}
]}'
browser-proxy do raw '{"method":"Target.getTargets","params":{}}'
browser-proxy do group-list '{"profile":"default"}'
browser-proxy do tab-move '{"profile":"default","tab_id":12,"after_tab_id":34}'
browser-proxy do group-add-tabs '{"profile":"default","group_id":7,"tab_ids":[12,13]}'
browser-proxy do group-remove-tabs '{"profile":"default","tab_ids":[12]}'
browser-proxy do group-sync '{"profile":"default","layout":[
  {"type":"tab","tab_id":1},
  {"type":"group","title":"Research","tab_ids":[2,3]},
  {"type":"tab","tab_id":4}
]}'
browser-proxy do page-navigate '{"profile":"default","target_id":"T1","url":"https://example.com"}'
browser-proxy do page-click '{"profile":"default","target_id":"T1","selector":"#submit"}'
browser-proxy do page-evaluate '{"profile":"default","target_id":"T1","expression":"document.title"}'
browser-proxy do cookie-list '{"profile":"default"}'
```

Each successful command writes this envelope to stdout:

```json
{"meta":{"status":"ok","comment":"","edited":false},"data":{}}
```

## Architecture

```text
CLI → Unix socket → browser-proxyd → direct CDP → Edge profile processes
                                  └→ authenticated loopback WS → browser-proxy-ext
```

The disk directory `$HOME/.local/share/browser-proxy/profiles/<profile>` is the persistent identity
of a profile; it is never a daemon cache. A "browser-proxy profile" is a whole isolated
`--user-data-dir` (one systemd unit, one CDP port) — a different level than Edge's own internal
people-profile concept (`Default`, `Profile 1`, … at `chrome://settings/people`, living *inside*
one `--user-data-dir`); never confuse the two. `materialize_edge_profile()` only ever `mkdir`s the
directory — it **declares** it, it does not make Edge treat it as real. `paths.edge_profile_state()`
is the one predicate (`not_declared`/`declared`/`initialized`, keyed on Edge's own `Local State`
marker file) used identically by `profile-list`, `admin edge status`, `admin status`, and direct-CDP
action resolution — see `CONTRACT.md` → Profile lifecycle management. `profile_state.py` is the
single canonical module computing disk state + systemd activation + real CDP reachability (no
longer hand-duplicated between `daemon.py` and `cli.py`); `materialize_edge_profile()` refuses
names that collide case-insensitively with a different already-declared profile. The daemon owns
transport, policy, and target/window control while systemd owns Edge process execution.
`profile-list` discovers disk profiles and reports their state plus systemd/CDP/extension status.
`profile-remove` stops the unit if active then moves the directory to the KpihX trash
(`trash-put`, resolved by absolute path) — never a permanent delete. The extension provides
approval overlays, secret-safe user input, Edge tab-group operations, and difficult-widget
fallback. Edge Workspaces are modeled as semantic containers inside profiles because Edge provides
no documented Workspace API to CDP or extensions.

**Extension bridge identity is per-profile too** (fixed after a real bug: 3 profiles returned the
byte-identical bookmark tree because only one global connection slot existed). Each profile is a
separate extension install; the Options page declares which profile it belongs to, the daemon keys
connections by that name, and every extension-mediated request (`bookmark-*`, `group-*`,
`browser-ask-user`, approval overlays, …) is routed exclusively to the matching profile's own
connection — a request for a profile with no matching connection fails closed by name
(`EXTENSION_UNAVAILABLE: <profile>`), never silently answered by a different profile. See
`CONTRACT.md` → Extension bridge identity.

**Every HITL prompt is 100% transparent and redirects to itself (KπX directive).** The overlay
shows the REAL non-secret proposal details (real `tab_ids`, `title`, `color`, `url`, …), never just
a bare action name — only genuinely secret-shaped fields (a cookie's real value, a dropped file's
raw bytes, any password) are shown as `<redacted>` instead. The extension always brings the hosting
tab AND its window to the front first — never a prompt you have to discover by accident. The same
centralized tab resolution backs every HITL kind (approval, ask, dismiss-overlays, captcha, set
date/combobox, drop-file), including automatic retry via a fresh temporary tab if the found one's
content script turns out stale (e.g. right after the extension itself reloads) — that temporary tab
is always closed again once the interaction settles, never left behind. `do group-sync` reorganizes
a whole window's tab/group layout — create, rename, recolor, add-to, remove-from, reposition — in
ONE call. See `CONTRACT.md` → HITL transparency and redirection.

The registry covers the full Edge profile hierarchy: profiles, heuristic Workspaces, windows,
tab groups, tabs, pages, and profile bookmarks. `workspace-list` and `group-list` clearly label
heuristic/non-authoritative data where Edge lacks a public Workspace API. The implementation is
strictly Edge-only; it does not launch, target, or publish for Chrome.

**Tab/group structure is one canonical computation, not three independent views.** CDP has no
concept of tab groups or real tab order at all — only `chrome.tabs.query`/`chrome.tabGroups.query`
(extension-side) can answer "what group is this tab in" or "what is the real left-to-right order."
`computeWindowLayouts()` computes that ONE truth once; `window-list`'s `chrome_layout` field,
`group-list`, and the movement actions (`tab-move`, `group-add-tabs`, `group-remove-tabs`) all read
or mutate the exact same state. See `CONTRACT.md` → Canonical tab/group structure for the full
`tabs`/`groups`/`order` shape, the CDP-`target_id`-vs-`chrome_tab_id` bridging strategy, and why the
3 new movement actions are deliberately approval-free like `window-create`/`tab-create`.

`raw` sends a browser-level CDP method and its parameters inside that same object. Conservative
read-only methods (`Browser.getVersion`, `Target.getTargets`, and related inspection calls) run
without approval. Every other raw method, including mutations, is blocked behind fail-closed
extension approval; a payload flag can never bypass it.

## Lifecycle

The daemon (`browser-proxy.service`, started via `admin start`, or on demand by the client's own
fallback) owns an exclusive lock and uses a Unix-domain socket. **It has deliberately NO automatic
timeout — no idle TTL, no maximum lifetime (KπX directive).** It is purely lançable/arrêtable on
request: `admin start`/`admin stop` (which now sends the real `shutdown` RPC over the socket first,
falling back to `systemctl stop` only if the socket is unreachable), or the OS itself. Every
managed Edge window is already always visible, so KπX can directly see and close one an agent
forgot — there is no case where an unattended timeout is the right way to reclaim a daemon.

Every Microsoft Edge instance is its **own separate systemd-templated service**, decoupled from the
daemon's lifetime — never a raw `subprocess.Popen`, never a hand-typed `microsoft-edge` command.
**There is no headless mode and no flag to hide the window: every instance is always real and
visible, by design (100% Transparency).**

```bash
browser-proxy admin edge install                  # once per machine: link the unit template
browser-proxy admin edge start test               # real window opens
#   -> edge://extensions -> developer mode -> load unpacked -> browser-proxy-ext/
#   -> browser-proxy admin extension pair -> paste secret in the extension's options page
browser-proxy do profile-start '{"profile":"test"}'   # daemon starts the same unit if not already running
browser-proxy admin edge status test              # state / systemd_active / cdp_port / cdp_reachable / extension_connected
browser-proxy do profile-remove '{"profile":"test"}'  # stops the unit if active, trashes the directory (never a permanent delete)
```

The first start materializes `$HOME/.local/share/browser-proxy/profiles/test/` before systemd is
called. It therefore remains listed while stopped or after a daemon restart; `profile-list` itself
is read-only and never creates a directory.

The loopback CDP port for a profile is deterministic (`edge_cdp_port()`, sha256-derived — see
`CONTRACT.md` → Edge lifecycle), so the daemon, the CLI, and a manually-started instance all agree
on it without any file-based or IPC handoff.

## Development

```bash
make install-dev
make check
make smoke
make stress
```

Every default value (ports, TTLs, the terminal JSON preview threshold, directory names) and its
overriding environment variable name lives in `src/browser_proxy/config.py` — never inline literals
scattered across `paths.py`/`daemon.py`/`bridge.py`/`cli.py`.

## Extension

`browser-proxy-ext` is an independent repository and Git submodule. Build it with its own Makefile; its compiled package is submitted only to Microsoft Edge Add-ons.

`browser-proxy admin extension pair` rotates a mode-0600 local capability without displaying it, stored under `paths.persistent_state_dir()` (survives reboot/logout — never the daemon's ephemeral `runtime_dir()`/tmpfs). The extension bridge only accepts an authenticated typed `handshake` and dispatches typed request/reply frames over loopback. The extension's own reconnect loop is backed by a `chrome.alarms` watchdog (not just `setTimeout`), so it recovers even after a Manifest V3 service-worker eviction.

**One Options-page setup per profile, not just once per machine:** each profile is a separate Edge install with its own extension storage — its Options page (`browser-proxy-ext/options.html`) needs both the shared secret AND a **Browser-proxy profile** field matching the exact profile name used with `admin edge start`/`profile-start` for that window. The shared secret can be the same value across every profile; the declared profile name must be unique per install, or requests will be routed to whichever install most recently declared that name.

## Security

- CDP endpoints bind to loopback only.
- The CLI uses a per-user Unix socket.
- The extension authenticates with a paired, short-lived capability.
- Password values and secret-bearing storage are never returned to an agent.
- `raw` has a conservative read-only CDP allowlist; all mutations require extension approval.
