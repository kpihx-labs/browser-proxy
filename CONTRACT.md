# browser-proxy Contract

## Scope

The CLI is the only agent-facing **Microsoft Edge** interface. It exposes browser operations as flat JSON-RPC actions and owns lifecycle, policy, verification, and output semantics.

## Transport

CLI requests are JSON-RPC 2.0-like messages over `$XDG_RUNTIME_DIR/browser-proxy.sock`. The daemon communicates with Edge through browser-level CDP WebSockets and with the paired extension through an authenticated loopback WebSocket.

## Input

`browser-proxy do ACTION PAYLOAD` accepts exactly one JSON object or a path to a JSON object. `PAYLOAD` is passed unchanged to the action model after validation. `-o` writes the complete envelope. `-f json|table` changes only presentation.

## Output

```json
{
  "meta": {"status": "ok|approved|rejected|error", "comment": "", "edited": false},
  "data": {}
}
```

Verification belongs in `data.verification`. Diagnostics, review URLs, and errors are emitted to stderr only.

## Resource hierarchy

```text
Edge user-data directory → persistent profile → Edge Workspace → window → tab group → tab → frame
```

The registry addresses profiles, Workspaces, windows, tab groups, tabs, pages, and the Edge
profile bookmark tree. Only Edge profiles, windows, tab groups, tabs, frames, and the Edge
profile bookmark tree have public APIs. Edge Workspace bindings are explicitly tagged
`heuristic` and never treated as authoritative identifiers. Group metadata obtained through the
extension is likewise identified as heuristic when it is not an authoritative CDP identifier.

## Policy

- `@require_approval`: visible, editable, 600-second fail-closed human review.
- `@require_preflight`: protected identity fields are re-read before an approved mutation.
- `@require_verification`: action result is read back and checked after mutation.
- `raw`: all payload, including `method` and object-valued `params`, remains in its single action
  JSON object. Read-only methods run directly; unknown or mutating CDP methods require
  fail-closed extension approval.

## Error codes

`DAEMON_UNAVAILABLE`, `PROFILE_UNAVAILABLE`, `CDP_UNAVAILABLE`, `EXTENSION_UNAVAILABLE`, `LEASE_CONFLICT`, `APPROVAL_REQUIRED`, `APPROVAL_REJECTED`, `VALIDATION_ERROR`, and `RAW_METHOD_DENIED` are stable machine-readable failures.
