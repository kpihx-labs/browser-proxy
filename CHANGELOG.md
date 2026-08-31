# Changelog

## 0.8.0 - 2026-08-31

- **InPrivate/Incognito navigation** — `window-create` and `tab-create` both accept a new
  optional `incognito: bool` parameter. When `true`, the tab(s) are created in a fresh
  `Target.createBrowserContext` private browsing context instead of the profile's default context.
  All layout tabs in `window-create` share the same incognito context; `tab-create` creates its
  own per-call context. `incognito` is mutually exclusive with `window_id` in `tab-create` (you
  cannot add an incognito tab to a non-incognito window). Uses `Target.createBrowserContext`
  (the correct CDP domain for Edge — `Browser.createBrowserContext` does not exist in Edge CDP).

## 0.7.0 (previous) - 2026-08-31

- **`page-screenshot` — removed the `translateZ(0)` body-transform hack** (root cause of SPA
  layout breakage on YouTube Music and other complex SPAs). The old hack modified
  `document.body.style.transform` to force a repaint before capturing, but this changed the
  page's computed style, triggering layout recalculations that broke rendering (player bar
  disappeared, queue became invisible, page went black). `Page.captureScreenshot` already forces
  an internal repaint — the hack was unnecessary. Now optional via `force_repaint: true` payload
  param, to be used only as a last resort.
- **New action: `page-press`** — press and release a keyboard key via CDP `Input.dispatchKeyEvent`
  (`keyDown` + `keyUp`). This is the real CDP input pipeline, unlike `page-evaluate` + JS
  `new KeyboardEvent().dispatchEvent()` which only fires in the JS event system and may be
  ignored by the page's internal handlers. Accepts `key` (e.g. `"Space"`, `"Enter"`,
  `"ArrowDown"`), optional `text` (character to insert), optional `modifier` bitfield
  (1=Alt, 2=Ctrl, 4=Meta, 8=Shift). Ideal for play/pause, navigation, and keyboard shortcuts
  on complex SPAs.

## 0.6.0 - 2026-08-30

- **3 live-verified bugs found and fixed during a full real-site test sweep of all 7 HITL actions
  (KπX: "corrige tots els desync partotu code contract")**: (1) `cookie-remove` read the raw CDP
  target as `["id"]` instead of the real `["targetId"]` — a `KeyError` masked by a mock matching
  the bug and by the one live attempt failing earlier at the extension-approval step, never
  reaching that line; (2) `page-evaluate` crashed with `CDP_ERROR: Object reference chain is too
  long` on any expression returning a non-serializable value (e.g. `window.open(...)`) — now
  retries without `returnByValue`, returning a safe description instead; (3)
  `browser-dismiss-overlays`'s accept-text regex used bare substring search, so `ok` matched inside
  an unrelated "JEUX SUDOKU" nav link and clicked it instead of the real "Accepter" button on a real
  multi-layer consent flow — fixed with word boundaries + a `<button>`-over-`<a>` preference.
- **`browser-solve-captcha`'s `click_checkbox` now dispatches a REAL CDP-level click** instead of
  an always-ineffective same-origin content-script click (live-verified: reCAPTCHA's anchor iframe
  is served cross-origin from `www.google.com` in every real deployment). The extension reports the
  iframe's own rect + tab url; the daemon resolves the CDP `target_id` and reuses
  `page-click-coordinates`'s own `Input.dispatchMouseEvent` primitive. Live-verified against the
  official Google demo: the checkbox now shows a genuine green checkmark.

## 0.5.0 - 2026-08-29

