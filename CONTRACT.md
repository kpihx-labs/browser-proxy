# browser-proxy — Architecture Contract

> **Status:** 🟢 ACTIVE — v0.5.0. Edge-only. Single binary, namespaced CLI: `do`/`admin`. 54 registered
> `do` actions (`page-list`/`page-get` purged and merged into `tab-list`/`tab-get`; `tab-move`
> renamed `tab-update`; `window-sync` added — KπX, GRAVÉ: "tab = page... je ne veux pas de
> duplication inutile"). Direct CDP is the default transport; the paired Edge extension is a
> privileged fallback + human-in-the-loop surface, multiplexed per profile (see `## Extension bridge
> identity`). Every managed Edge instance is always visible (no headless mode exists — see
> `## Edge lifecycle`). Profile lifecycle (state, systemd, CDP) is computed by ONE canonical
> function, `profile_state.describe_edge_profile()` — never duplicated per caller (see
> `## Profile lifecycle management`). Tab/group structure (order, membership, movement) is computed
> by ONE canonical function, `computeWindowLayouts()` — never a patched-on-afterward second view
> (see `## Canonical tab/group structure`); `tab-list`/`tab-get` now surface each tab's REAL
> `window_id`/`group_id`/`group_title` from that SAME computation. The daemon has deliberately NO
> automatic timeout (idle TTL or maximum lifetime) — purely lançable/arrêtable on request (KπX
> directive, see `## Daemon lifecycle`). Every HITL prompt (approval or otherwise) is shown in a
> real, FOCUSED tab — never one KπX has to discover by accident — and shows the REAL non-secret
> proposal details PLUS resolved native-id illustrations (a group's current name+tabs, a window's
> real tabs, each tab's real title/url — never opaque ids alone), with a single configurable timeout
> (`config.HITL_TIMEOUT_SECONDS_DEFAULT`, `BROWSER_PROXY_HITL_TIMEOUT_SECONDS`) shared by the daemon
> and the extension so neither can give up before the other (KπX directive: 100% transparency,
> "human readable... intuitif"; see `## HITL transparency and redirection`). `do group-sync`/
> `do window-sync` (new) reorganize a whole window's tab/group layout — create, rename, recolor,
> add-to, remove-from, and reposition — in ONE call, now BOTH `@require_approval`-gated (KπX
> directive, GRAVÉ reversal: absolute flexibility, but reviewable).

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
| `window-list` | `Target.getTargets` (filtered to `type=="page"`, via shared `_page_targets()`) then ONE `Browser.getWindowForTarget` per tab, grouped by the real returned `windowId`; plus, when the extension is connected, bridge kind `window.layout` merged in as `chrome_layout` | ❌ | Returns `{"windows": [{"window_id", "bounds", "tabs": [...], "chrome_layout": {...}|null}]}` — never a flat target list (see `## Window grouping` and `## Canonical tab/group structure`); `tab-list` reuses this SAME computation by flattening it, independent of `window-list`'s own grouped shape |
| `window-create` | `Target.createTarget` (`newWindow: true`), then `Browser.getWindowForTarget` for the real `window_id`, then (optional) `layout` — see `## Window layout` | ❌ preflight `profile` + verify `url` | Deliberately NOT approval-gated — every managed Edge window is already always real and visible (never headless), so opening one is directly observable the instant it happens; no hidden side effect for an overlay to meaningfully gate |
| `window-close` | `Target.closeTarget` (one call per `target_id`, all in ONE approval) | ✅ preflight `profile`,`target_ids` | `target_ids` is a LIST — closing several tabs/windows across the profile is ONE deliberate command with ONE approval, never N separate calls each needing its own round-trip (root-caused, KπX: too slow/tedious in practice) |
| `window-sync` | bridge kind `window.update` (bounds/state/focused) and/or bridge kind `group.sync` (layout) — see `## Window layout` | ✅ preflight `profile`,`window_id` | The window-level equivalent of `group-sync` (KπX: "très complet et flexible"): adjust an EXISTING window's own bounds/state/focus AND/OR reorganize its whole tab/group `layout`, all in one call, one approval |

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

`tab-list` now DELIBERATELY reuses `window-list`'s full computation via `_tabs_with_context()`
(KπX, GRAVÉ: "tab-list doit indiquer ds quel fenêtre est la tab, ds quel dossier c'est si c'est ds
un dossier") — it flattens `window-list`'s grouped windows back into a flat per-tab list, each tab
carrying `window_id` (which real window it lives in) and `group_id`/`group_title` (which real
group/folder it's in, both `null` if ungrouped or the extension is unavailable — an honest
degradation, never a silent guess). `tab-get` (new, replacing `page-get` — see `## Tabs`) shares the
SAME helper for a single tab, merged with the raw `Target.getTargetInfo` CDP metadata.
## Canonical tab/group structure — one real truth, three views (KπX refonte)

**Root-caused bug (fixed):** `tab-list` used to be a flat list of raw CDP targets with zero
awareness of tab groups; `group-list` was a completely separate extension-sourced view with its
own numeric ids. There was no way to answer "which tab is in which group" or "what is the real
visual order" without manually cross-referencing two disjoint views — because CDP (`Target.
getTargets`) and Chromium's own tab-group model are two genuinely disconnected systems: CDP has no
concept of tab groups or tab `index` at all (that's UI-layer state, invisible to the debugging
protocol); only `chrome.tabs.query`/`chrome.tabGroups.query` (extension-side) can answer it. Patching
group hints onto a CDP-only list after the fact could never be more than a superficial fix.

**The fix starts from Chromium's actual model, not a filesystem-folder metaphor.** A window is one
ORDERED sequence of tabs (real `index`); a group is not a container, it is a label
(`tab.groupId`) on a CONTIGUOUS run of that same sequence — Chromium enforces contiguity, there are
no nested groups. `computeWindowLayouts()` (`background.ts`) computes this ONE real truth in a
single pass (`chrome.tabs.query({})` + `chrome.tabGroups.query({})`, joined and sorted by real
`index`), and every action below only ever reads or mutates that SAME state — never a second,
independently-fetched copy:

```json
{
  "window_id": 143985169,
  "tabs": [
    {"chrome_tab_id": 111, "index": 0, "url": "...", "title": "...", "group_id": null, "active": false, "pinned": false},
    {"chrome_tab_id": 112, "index": 1, "url": "...", "title": "...", "group_id": 505967183, "active": false, "pinned": false}
  ],
  "groups": {"505967183": {"title": "Research", "color": "blue", "collapsed": false}},
  "order": [
    {"kind": "tab", "chrome_tab_id": 111},
    {"kind": "group", "group_id": 505967183, "title": "Research", "color": "blue", "collapsed": false, "tabs": [112]}
  ]
}
```

**Absolute flexibility from one source, not three independently-maintained ones:**
- **`tabs`** answers "what group is THIS tab in" directly (`group_id`, `null` if ungrouped) — flat,
  ordered by real `index`.
- **`groups`** is pure per-group metadata (title/color/collapsed), never duplicated tab data.
- **`order`** answers "what does Edge actually show, left to right" — standalone tabs interleaved
  with contiguous group blocks, each carrying its own member tab ids — the exact visual layout, not
  a reconstruction.

All three are PURE PROJECTIONS of the identical `chrome.tabs.query`/`chrome.tabGroups.query`
snapshot, computed once per call — never three sources that could drift apart.

**Where this surfaces:**
- **`window-list`** gained `chrome_layout: {tabs, groups, order} | null` per window (`window.layout`
  bridge kind, all windows fetched in one call) — `null` only when the extension is not connected
  for that profile (CDP alone cannot answer this; an honest degradation, never a silent guess). The
  pre-existing CDP-only `tabs` field is untouched (100% backward compatible); each `chrome_layout`
  tab additionally carries a best-effort `target_id` (see the ID-bridging note below).
- **`group-list`** now derives from the SAME `computeWindowLayouts()` (previously its own separate
  `chrome.tabGroups.query`+`chrome.tabs.query({groupId})` calls) and gained `window_id`/`collapsed`
  — additive, the pre-existing `id`/`title`/`color`/`tabs` shape is unchanged.

**The ID-bridging problem, solved honestly, not hidden:** CDP `target_id` (opaque devtools string)
and `chrome.tabs.Tab.id` (real stable integer) are two genuinely separate identifier systems with
NO first-class mapping between them anywhere in Chromium's public surface. `_correlate_cdp_targets()`
pairs them by matching URL in left-to-right encounter order on BOTH sides — exact when each URL is
open once, and deterministic even for duplicate URLs (Nth occurrence pairs with Nth occurrence,
never an ambiguous first-match guess); `target_id` is `null` when nothing matches. This is why the
mutation actions below (`tab-update`, `group-add-tabs`, `group-remove-tabs`) all address tabs by
their REAL `chrome_tab_id` — never a CDP `target_id`, which `chrome.tabs.*` cannot accept at all.

### Moving and regrouping tabs — the same primitives a mouse drag performs

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `tab-update` | bridge kind `tab.update` — `chrome.tabs.update` (url), `chrome.tabs.group`/`ungroup` (group_id), `chrome.tabs.move` (position/window), applied in that fixed order | ❌ preflight `tab_id` | Renamed from `tab-move` (KπX, GRAVÉ: "renomme en tab-update... url, window, folder, index... centralise vraiment tout cela pour redistribuer partout cette philo de fin ajustement"). ANY combination of `url`, `window_id`, `group_id` (`null` removes from group), and AT MOST ONE of `index`/`before_tab_id`/`after_tab_id` — at least one field beyond `tab_id` required, never a silent no-op |
| `group-add-tabs` | `chrome.tabs.group({tabIds, groupId})` | ❌ preflight `group_id` | Adds tabs to an ALREADY-CREATED group — never creates a new one (that's `group-create`) |
| `group-remove-tabs` | `chrome.tabs.ungroup` | ✅ preflight `tab_ids` | Removes tabs from their group WITHOUT closing them |
| `group-sync` | `chrome.tabs.group`/`ungroup`/`move` + `chrome.tabGroups.update`/`move` in one pass — see `## HITL transparency and redirection` | ✅ preflight `layout` | Reorganizes a WHOLE window's layout (`{"layout": [...]}}`) in ONE call — create, rename, recolor, add-to, remove-from, and reposition, all at once |

`tab-update`/`group-add-tabs` stay **NOT** `@require_approval`-gated — repositioning/regrouping an
already-visible tab is directly observable the instant it happens, the same rationale already
applied to `window-create`/`tab-create`. `group-remove-tabs` and `group-sync` are now **gated**
(KπX directive, GRAVÉ — a reversal of their original "directly observable" stance), same treatment
as `group-create`/`group-update`/`group-move`/`window-sync`: reorganizing a whole window/group's
structure is now always a deliberate, reviewable command.

### Window layout — build/reorganize a whole tab/group setup in one `layout` field

`window-create`'s (and `window-sync`'s) `layout` field is an ordered list, each entry either:

```json
{"type": "tab", "url": "https://a.example"}
{"type": "group", "title": "Research", "color": "blue", "tabs": ["https://b.example", "https://c.example"]}
```

Processed strictly in the given order — one tab, then a group of N tabs, then another tab, and so
on — every one landing in the SAME window via its real `window_id` (`Target.createTarget`'s
`windowId` parameter, resolved once via `Browser.getWindowForTarget` right after the window opens
— see `## Window grouping`). A `group` entry's tabs are created first, then `chrome.tabs.group` is
called once with their **real chrome tab ids** — never CDP `target_id` strings, which
`chrome.tabs.group` cannot accept. Those real ids are captured via the SAME extension-mediated
mechanism `browser-get-new-tab` already exposes standalone (`tab.capture_next`): the extension's
`chrome.tabs.onCreated` listener is armed concurrently with the CDP tab creation
(`_create_window_tab()`), so the daemon never guesses which numeric id belongs to which target.

Grouping therefore still requires the paired extension (fails closed with `EXTENSION_UNAVAILABLE`
if it cannot capture a real id) — plain `tab` entries never do, since they only need direct CDP.
**No individual approval per entry** for `window-create` (itself approval-free): every tab/group
created through its `layout` bypasses `tab-create`'s/`group-create`'s own standalone approval gates
too — the whole layout is one single deliberate command, not a series of separately approved ones.
`window-sync`'s own `layout` field, by contrast, IS covered by `window-sync`'s own single approval
(the whole action is gated, see `## Windows`). Response shape (`window-create`):
`{"profile", "window_id", "layout": [...]}` — `layout` is present only when the payload supplied
one.

#### Tabs

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `tab-list` | reuses `window-list`'s FULL computation, flattened per tab — see `## Window grouping` | ❌ | Each tab carries `window_id` and `group_id`/`group_title` (both `null` if ungrouped/extension unavailable) — root-caused live (KπX): a flat tab list with zero window/folder context gave no way to recognize which tab is which |
| `tab-get` | same flattened view (matched by `target_id`) merged with raw `Target.getTargetInfo` | ❌ | Replaces `page-get` (KπX, GRAVÉ: "tab = page... je ne veux pas de duplication inutile" — `page-get` was a second, narrower "get one tab's identity" action with zero window/group context; merged here as the single comprehensive source). Fails closed (`CDP_UNAVAILABLE`) for an unknown `target_id` |
| `tab-create` | `Target.createTarget` (optionally `windowId`/`newWindow`), then (only if `group_id`/position requested) bridge kind `tab.update` to place the captured real chrome tab id | ❌ preflight `profile` + verify `url` | As fine-grained as possible (KπX, GRAVÉ: "on doit être le plus fin possible... donner l'illusion d'un aspect esthétique visuel, pas juste créer du bullshit"): optional `window_id` (open in an EXISTING window — mutually exclusive with `new_window`), optional `group_id` (add to an EXISTING group the instant it's created), optional `index`/`before_tab_id`/`after_tab_id`. Deliberately NOT approval-gated, same rationale as `window-create` |
| `tab-activate` | `Target.activateTarget` | ❌ preflight `profile`,`target_id` | **No longer approval-gated** (KπX directive, GRAVÉ reversal): activating an already-open, already-visible tab is directly observable the instant it happens. Its role is purely navigational focus — bring a specific already-open tab to the front (e.g. before a screenshot/interaction, or to surface a background tab) — it never creates/closes/mutates content |
| `tab-update` | bridge kind `tab.update` — see `## Moving and regrouping tabs` | ❌ preflight `tab_id` | Renamed from `tab-move`. Addresses the REAL `chrome_tab_id`, never a CDP `target_id` |

#### Edge Workspaces — deliberately not implementable

No `workspace-*` command belongs to the public `do` surface. The former `workspace-list` was
removed: it only relabeled every CDP page target as one heuristic `ungrouped` container, so it did
not represent Edge Workspaces and would have been misleading.

This boundary is established and must not be reinvestigated unless Microsoft Edge ships a supported
API. Neither Edge CDP nor the Chrome/Edge extension APIs expose native Workspace identity,
membership, creation, restoration, or switching. The historical `WorkspacesCache` reader has no
cache to read in the current Edge profile. Current local sync artefacts expose a boolean
`workspaces.has_workspace` preference and opaque `edge_workspace-md-<uuid>` LevelDB records, but
not a documented, stable, or safely writable protocol. Native Workspace operations therefore remain
outside browser-proxy; use the visible Edge UI directly. Browser-proxy's supported hierarchy is
profiles → windows → tabs → tab groups.

#### Groups (extension-mediated — `_extension(payload, context, kind)` → `Daemon.extension_request`)

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `group-list` | bridge kind `group.list`, derived from the same `computeWindowLayouts()` as `window-list`'s `chrome_layout` — see `## Canonical tab/group structure` | ❌ | real `chrome.tabGroups` id, now also carries `window_id`/`collapsed` |
| `group-create` | bridge kind `group.create` | ✅ verify `title` | |
| `group-update` | bridge kind `group.update` | ✅ preflight `group_id` | |
| `group-move` | bridge kind `group.move` | ✅ preflight `group_id` | |
| `group-add-tabs` | bridge kind `group.add_tabs` — see `## Canonical tab/group structure` | ❌ preflight `group_id` | adds to an EXISTING group, never creates one |
| `group-remove-tabs` | bridge kind `group.remove_tabs` — see `## Canonical tab/group structure` | ✅ preflight `tab_ids` | ungroups without closing — now gated (KπX directive, GRAVÉ reversal) |
| `group-sync` | bridge kind `group.sync` — see `## Moving and regrouping tabs` | ✅ preflight `layout` | reorganizes a WHOLE window's tab/group structure in one call — now gated (KπX directive, GRAVÉ reversal) |

#### Bookmarks (extension-mediated) — filesystem-like tree, full batch flexibility

Edge bookmarks are a real folder/subfolder hierarchy (`Bookmarks bar`/`Other bookmarks`/`Mobile
bookmarks` as top-level roots, folders nestable arbitrarily deep) — this surface reveals and
mutates that REAL structure, never a flat dump, and every mutating action is batch-first: several
bookmarks/folders created, removed, or updated in ONE call, never one call per item.

| Action | Backend | Approval | Notes |
|---|---|---|---|
| `bookmark-list` | bridge kind `bookmark.list`, `chrome.bookmarks.getTree()` (or `getSubTree(root_id)` when scoped) walked into a real nested tree | ❌ | `{depth?, roots: [...]}` — the invisible super-root (`"0"`) is never itself returned, `roots` starts at its real children; each node carries `id`/`title`/`type` (`"folder"`\|`"bookmark"`)/`url`/`parent_id`/`index`, and, for folders only, a real `children` list. `depth` (optional, non-negative int) caps how many levels below the roots are included — omitted/`null` returns the full tree, unbounded; `depth:0` returns only the roots with empty `children`. `root_id` (optional, existing real folder id) scopes the WHOLE call to just that one subfolder (via `getSubTree`) instead of the top-level roots — `depth` then counts from THAT folder; `roots` stays a single-element list either way, never a special-cased singular field |
| `bookmark-get` | bridge kind `bookmark.get`, `chrome.bookmarks.get(id)` + `getSubTree(id)` (folders only, for the preview) | ❌ | read ALL available info about ONE id in one call (same philosophy as `tab-get`): `{id, title, type, url, parent_id, parent_title, index, date_added}` always, plus for a folder `{date_group_modified, children_count, children_preview:{first,last}\|null}`, or for a leaf bookmark `date_last_used` instead — never the full subtree (use `bookmark-list` with `root_id` for that) |
| `bookmark-create` | bridge kind `bookmark.create` | ✅ preflight `items` | batch: `items` is an ORDERED list of `{"type":"folder"\|"bookmark","title","url"? (bookmark only),"parent_id"? (existing real folder),"parent_ref"? (an EARLIER folder item's local `ref` in the SAME batch — mutually exclusive with `parent_id`),"ref"? (a local name later items may target),"index"?}`; a folder created earlier in the batch can be filled immediately via `parent_ref`, zero extra round trip. Not atomic (documented) — a failure partway leaves earlier creations in place |
| `bookmark-remove` | bridge kind `bookmark.remove` | ✅ preflight `ids` | batch: `ids` is a non-empty list mixing folder AND leaf bookmark identifiers freely in the SAME call — a folder id is removed WITH its whole subtree (`chrome.bookmarks.removeTree`), a leaf id alone (`chrome.bookmarks.remove`). Every id is resolved BEFORE any removal happens — an unknown id anywhere in the batch removes NOTHING (all-or-nothing identity) |
| `bookmark-update` | bridge kind `bookmark.update` | ✅ preflight `items` | batch, fine-grained: `items` is a list of `{"id","title"?,"url"? (bookmark only),"parent_id"?,"index"?}` — any subset of rename/re-url/relocate/reposition per item, at least one field beyond `id` required (a no-op item is rejected). Every id is resolved BEFORE any mutation happens — an unknown id, or a `url` given for an id that is actually a folder, rejects the WHOLE call |

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
| HITL approval timeout | `20.0` seconds (`BROWSER_PROXY_HITL_TIMEOUT_SECONDS`) | `config.HITL_TIMEOUT_SECONDS_DEFAULT` — single source of truth for BOTH the extension's own overlay-expiry alarm AND the daemon-side bridge wait (with a fixed +5s grace margin on the daemon side, see `## HITL design`); root-caused live (KπX): these used to be two independently hardcoded numbers that could silently drift apart |

### Daemon lifecycle — purely lançable/arrêtable, NO automatic timeout (KπX directive, GRAVÉ)

**Root-caused bug (fixed twice):** first, `Daemon._lifecycle()` self-stopped after an idle TTL
purely from the absence of a `do`/`admin` CLI call, force-closing a healthy authenticated extension
bridge for no functional reason; that was "fixed" by suspending the idle timer while
`bridge.connected`. But this only held while the bridge STAYED connected — the instant it dropped
for any unrelated reason (network blip, an old un-reloaded extension build, computer sleep), the
idle countdown resumed and killed the WHOLE daemon, CDP included. KπX's directive: remove the
automatic timeout entirely, not paper over it a second time. Every managed Edge window is already
always visible — if an agent leaves one open, KπX can see and close it directly; there is no case
where an unattended timeout is the right way to reclaim a daemon.

**Current design:** `Daemon()` takes no lifecycle configuration at all — no `idle_seconds`, no
`max_lifetime_seconds`, no environment variable for either (removing them was the fix, not making
them configurable). `_await_explicit_stop()` is the ONLY stop path: it blocks on `self._stop`
forever until the `shutdown` RPC sets it (`admin stop`). The systemd unit's own `RuntimeMaxSec=8h`
was removed too — that was a second, independent automatic-timeout mechanism enforcing the exact
same thing KπX rejected, just one layer lower. `Restart=on-failure`/`RestartSec=2` are unrelated
(crash resilience, never fires against a healthy daemon) and stay.

`admin stop` was ALSO fixed as part of this: it used to run only `systemctl --user stop
browser-proxy.service`, which silently no-ops for a daemon systemd never launched (e.g. `make
smoke`'s isolated test daemon) — root-caused a real hang once the idle-TTL fallback that used to
mask this bug was removed. It now sends the real `shutdown` RPC over the daemon's own Unix socket
FIRST (works identically whether systemd-managed or not), falling back to `systemctl stop` only if
the socket itself is unreachable.

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
1. Agent runs: browser-proxy do group-update '{"profile":"default","group_id":7,"title":"Setup"}'
2. dispatch() sees action.policy.approval == True
3. preflight_fields checked first (payload must already contain them, e.g. "profile")
4. _target_approval_preview(payload) — if a target_id/target_ids is present (e.g. window-close,
     tab-activate historically, storage-local-set), enrich the payload with a "context" field
     (per-window first/last-tab lines, see `windows_preview_for_targets`/`format_window_preview`)
     BEFORE approval — centralized, not a window-close-only special case
5. _approve("group-update", enriched_payload) → hitl_timeout = config.HITL_TIMEOUT_SECONDS_DEFAULT
     (20s) or $BROWSER_PROXY_HITL_TIMEOUT_SECONDS override → bridge.request("approval",
     {"action": "group-update", "payload": {...}, "timeout_seconds": hitl_timeout}, profile,
     timeout_seconds=hitl_timeout+5s grace) — routed exclusively to THAT profile's connection, so
     the overlay only ever appears in the correct Edge window, never a different profile's; the
     daemon-side wait and the extension-side alarm now derive from the SAME configured value, so
     the daemon can never give up before the extension's own overlay could still legitimately reply
     (root-caused live, KπX: a genuinely open, still-waiting overlay and an already-given-up daemon
     used to be two independently hardcoded numbers with no guaranteed relationship)
6. Extension shows a closed-shadow-root overlay in the paired Edge window: scopes + real non-secret
   `details` (raw fields, ANY array-of-strings field rendered one line per element) PLUS
   `describeNativeReferences()`'s resolved illustrations for `group_id`/`window_id`/`tab_ids`/
   `layout`/bookmark `ids`/`items[].id`/`items[].parent_id` (real title/url/name, never opaque ids
   alone) — never raw secrets —
   Approve once / Deny
7. Extension replies {"decision": "approved"|"rejected"|"timeout", "comment": "...",
     "payload": <possibly edited>} — or, if delivery itself failed (no valid tab could ever host
     the overlay), {"ok": false, "data": {"message": "Failed to show approval UI"}} with no
     "decision" field at all
8. Daemon maps the reply to ONE of 3 distinct codes — never a single generic "rejected" hiding
     WHICH of these actually happened: "rejected" → APPROVAL_REJECTED, "timeout" → APPROVAL_TIMEOUT,
     anything else (including a delivery failure or the daemon's own bridge-level timeout) →
     APPROVAL_UNAVAILABLE — each carrying the real message, never silently discarded
9. "approved" → action.handler(edited_payload, self) actually runs the real CDP/extension call
10. If action.policy.verification is set, the result is read back and checked against the payload
11. Envelope: {"meta":{"status":"ok","comment":"...","edited":true|false},"data":{...}}
```

**Fail-closed always:** no approval reply, a bridge disconnect, or a timeout are all treated as
rejection — never as an implicit approval. Root-caused live (KπX): a previous version could return
`APPROVAL_REJECTED` to the CLI BEFORE a human ever saw a genuinely delivered overlay (a technical
delivery failure indistinguishable from a real "no") — the 3-code split above exists specifically so
this is never silently misreported again.

**Human-in-the-loop actions (`browser-ask-user`, `browser-solve-captcha`, …) carry no
`@require_approval` decorator** — they are not "approved before running", they ARE the mechanism
that puts a human in the loop (the overlay itself is the action's entire purpose), so there is
nothing separate to gate.

---

## HITL transparency and redirection (KπX directive)

Two real problems, root-caused live and fixed centrally, not per-kind:

**1. "I had to notice a tab had appeared myself — I should have been redirected to it."** Every
HITL-hosting tab is now actively focused before the overlay is shown:
`background.ts`'s `focusHostTab(tabId)` calls `chrome.tabs.update(tabId, {active:true})` **and**
`chrome.windows.update(windowId, {focused:true})` — bringing both the tab AND its window to the
front. `createTemporaryHostTab()` (the last-resort tab, see below) is created with `active:true`
directly, for the same reason. Never a prompt KπX has to go discover by accident.

**2. "I need to see EXACTLY what you're proposing, e.g. the actual grouping."** The overlay used to
show only a bare action name (`describeApprovalScopes()` → `["group-create"]`). New
`describeApprovalDetails(request)` extracts the REAL fields of the gated action's own payload
(`tab_ids`, `title`, `color`, `url`, …) as human-readable lines, shown in the overlay via a new
`ShowApprovalMessage.details: string[]` field — 100% transparency of WHAT is being proposed. Only
genuinely secret-shaped fields (`value` on `cookie-set`, `content_base64` on `browser-drop-file`,
any `password`) are shown as `<redacted>` instead of leaked — never silently omitted, so the
overlay still names every field that exists.

**Centralized tab resolution, for every non-approval HITL kind too:** `sendToHostTab()`
(`background.ts`) is the ONE shared entry point `handleUserAsk`/`handleOverlayDismiss`/
`handleCaptchaSolve`/`handleFormSetDate`/`handleFormSetCombobox`/`handleFormDropFile` all go
through — the exact same tab-resolution, focus-redirect, and stale-content-script retry behavior
`requestApproval` already had, never six separately hand-duplicated copies. Root-caused live: a
found candidate tab's content script can be stale/orphaned right after THIS extension itself
reloads (`chrome.tabs.sendMessage` silently fails even though the tab looked perfectly usable);
`sendToHostTab`/`requestApproval` both ALWAYS retry once via a brand-new, focused temporary tab
(`createTemporaryHostTab()`, `https://example.com/`) whenever the first delivery attempt fails for
ANY reason — never only when no candidate tab existed at all.

**Temporary-tab leak, fixed:** the temporary host tab is always closed once the interaction settles
— on an explicit decision (`handleApprovalResponse`) or on expiry with no answer at all. The expiry
sweep is armed via `chrome.alarms` (`armApprovalExpiryAlarm`, one-shot, uniquely named per request),
never a plain `setTimeout` — root-caused live: a `setTimeout` is silently discarded if the service
worker is evicted before it fires (confirmed: a temporary `https://example.com` tab was left open
for many minutes across several subsequent turns after one approval was abandoned mid-flow, well
under the setTimeout's own 60s deadline — because the worker died first). `chrome.alarms` always
survives eviction — Chromium redelivers it by waking the worker, the same pattern already used for
the reconnect watchdog (see `## Extension bridge identity`).

**`do group-sync` — reorganize a WHOLE window in ONE call, absolute flexibility (KπX directive):**
bridge kind `group.sync`, payload `{"layout": [...]}}` — an ORDERED list processed left to right,
each entry either `{"type":"tab","tab_id":N}` (a standalone ungrouped tab at this position) or
`{"type":"group","group_id":N|omitted,"title":str,"color":str,"tab_ids":[N,...]}` (a whole group at
this position — `group_id` given reuses/renames/recolors/adds-to that EXACT existing group;
omitted creates a brand-new one). One command creates, renames, recolors, adds-to, removes-from,
AND repositions, all at once — never N separately-approved primitive calls for what is
conceptually one deliberate rearrangement. **Now `@require_approval`** (KπX directive, GRAVÉ — a
reversal from its original "directly observable" stance, same as `group-remove-tabs`): its HITL
overlay resolves every real tab id referenced ANYWHERE inside `layout` via `describeNativeReferences`
(nested-entry-aware, shared with `window-sync`'s own `layout` field — one illustration path, never
duplicated per action). `tab-update`/`group-add-tabs` remain the two exceptions that stay
approval-free — repositioning/regrouping an already-visible tab without changing a whole
window/group's STRUCTURE is still directly observable, the line KπX drew between the two.

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
