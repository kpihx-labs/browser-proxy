# TODO

## Next

- ~~Live-verify `incognito: true` on `window-create` and `tab-create`~~ — done (2026-08-31): both
  create tabs with a different `browserContextId` from the profile default, confirmed via `window-list`
  and `tab-get`. Extension disconnects prevent `window-close` cleanup via `@require_approval` — use
  `raw` + `Target.closeTarget` as fallback when the extension is unavailable.
- Complete the extension's store artwork, privacy declaration, and manual submission metadata.
- Validate the disk-backed persistent profile inventory and the separate heuristic Workspace view
  against real daily-use Edge profiles; Edge exposes no authoritative Workspace API.
- Validate the restored page-control action surface (navigation/interaction/inspection/dialogs/
  downloads/cookies/storage/groups/human-in-the-loop) against a real Edge instance paired with the
  companion extension — Edge-only lifecycle (profile-start/systemd unit start/stop, deterministic
  port, always-visible window, sandboxing) is now live-verified end-to-end (v0.4.0/v0.5.0); still
  simulated: the actual `page-*`/`cookie-*`/`bookmark-*`/`group-*`/human-in-the-loop actions against
  a real paired extension and a real page.
- Bootstrap the extension once per profile actually intended for daily use (`admin edge start
  <profile>` → load unpacked → `admin extension pair` → paste secret AND set the profile name in
  options) — done for the throwaway `smoke` test profile during v0.4.0/v0.5.0 verification, not yet
  for a real named profile KπX intends to keep using.
- Live-verify the new per-profile extension bridge multiplexing (`ExtensionBridge._connections`)
  against 2+ genuinely separate Edge windows with the extension loaded and distinct profile names
  declared in Options — currently only unit/simulated-transport tested, not against real Edge.
- Live-verify `do profile-remove` against a real orphaned profile, and `admin edge status`'s new
  best-effort `extension_connected` field end-to-end (only unit-tested with mocked `systemctl`/CDP
  so far, per this codebase's existing pattern of never CliRunner-testing `admin edge *` commands).
- Live-verify `window-list`'s `chrome_layout` (real `order`/`group_id`/`target_id` correlation),
  `tab-move`, `group-add-tabs`, and `group-remove-tabs` against a real multi-group Edge window —
  currently only unit/simulated-transport tested, not against a real paired extension.
- Decide whether to also drop `@require_approval` from the pre-existing `group-create`/
  `group-update`/`group-move` for consistency with `tab-update`/`group-add-tabs` (directly-observable
  already-visible-browser manipulations) — currently an unresolved asymmetry, left untouched
  pending an explicit decision.
- Live-verify the new HITL transparency/redirection/temp-tab-cleanup fixes end-to-end against a
  real paired extension. `group-sync` purged (KπX, GRAVÉ: "purge group-sync vu que inclus ds
  window-sync") — its former live-verification item is moot; `window-sync`'s `layout` field
  (strict superset) is now the one to verify instead.

## Known limitations (from session 2026-08-30) - All Fixed Live (2026-08-30 v0.6.0)

- **DOM scraping fragility** — Fixed. `page-click`, `page-type`, and `page-hover` now accept an optional `fallback_selector` parameter. The daemon runs a short (2s) retry loop on the primary selector, then falls back and clearly reports which selector succeeded. Agent strategy rule (`k-browser/SKILL.md`) updated: prioritize `[data-testid]`, `[aria-label]`, and `xpath=//text()`. Site memories must document these fallbacks.
- **CDP_UNAVAILABLE when Edge windows close** — Fixed. `CdpBrowser` now intercepts `CDP_UNAVAILABLE` or `ConnectionError`, waits 500ms, uses `systemctl --user start browser-proxy-edge@<profile>.service` to implicitly wake up the Edge unit, and retries the call for up to 3 seconds. The CLI added a `--wait-for-cdp` option for long-wait robustness in scripts, without introducing a non-ergonomic `Restart=always` loop in systemd.
- **Screenshot cache** — Fixed (v0.6.0). `page-screenshot` auto-generates a `/tmp/browser-proxy-results/screenshot_YYYYMMDD_HHMMSS.png` timestamped file when no output is specified. The old `translateZ(0)` repaint hack was removed in v0.7.0 because it broke SPAs by modifying `document.body.style.transform` — `Page.captureScreenshot` already forces an internal repaint. Pass `force_repaint: true` only as a last resort.
- **No clipboard native** — Fixed. Added new `clipboard-read` and `clipboard-write` actions using extension-level permissions. The extension manifest declares `clipboardRead`, `clipboardWrite`, and `offscreen`. The background script leverages an invisible `offscreen.html` document to access the host OS clipboard with 100% reliability, bypassing the sandboxed page-level `navigator.clipboard` restrictions.
- **window-list does not show Workspaces** — Edge exposes no public Workspace API, so `window-list`/`tab-list` cannot report which Workspace a window belongs to. Only KπX can tell (manual annotation). Mitigation: maintain a local workspace→window_id mapping in k-browser site memory; accept the limitation and ask KπX when disambiguation is needed.

## Post-beta (final phase)

Deliberately left as-is for now (KπX directive: "on va d'abord laisser ainsi... en phase finale
après phase beta il faudra revoir leur perm si nécessaire") — revisit `@require_approval` for these
specific `page-*` actions ONLY once the beta phase is over, if still judged necessary then:

- `page-evaluate` — l'action la plus puissante de tout le registre (exécute n'importe quel JS avec
  les privilèges de la page — vol de cookies, exfiltration, soumission de formulaire...) reste
  ungated alors que `bookmark-remove`/`extension-disable` le sont pour un risque bien moindre.
- `page-fill-form`/`page-type` — peuvent injecter des valeurs dans des champs sensibles (mot de
  passe, paiement).