- **HITL transparency, redirection, and one-shot reorganization (KπX directives, all live-verified
  during real testing)**:
  - Every HITL prompt now shows the REAL non-secret proposal details (`describeApprovalDetails()` —
    real `tab_ids`, `title`, `color`, `url`, …), never just a bare action name; only genuinely
    secret-shaped fields (`value`, `content_base64`, `password`) are shown as `<redacted>`.
  - Every HITL-hosting tab is now actively focused, tab AND window, before the prompt shows
    (`focusHostTab()`) — never a prompt KπX has to discover by accident (confirmed live: a
    temporary tab appeared unnoticed during earlier testing).
  - `sendToHostTab()` centralizes the same tab-resolution/focus/stale-content-script-retry
    `requestApproval()` already had across all 6 non-approval HITL kinds (`user.ask`,
    `overlay.dismiss`, `captcha.solve`, `form.set_date`, `form.set_combobox`, `form.drop_file`) —
    never six hand-duplicated copies.
  - Fixed a real temporary-tab leak: expiry was a plain `setTimeout`, silently discarded if the
    service worker is evicted before it fires (confirmed live: a temporary `https://example.com`
    tab stayed open for many minutes across several turns after one approval was abandoned
    mid-flow). Now armed via `chrome.alarms` (`armApprovalExpiryAlarm()`), which always survives
    eviction — same pattern as the reconnect watchdog.
  - New **`do group-sync`**: reorganizes a WHOLE window's tab/group layout in ONE call — create,
    rename, recolor, add-to, remove-from, and reposition, all at once (`{"layout": [{"type":"tab",
    "tab_id"}|{"type":"group","group_id"?,"title"?,"color"?,"tab_ids"}]}`), never N separate
    primitive calls for one deliberate rearrangement. Deliberately not approval-gated, same
    rationale as `tab-move`/`group-add-tabs`/`group-remove-tabs`.
  - Extension test suite gained a `FakeWebSocket` (replacing a real, unmocked `WebSocket` that used
    to attempt a genuine loopback connection at every test run) — root-caused a real, confirmed-live
    test flake where the real socket's delayed `close` event unpredictably rejected unrelated
    in-flight content-script replies later in the suite.
  - 9 new TypeScript tests (transparency ×2, redirect ×1, sendToHostTab centralization ×2,
    group.sync ×3, plus the FakeWebSocket fix) plus the new `do group-sync` action registered
    Python-side, all green.
- **Removed the daemon's automatic timeout entirely — no idle TTL, no maximum lifetime (KπX
  directive, GRAVÉ)**: an idle-suspended-while-connected TTL (the previous fix) still resumed and
  killed the WHOLE daemon — CDP included — the instant the extension bridge merely dropped for any
  unrelated reason (network blip, an old un-reloaded extension build, computer sleep), confirmed
  live via `journalctl` showing repeated daemon restarts unrelated to genuine inactivity. `Daemon()`
  now takes no lifecycle configuration at all (`idle_seconds`/`max_lifetime_seconds` removed, not
  merely defaulted huge); `_await_explicit_stop()` is the ONLY stop path, blocking forever on
  `self._stop` until the `shutdown` RPC sets it. The systemd unit's own `RuntimeMaxSec=8h` was
  removed too — a second, independent automatic-timeout mechanism enforcing the exact same thing
  one layer lower. Every managed Edge window is already always visible, so KπX can directly see and
  close one an agent forgot — there is no case where an unattended timeout is the right way to
  reclaim a daemon. `admin stop` was also fixed as a direct consequence: it only ran `systemctl
  --user stop`, silently no-oping for a daemon systemd never launched (root-caused a real hang in
  `make smoke`'s isolated test daemon, previously masked by the idle-TTL fallback this pass
  removed) — it now sends the real `shutdown` RPC over the socket FIRST (works identically whether
  systemd-managed or not), falling back to `systemctl stop` only if the socket is unreachable.
- **Purged `.env`/`.env.example`**: no code in this repo reads a dotenv file at runtime (every
  override reads `os.environ` directly); every default value and its overriding environment
  variable name already lives solely in `src/browser_proxy/config.py`, making a checked-in
  `.env.example` pure redundant, driftable documentation.
