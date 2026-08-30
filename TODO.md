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
