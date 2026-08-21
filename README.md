# browser-proxy

`browser-proxy` is a local-first, profile-aware **Microsoft Edge-only** automation CLI. It replaces per-agent browser MCP processes with one socket-activated daemon and a KπX-owned Edge extension bridge.

## Contract

The public interface is deliberately identical in philosophy to `tick-proxy`:

```text
browser-proxy do <flat-domain-first-action> '<json>' [-o FILE] [-f json|table]
browser-proxy admin <command>
```

The positional payload is always one JSON object or a path to a JSON object. Options never carry business data.

```bash
browser-proxy do profile-list '{}'
browser-proxy do window-create '{"profile":"kpihx-labs","url":"https://example.com"}'
browser-proxy do raw '{"method":"Target.getTargets","params":{}}'
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

`browser-proxyd` owns profile process lifecycle and target/window state. The extension provides approval overlays, secret-safe user input, Edge tab-group operations, and difficult-widget fallback. Edge Workspaces are modeled as semantic containers inside profiles because Edge provides no documented Workspace API to CDP or extensions.

The implemented action registry covers `profile-list`, `profile-start`, window and tab discovery/creation/activation/closure, direct CDP page discovery/metadata, `workspace-list` (explicitly heuristic), extension-backed bookmarks, and a conservative read-only `raw` CDP allowlist. Mutations are never unlocked by payload flags: they require a typed approval reply from the paired extension.

## Lifecycle

`browser-proxy.socket` activates `browser-proxy.service` on the first CLI request. The daemon owns an exclusive lock, uses a Unix-domain socket, tracks work activity, stops after an idle TTL, and has a hard lifetime cap. Edge starts only when an action needs a selected profile.

## Development

```bash
make install-dev
make check
make smoke
make stress
```

## Extension

`browser-proxy-ext` is an independent repository and Git submodule. Build it with its own Makefile; its compiled package is submitted only to Microsoft Edge Add-ons.

`browser-proxy admin extension pair` rotates a mode-0600 local capability without displaying it. The extension bridge only accepts an authenticated typed `handshake` and dispatches typed request/reply frames over loopback.

## Security

- CDP endpoints bind to loopback only.
- The CLI uses a per-user Unix socket.
- The extension authenticates with a paired, short-lived capability.
- Password values and secret-bearing storage are never returned to an agent.
- `raw` defaults to a read-only CDP allowlist; mutations require an explicit approval policy.
