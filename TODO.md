# TODO

## Next

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

## Post-beta (final phase)

Deliberately left as-is for now (KπX directive: "on va d'abord laisser ainsi... en phase finale
après phase beta il faudra revoir leur perm si nécessaire") — revisit `@require_approval` for these
specific `page-*` actions ONLY once the beta phase is over, if still judged necessary then:

- `page-evaluate` — l'action la plus puissante de tout le registre (exécute n'importe quel JS avec
  les privilèges de la page — vol de cookies, exfiltration, soumission de formulaire...) reste
  ungated alors que `bookmark-remove`/`extension-disable` le sont pour un risque bien moindre.
- `page-fill-form`/`page-type` — peuvent injecter des valeurs dans des champs sensibles (mot de
  passe, paiement).
