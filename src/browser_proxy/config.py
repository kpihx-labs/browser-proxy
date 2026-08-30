"""Single source of truth for every magic value and its overriding environment variable name.

Every default constant here is read by exactly one function in ``paths.py``/``daemon.py``/
``bridge.py``/``cli.py`` at call time (never frozen at import time), so tests that
``monkeypatch.setenv(...)`` an override keep working exactly as before. Only the two names that
were already frozen module-level constants (``cli.PREVIEW_LINES``/``cli.AUTOSAVE_DIR``, both
``monkeypatch.setattr``-overridden directly in tests) stay frozen — everything else stays dynamic.

Examples:
    >>> PREVIEW_LINES_DEFAULT
    100
    >>> ENV_PREVIEW_LINES
    'BROWSER_PROXY_PREVIEW_LINES'
"""

# --- Environment variable names (never a bare string literal anywhere else) ---
ENV_STATE_DIR = "BROWSER_PROXY_STATE_DIR"
"""Overrides the ephemeral runtime directory (socket, lock) — safe to be tmpfs/volatile."""
ENV_PERSISTENT_STATE_DIR = "BROWSER_PROXY_PERSISTENT_STATE_DIR"
"""Overrides the durable per-user state directory — MUST survive reboot/logout (pairing secret)."""
ENV_EXTENSION_PORT = "BROWSER_PROXY_EXTENSION_PORT"
ENV_PROFILE_ROOT = "BROWSER_PROXY_PROFILE_ROOT"
ENV_EDGE_PORT = "BROWSER_PROXY_EDGE_PORT"
ENV_EDGE_PATH = "BROWSER_PROXY_EDGE_PATH"
ENV_AUTOSAVE_DIR = "BROWSER_PROXY_AUTOSAVE_DIR"
ENV_PREVIEW_LINES = "BROWSER_PROXY_PREVIEW_LINES"
ENV_HITL_TIMEOUT_SECONDS = "BROWSER_PROXY_HITL_TIMEOUT_SECONDS"
ENV_IPC_MAX_MESSAGE_BYTES = "BROWSER_PROXY_IPC_MAX_MESSAGE_BYTES"
ENV_CDP_MAX_FRAME_BYTES = "BROWSER_PROXY_CDP_MAX_FRAME_BYTES"

# --- Default values ---
EXTENSION_PORT_DEFAULT = 37291
"""Loopback port for the authenticated Edge-extension WebSocket bridge."""
AUTOSAVE_DIR_DEFAULT = "/tmp/browser-proxy-autosave"
PREVIEW_LINES_DEFAULT = 100
"""Terminal JSON preview threshold: results with more lines than this show N/2 head + N/2 tail."""
PREVIEW_LINES_MINIMUM = 4
EDGE_PORT_RANGE_START = 33000
EDGE_PORT_RANGE_SIZE = 9000
"""Deterministic per-profile CDP port = START + sha256(name) % SIZE, so it is always in
[EDGE_PORT_RANGE_START, EDGE_PORT_RANGE_START + EDGE_PORT_RANGE_SIZE)."""
PROFILE_ROOT_RELATIVE = ".local/share/browser-proxy/profiles"
"""Persistent Edge profile root, relative to ``Path.home()`` — never volatile."""
PERSISTENT_STATE_RELATIVE = ".local/state/browser-proxy"
"""Persistent per-user state root (pairing secret today), relative to ``Path.home()`` when
``$XDG_STATE_HOME`` is unset — never ``$XDG_RUNTIME_DIR`` (tmpfs, wiped at logout/reboot)."""

HITL_TIMEOUT_SECONDS_DEFAULT = 20.0
"""Default seconds the extension keeps ONE HITL approval overlay open before auto-expiring it
(``resolveApprovalTimeoutMs`` reads this same value from the ``timeout_seconds`` field ``_approve``
sends). ``daemon._approve()`` ALSO uses this SAME value (plus a small fixed grace margin, never a
second independently-chosen number) as the floor for how long the daemon-side bridge itself waits
for the extension's reply — single source of truth so the two can never silently diverge again
(root-caused live, KπX: a genuinely open, still-waiting-for-a-click overlay and an already-given-up
daemon-side wait used to be two independently hardcoded numbers with no guaranteed relationship)."""

TRASH_PUT_BINARY = "trash-put"
"""The KpihX-kernel trash-safe deletion binary (`trash-cli`, same tool the interactive `rm` wrapper
delegates to — see `~/.local/bin/rm`). `profile-remove` resolves and calls this binary DIRECTLY
by its absolute path (`shutil.which`), never the `rm` shell alias/wrapper — a systemd-spawned
daemon subprocess is not guaranteed to inherit the same interactive-shell `$PATH` ordering that
makes `rm` resolve to the wrapper rather than `/usr/bin/rm`. This keeps the trash guarantee correct
and transparent regardless of which process (CLI or daemon) performs the removal."""

EDGE_PROFILE_MARKER_FILENAME = "Local State"
"""Chromium writes this file at the root of a ``--user-data-dir`` the moment it actually boots
against it — verified live against 3 real browser-proxy profile directories. Its presence is the
single authoritative signal that a directory is a genuine Edge-initialized profile rather than one
merely declared/`mkdir`'d by browser-proxy itself (see ``paths.edge_profile_state``)."""

IPC_MAX_MESSAGE_BYTES_DEFAULT = 64 * 1024 * 1024
"""Ceiling for one length-prefixed client<->daemon IPC message body (see ``ipc.py``). The Unix
socket transport used to be one JSON line terminated by ``\\n``, read via ``readline()`` — bounded
by asyncio's internal ~64 KiB line buffer (``LimitOverrunError``, surfaced to users as the raw wire
message ``Separator is found, but chunk is longer than limit``). A genuinely large single-page
result (e.g. ``page-snapshot``'s full accessibility tree) routinely exceeded that ceiling before
the CLI's own preview/autosave truncation ever got a chance to run. 64 MiB is generous headroom
for any legitimate single-page CDP result while still bounding memory against a malformed or
hostile length prefix — this daemon is reachable only via a same-user Unix socket, never a network
listener, so the bound exists for correctness/memory-safety, not as a network hardening control."""

CDP_MAX_FRAME_BYTES_DEFAULT = 64 * 1024 * 1024
"""Ceiling for ONE WebSocket frame on the direct CDP connection (``CdpBrowser``), passed as
``max_size`` to every ``websockets.connect()``. Root-caused live (KπX, GRAVÉ — surfaced while
reading a large Zimbra inbox via ``page-click``): the `websockets` library default is 1 MiB
(``2**20``), and a heavy single CDP response on a complex page — e.g. ``DOM.getDocument(depth:-1)``
serializing a whole huge DOM tree — exceeds that limit, so the library raises
``websockets.exceptions.ConnectionClosedError: sent 1009 (message too big) frame exceeds limit of
1048576 bytes`` and kills the connection mid-request, surfacing to the CLI as a misleading
``IPC_ERROR: connection closed before a length prefix arrived`` (the client saw the daemon's
socket die, not the real frame-limit cause). Mirroring the IPC ceiling at 64 MiB removes the whole
class; the connection is loopback-only, so the bound exists for memory-safety, never as a network
hardening control."""
