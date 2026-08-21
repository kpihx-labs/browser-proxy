# Changelog

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