- **Canonical tab/group structure refonte (KπX request — "penser structure, pas patcher après
  coup")**: `tab-list` used to be flat CDP targets with zero group awareness; `group-list` was a
  disconnected extension-sourced view with its own numeric ids — no way to answer "which tab is in
  which group" or "what's the real visual order" without manual cross-referencing, because CDP has
  no concept of tab groups or real tab `index` at all (only `chrome.tabs.query`/
  `chrome.tabGroups.query`, extension-side, can answer it). New `computeWindowLayouts()`
  (`background.ts`) computes the ONE real truth per window in a single pass — a flat ordered
  `tabs` list (each carrying its own `group_id`, `null` if ungrouped), pure `groups` metadata, and
  `order` (the exact visual left-to-right sequence Edge itself renders, standalone tabs interleaved
  with contiguous group blocks) — all 3 pure projections of the identical snapshot, never
  independently re-fetched. `window-list` gained `chrome_layout: {tabs, groups, order} | null` per
  window (`null` only when the extension is disconnected — honest degradation, never a guess);
  `group-list` now derives from the same computation (gained `window_id`/`collapsed`, unchanged
  pre-existing shape otherwise). New `_correlate_cdp_targets()` bridges the two genuinely separate
  identifier systems (CDP `target_id` vs. real `chrome.tabs.Tab.id`) by URL in left-to-right
  encounter order, deterministic even for duplicate URLs.
  New actions **`tab-move`** (`chrome.tabs.move` — `index`/`before_tab_id`/`after_tab_id`,
  optional cross-window `window_id`), **`group-add-tabs`** (`chrome.tabs.group({tabIds,groupId})`
  — adds to an EXISTING group, never creates one), **`group-remove-tabs`**
  (`chrome.tabs.ungroup` — removes without closing) — the same primitives a mouse drag performs.
  All 3 deliberately NOT `@require_approval`-gated, same rationale as `window-create`/`tab-create`
  (a known asymmetry with the pre-existing `group-create`/`group-update`/`group-move`, left
  unchanged, not yet resolved). 20 new tests (10 Python, 10 TypeScript), all green.
- **`window-create` gained an optional `items` field building a whole ordered tab/group layout in
  one call** (KπX request): `[{"type":"tab","url":"..."}, {"type":"group","title":"...","tabs":
  ["url1","url2"]}, ...]`, processed strictly in order, every tab landing in the SAME new window
  via its real `window_id`. Grouping resolves real `chrome.tabs.Tab.id`s (never a CDP `target_id`,
  which `chrome.tabs.group` cannot accept) using the same mechanism `browser-get-new-tab` already
  exposes standalone (`tab.capture_next`, armed concurrently with the CDP tab creation via
  `_create_window_tab()`); fails closed with `EXTENSION_UNAVAILABLE` if a real id cannot be
  captured. Items bypass `tab-create`'s/`group-create`'s own individual approval gates (the whole
  layout is one deliberate command, matching `window-create`'s own approval-free status).
  **Live-verified end-to-end**: one call created a window with an initial Wikipedia tab plus a real
  Edge tab group (confirmed via a genuine `chrome.tabs.group` numeric `group_id`) containing 2
  more tabs — window-list correctly reported all 3 tabs under the one real window afterward.
  Documented, not silently papered over: a failure partway through `items` is **not atomic** —
  whatever was already created (window, prior tabs) stays in place (observed live once, during a
  daemon restart mid-test, before a clean successful retry).
- **`window-create` is no longer `@require_approval`-gated (KπX directive):** every managed Edge
  window is already always real and visible (never headless), so opening one is directly
  observable the instant it happens — there is no hidden side effect for an approval overlay to
  meaningfully gate. Still preflight-`profile` and verify-`url`. `tab-create` (which opens content
  inside an existing window, less immediately visible) keeps its approval gate unchanged.
- **Fixed `window-list` being byte-identical to `tab-list` — no window grouping at all:**
  verified live that 2 real tabs share the exact same `browserContextId` while genuinely sitting in
  the same window, proving `browserContextId` identifies the profile context, not a window; with
  multiple real Edge windows open, the old flat implementation could never answer "which tab is in
  which window." `window-list` now calls `Browser.getWindowForTarget` once per tab
  (`_window_id_for_target()`) and groups by the real returned `windowId`:
  `{"windows": [{"window_id", "bounds", "tabs": [...]}]}` — never a flat target list. Extracted the
  page-target filter itself (previously hand-duplicated 4 times across `window-list`/`tab-list`/
  `page-list`/`workspace-list`) into one shared `_page_targets()` helper; `tab-list` no longer calls
  `_window_list()` internally and stays flat, independent of the new grouping.
- **Profile lifecycle refonte — one canonical module, a real removal action, a collision guard:**
  new `src/browser_proxy/profile_state.py` is now the SINGLE implementation of disk-state +
  systemd-activation + real CDP-reachability probing (`describe_edge_profile()`), replacing two
  independently hand-duplicated copies that previously lived in `daemon.py` (used by
  `profile-list`/`admin status`) and `cli.py` (used by `admin edge status`, which additionally
  used to gate its CDP probe on nothing while `daemon.py`'s copy gated it on `systemd_active` — an
  undocumented behavioral drift between the two, now unified to always probe unconditionally, a
  real network round-trip being more trustworthy than trusting systemd's opinion as a proxy for
  it). `profile-list`/`admin status`/`ping` now also report `extension_connected` **per profile**
  (was previously only visible as a global aggregate); `admin edge status` gained the same field
  as a best-effort daemon RPC (`null` if the daemon is genuinely unreachable — the other 3 axes
  never depend on it).
  New **`do profile-remove`** action: stops the systemd unit if active, then trashes the profile
  directory via `trash-put` resolved by absolute path (`shutil.which` — never the interactive `rm`
  wrapper, since a systemd-spawned daemon subprocess is not guaranteed the same `$PATH` ordering),
  never a permanent `shutil.rmtree`; fails closed if the profile was never declared or if
  `trash-put` is missing. Deliberately **not** `@require_approval` — the most likely removal
  candidates (orphaned/never-initialized profiles) have no reachable extension to approve; safety
  comes from the mandatory named `@require_preflight("profile")` identity instead, the same
  admin-tier trust level as `admin edge start`/`stop`.
  New **case-insensitive collision guard** in `materialize_edge_profile()`: refuses a name that
  collides with a DIFFERENT already-declared profile only by letter case — the exact real bug that
  produced `Default` and `default` as two unrelated top-level profile directories.
- **Fixed the extension bridge answering EVERY profile with the same connection:** verified live —
  `bookmark-list` for 3 different profile names returned the byte-identical bookmark tree, because
  `ExtensionBridge` held exactly one global `_connection` regardless of how many Edge profiles had
  the extension loaded, and `_extension()`/`Daemon._approve()` never even looked at the payload's
  `profile` field. Each browser-proxy profile is a fully separate extension install (separate
  `chrome.storage.local`); the Options page now has an explicit **Browser-proxy profile** field
  (default `default`), sent in the handshake. `ExtensionBridge` keys connections by that declared
  name (`_connections: dict[str, ServerConnection]`, new `is_connected(profile)`/
  `connected_profiles()`); `bridge.request(kind, payload, profile)` and `Daemon._approve()` (which
  now extracts `payload["profile"]`, so approval overlays always appear in the correct window) both
  route exclusively to that one connection. A request for an unconnected profile fails closed by
  name: `EXTENSION_UNAVAILABLE: <profile>`. `_extension()` now echoes `"profile"` in every response
  so callers can always confirm which profile actually answered. `admin status`/
  `ping` gained `extension_connected_profiles: [...]` (precise per-profile truth) alongside the
  pre-existing `extension_connected: bool` (kept, zero information loss). New regression test
  (`test_two_profiles_stay_isolated_and_are_never_answered_by_the_wrong_extension`) proves two
  concurrently connected profiles are never cross-answered.
- **New `config.py`**: single source of truth for every magic default value and its overriding
  environment variable NAME (`ENV_*`/`*_DEFAULT`), consumed dynamically (never frozen at import
  time, so env-var overrides keep working) by `paths.py`/`daemon.py`/`bridge.py`/`cli.py`. Raised
  the terminal JSON preview threshold default `40` → **`100`** lines (`PREVIEW_LINES_DEFAULT`).
- **Fixed the root cause of perpetual extension re-pairing:** the pairing secret used to live under
  `runtime_dir()` (`$XDG_RUNTIME_DIR`, tmpfs — wiped at every logout/reboot), while the extension's
  own half of the pair (`chrome.storage.local`) genuinely persists. Every logout/reboot silently
  invalidated only the daemon's side, forcing an avoidable `admin extension pair` re-run. New
  `paths.persistent_state_dir()` (`$XDG_STATE_HOME/browser-proxy`, same durability class as the
  Edge profile root) now holds `extension.token`; `runtime_dir()` keeps only the truly ephemeral
  socket/lock. One manual re-pair is required once more after upgrading (last time).
- **Fixed the daemon dropping a live, connected extension for no functional reason:**
  `Daemon._lifecycle()`'s idle-TTL used to fire purely from "no CLI call in `idle_seconds`," even
  while an authenticated extension stayed connected and useful. The idle timer is now suspended
  entirely while `bridge.connected` is true; `max_lifetime_seconds` remains the unconditional cap.
- **Fixed the extension's reconnect loop silently dying on MV3 service-worker eviction:**
  `scheduleReconnect()`'s `setTimeout` is discarded by Chromium if the service worker is evicted
  (~30s idle) before it fires — after any daemon downtime, the extension could go permanently dark
  until an unrelated browser event happened to wake the worker. Added a `chrome.alarms`-based
  watchdog (`browser-proxy-ext`, new `"alarms"` permission) that Chromium always redelivers by
  waking the worker, guaranteeing eventual reconnection; `setTimeout` remains as a fast-path
  optimization only, no longer the sole mechanism.
- **Corrected the browser-proxy-profile vs. Edge-internal-profile confusion and made "real" vs
  "declared" homogeneous everywhere:** a browser-proxy "profile" is a whole isolated
  `--user-data-dir` (one systemd unit, one CDP port), a different level of the hierarchy than
  Edge's own internal people-profile concept (`Default`, `Profile 1`, … inside one `--user-data-dir`,
  visible at `chrome://settings/people`) — confirmed live by finding an Edge-internal `Default`
  subdirectory nested one level inside a real browser-proxy profile directory.
  `materialize_edge_profile()` was only ever a bare `mkdir` — it **declares** a directory, it does
  not make Edge treat it as initialized; `discover_edge_profiles()`/`_profile()` used to treat any
  existing directory as a genuine profile regardless. New `paths.edge_profile_state(path)` is the
  single predicate (`not_declared`/`declared`/`initialized`, keyed on Edge's own `Local State`
  marker file — verified present at the root of every real Edge-launched profile directory, absent
  from a bare `mkdir`), used identically by `profile-list`, `admin edge status`, `admin status`,
  and `_profile()` (which now fails closed with a distinct, actionable message per state instead of
  a generic CDP failure, and correctly reports the `PROFILE_UNAVAILABLE` error code — a pre-existing
  bug had it raise `ValueError`, silently downgrading the code to `VALIDATION_ERROR`).
- **Corrected profile identity:** persistent profile directories under
  `$HOME/.local/share/browser-proxy/profiles/` are now the sole inventory source. The previous
  implementation wrongly exposed the transient `Daemon.profiles` cache: its inventory disappeared
  on daemon restart and missed profiles started directly through `admin edge start`. `profile-list`,
  `admin status`, and direct-CDP action resolution now use persistent disk identity plus observed
  systemd/CDP state. First start materializes the directory before launching Edge; listing remains
  read-only. Workspaces are explicitly distinct heuristic containers within a profile.

- **Removed the headless Edge mode entirely** (KπX directive, non-negotiable): the `0.4.0` design
  introduced a headless-by-default / `--visible`-bootstrap-only split, on the assumption that
  agent-driven browser work should stay out of sight. This directly violated the 100% Transparency
  principle — KπX must always be able to see what an agent is doing inside the browser, every time,
  not just during a one-time setup window.
- Collapsed to a **single** systemd-templated unit, `browser-proxy-edge@<profile>.service`: always
  opens a real, visible window, whether started manually (`admin edge start <profile>`, now with no
  `--visible` flag — there is nothing left to toggle) or on demand by `Daemon.start_profile()`.
  Deleted `browser-proxy-edge-visible@.service` entirely; `admin edge install` now links one unit.
  `edge_launch_args()`/`edge_launch`/`_edge_unit()`/`admin edge start`/`stop`/`status` all lost their
  `visible: bool` parameter — `--no-startup-window` is never added, unconditionally.
- Removed the now-unnecessary mutual-exclusion guard in `Daemon.start_profile()` (there is only one
  unit per profile left to conflict with itself, not two variants).
- Live-verified: `admin edge start <profile>` and `do profile-start` both open a real, visible
  window with an identical deterministic CDP port and no `--no-sandbox`/`--no-startup-window`
  anywhere in the process argv.

## 0.4.0 - 2026-08-29

- **Edge lifecycle redesign**: every Microsoft Edge instance is now its own systemd-templated user
  service (`browser-proxy-edge@<profile>.service` headless, `browser-proxy-edge-visible@<profile>.service`
  bootstrap-only), never a `subprocess.Popen` owned by the daemon and never a hand-typed
  `microsoft-edge` command. `Daemon.start_profile()` now starts/reuses the systemd unit and polls a
  deterministic CDP port (`paths.edge_cdp_port()`, sha256-derived) instead of allocating a random one
  and tracking a child process; `_free_port()`/`self._processes` removed entirely.
- Fixed the extension-installation bootstrap deadlock: `window-create`/`tab-create` require
  extension-mediated approval, but the daemon always launched Edge with `--no-startup-window`, so no
  window could ever exist to load the extension into in the first place. `admin edge start <profile>
  --visible` now launches a real, visible window through the CLI itself for that one-time step —
  no raw `microsoft-edge` invocation ever required.
- Added a same-profile mutual-exclusion guard: `start_profile()` refuses with `PROFILE_UNAVAILABLE`
  (naming the exact command to run) if the visible bootstrap instance is still active for that
  profile, instead of a confusing CDP-readiness timeout from a `SingletonLock` conflict.
- New `admin edge install/start/stop/status` sub-typer (`cli.py`), new hidden `edge-launch`
  entrypoint (`os.execvp`, so systemd tracks Edge's real PID), new `paths.edge_profile_dir()` /
  `paths.edge_cdp_port()`.
- Fixed stale references to the `browser-proxy.socket` unit deleted in `c43d219` (same day as
  `v0.1.0`) but never cleaned up anywhere: `admin start`/`admin stop` tried to
  start/stop a unit that could not be found (`systemctl --user status browser-proxy.socket` →
  `could not be found`, confirmed broken live before this fix). Both now target only
  `browser-proxy.service`; `admin install`'s docstring now says explicitly it manages only the
  daemon process, not Edge.
- Removed `--no-sandbox` from the Edge launch args (found live: real Chromium
  "unsupported command-line flag" warning banner during a visible-bootstrap smoke test). Root
  cause: the new systemd units initially set `NoNewPrivileges=true`, which silently defeats Edge's
  SUID `chrome-sandbox` helper — removed from both Edge unit templates (kept on the daemon's own
  unit, unaffected) instead of papering over it with `--no-sandbox`; live-verified the SUID helper
  process is now present and no warning banner appears.
- Live-verified end-to-end on real Microsoft Edge (not simulated): visible bootstrap window opens
  with the deterministic port and no startup-window suppression, `admin edge status` correctly
  reports whichever unit variant is active plus live CDP reachability, `do profile-start` refuses
  while the visible instance is running, then succeeds via the headless unit once it is stopped,
  reusing the identical deterministic port.
- Added `tests/test_edge_lifecycle.py` (10 tests: deterministic port/dir resolution, launch-arg
  construction, unit naming, `start_profile()` systemd-start/reuse/failure paths).

## 0.3.0 - 2026-08-29

- Added a full "drive a real browser page" action surface (33 new actions, 49 total) on top of the
  existing profile/window/tab/bookmark registry: navigation (`page-navigate`, `page-reload`,
  `page-back`, `page-forward`), interaction (`page-click`, `page-hover`, `page-type`,
  `page-fill-form`, `page-select-option`, `page-scroll`), inspection (`page-evaluate`,
  `page-snapshot`, `page-screenshot`, `page-query`, `page-console-list`, `page-network-list`),
  dialogs (`page-dialog-policy`), downloads (`page-set-download-behavior`), cookies (`cookie-list`,
  `cookie-set`, `cookie-remove`), storage (`storage-local-get`, `storage-local-set`), tab-group
  mutation (`group-create`, `group-update`, `group-move`), and human-in-the-loop extension bridging
  (`browser-ask-user`, `browser-dismiss-overlays`, `browser-solve-captcha`, `browser-set-date`,
  `browser-set-combobox`, `browser-drop-file`, `browser-get-new-tab`).
- Added `CdpBrowser.page_session`, a single attached flattened CDP session used for any action that
  needs 2+ sequential page-scoped calls sharing enabled-domain state (fixing a real correctness gap
  in `page_call`'s per-call attach/detach cycle).
- Restores full parity with the retired `chrome-agent-mcp`/`browser-mcp` toolset while keeping the
  single-object payload contract, fail-closed approval for mutations, and preflight/verification
  policy model.
- Added `page_session` simulated CDP tests, a dedicated `tests/test_page_actions.py` covering
  registration and representative dispatch behavior (read-only, DOM-resolution, approval-gated,
  extension-mediated), and extended contract assertions for the new action set.

## 0.2.0 - 2026-08-21

- Completed the core action surface for Edge profiles, heuristic Workspaces, windows, groups,
  tabs, pages, and profile bookmarks while preserving the single-object payload contract.
- Documented and tested read-only raw CDP bypass versus fail-closed extension approval for every
  non-read-only method.
- Added simulated HTTP/WebSocket CDP tests, daemon lock-race and idle/lifetime lifecycle tests,
  and a source-wide rich-docstring contract test.

## 0.1.0 - 2026-08-21

- Implemented the Edge-only Unix-socket daemon, direct CDP action registry, policy enforcement, authenticated extension bridge, and user-systemd administration.
- Added isolated real-roundtrip smoke coverage plus bridge and transport tests.

## 0.1.0 — 2026-08-21

- Introduced the contract-first browser-proxy daemon/CLI architecture.
- Added the independent browser-proxy extension subproject boundary.
- Defined direct-CDP routing, profile/workspace hierarchy, and JSON-only `do` payload semantics.
- Declared Microsoft Edge as the exclusive browser and store target; profiles own Workspaces and bookmarks.
