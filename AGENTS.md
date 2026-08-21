# Browser Proxy

## Purpose

`browser-proxy` is the single JSON-RPC CLI for KπX **Microsoft Edge-only** operations. It owns a socket-activated daemon, direct Edge CDP connections, profile-aware windows, bookmarks, approvals, and an extension bridge. Agents never call browser MCPs directly.

## Invariants

- Public CLI: `browser-proxy do <action> '<json>'` and `browser-proxy admin <action>`.
- `do` accepts one inline JSON object or one JSON file path; only output/help options are permitted.
- Normal browser control uses CDP; the extension is only a privileged fallback and HITL surface.
- Edge Workspace is inside a persistent Edge profile. Workspace identity is heuristic because no public Edge CDP or extension Workspace API exists.
- The extension is published only through Microsoft Edge Add-ons; no Chrome compatibility or Chrome Web Store scope exists.
- Secrets never appear in agent output, logs, autosaves, or daemon responses.
- Mutating/destructive/sensitive commands require declared policy decorators, preflight, approval, and verification as applicable.

## Structure

- `src/browser_proxy/`: Python daemon, JSON-RPC CLI, action registry, and direct CDP adapter.
- `browser-proxy-ext/`: independent TypeScript extension repository, tracked here as a git submodule.
- `systemd/`: user socket/service units; source of truth for managed lifecycle.

## Verification

Run `make check`, `make smoke`, `make stress`, then `make push`. Never bypass the Makefile.
