"""Resolve XDG-safe runtime locations without hardcoded user paths.

Examples:
    >>> runtime_dir().name == 'browser-proxy'
    True
    >>> socket_path().name
    'browser-proxy.sock'
"""

import hashlib
import os
from pathlib import Path

from browser_proxy import config


def runtime_dir() -> Path:
    """Purpose: return the daemon's EPHEMERAL runtime directory (socket, lock only).

    Args:
        None.

    Returns:
        The configured test/runtime directory ending in ``browser-proxy``. This directory is
        allowed to be volatile (``$XDG_RUNTIME_DIR`` is a tmpfs wiped at logout/reboot) because
        nothing placed here needs to survive past the current login session. Anything that must
        survive reboot/logout (the extension pairing secret, Edge profiles) belongs in
        ``persistent_state_dir()``/``edge_profile_root()`` instead — never here.

    Examples:
        >>> runtime_dir().name
        'browser-proxy'
        >>> runtime_dir().is_absolute()
        True
    """

    if override := os.environ.get(config.ENV_STATE_DIR):
        return Path(override)
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "browser-proxy"


def persistent_state_dir() -> Path:
    """Purpose: return the PERSISTENT per-user state directory, surviving reboot and logout.

    Args:
        None: Reads the optional ``BROWSER_PROXY_PERSISTENT_STATE_DIR`` environment value.

    Returns:
        Path: Configured or default persistent state root, creating no files by itself. Unlike
        ``runtime_dir()``, this is never ``$XDG_RUNTIME_DIR`` (tmpfs) — it defaults to
        ``$XDG_STATE_HOME/browser-proxy`` (or ``~/.local/state/browser-proxy``), the same
        durability class as ``edge_profile_root()``.

    Examples:
        >>> persistent_state_dir().name
        'browser-proxy'
        >>> persistent_state_dir().is_absolute()
        True
    """

    if override := os.environ.get(config.ENV_PERSISTENT_STATE_DIR):
        return Path(override)
    xdg_state_home = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))
    return Path(xdg_state_home) / "browser-proxy"


def socket_path() -> Path:
    """Purpose: return the local Unix-domain socket path.

    Args:
        None.

    Returns:
        The socket path beneath the daemon runtime directory.

    Examples:
        >>> socket_path().suffix
        '.sock'
        >>> socket_path().parent == runtime_dir()
        True
    """

    return runtime_dir() / "browser-proxy.sock"


def lock_path() -> Path:
    """Purpose: return the exclusive daemon ownership lock path.

    Args:
        None.

    Returns:
        The lock path beneath the daemon runtime directory.

    Examples:
        >>> lock_path().name
        'browser-proxy.lock'
        >>> lock_path().parent == runtime_dir()
        True
    """

    return runtime_dir() / "browser-proxy.lock"


def pairing_token_path() -> Path:
    """Purpose: return the private extension-pairing capability path.

    Args:
        None: Reads only configured persistent-state path settings.

    Returns:
        Path: Protected local path reserved for the pairing capability, under
        ``persistent_state_dir()`` — NEVER ``runtime_dir()``. A pairing secret placed in
        ``$XDG_RUNTIME_DIR`` (tmpfs) is silently wiped at every logout/reboot, forcing a repeated,
        avoidable ``admin extension pair`` even though the extension's own stored secret survives
        (confirmed root cause of the "must perpetually re-pair" complaint).

    Examples:
        >>> pairing_token_path().name
        'extension.token'
        >>> pairing_token_path().parent == persistent_state_dir()
        True
    """

    return persistent_state_dir() / "extension.token"


def extension_port() -> int:
    """Purpose: return the loopback extension bridge port with test overrides.

    Args:
        None: Reads the optional ``BROWSER_PROXY_EXTENSION_PORT`` environment value.

    Returns:
        int: Configured loopback TCP port for the authenticated extension bridge.

    Examples:
        >>> isinstance(extension_port(), int)
        True
        >>> extension_port() > 0
        True
    """

    return int(os.environ.get(config.ENV_EXTENSION_PORT, str(config.EXTENSION_PORT_DEFAULT)))


def edge_profile_root() -> Path:
    """Purpose: return the parent directory holding every persistent Edge profile.

    Args:
        None: Reads the optional ``BROWSER_PROXY_PROFILE_ROOT`` environment value.

    Returns:
        Path: Configured or default profile root, creating no files by itself.

    Examples:
        >>> edge_profile_root().name
        'profiles'
        >>> edge_profile_root().is_absolute()
        True
    """

    override = os.environ.get(config.ENV_PROFILE_ROOT)
    if override:
        return Path(override)
    return Path.home() / config.PROFILE_ROOT_RELATIVE


def edge_profile_dir(name: str) -> Path:
    """Purpose: return one named persistent Edge profile's user-data directory.

    Args:
        name (str): Safe persistent Edge profile name, never a path traversal.

    Returns:
        Path: The profile's dedicated ``--user-data-dir`` beneath the profile root.

    Examples:
        >>> edge_profile_dir('test').name
        'test'
        >>> edge_profile_dir('test').parent == edge_profile_root()
        True
    """

    validate_profile_name(name)
    return edge_profile_root() / name


def validate_profile_name(name: str) -> str:
    """Purpose: validate one persistent profile identity without filesystem side effects.

    Args:
        name (str): Candidate profile name, never a filesystem path.

    Returns:
        str: The unchanged validated profile name.

    Raises:
        ValueError: If the name is empty, reserved, or attempts path traversal.

    Examples:
        >>> validate_profile_name('default')
        'default'
        >>> validate_profile_name('../outside')
        Traceback (most recent call last):
        ...
        ValueError: invalid profile name
    """

    if not name or name in {".", ".."} or any(part in name for part in ("/", "\\", "..")):
        raise ValueError("invalid profile name")
    return name


def discover_edge_profiles() -> tuple[Path, ...]:
    """Purpose: discover persistent managed Edge profile directories from disk.

    Args:
        None: Reads the profile root without creating it.

    Returns:
        tuple[Path, ...]: Sorted non-symlink profile directories; an absent root is empty.

    Examples:
        >>> isinstance(discover_edge_profiles(), tuple)
        True
        >>> all(path.parent == edge_profile_root() for path in discover_edge_profiles())
        True
    """

    root = edge_profile_root()
    if not root.is_dir():
        return ()
    return tuple(sorted(path for path in root.iterdir() if path.is_dir() and not path.is_symlink()))


def materialize_edge_profile(name: str) -> Path:
    """Purpose: DECLARE one persistent Edge profile directory before its first launch.

    Args:
        name (str): Validated profile identity.

    Returns:
        Path: The durable directory reserved as Edge's future ``--user-data-dir``. This only
        creates an empty directory — it does NOT make Edge treat it as an initialized profile.
        Edge itself stamps a directory as genuinely initialized only once it actually boots
        against it, by writing ``config.EDGE_PROFILE_MARKER_FILENAME`` at its root (verified live:
        every real browser-proxy profile has this file; a directory `mkdir`'d here but never
        actually launched does not). Use ``edge_profile_state()`` to tell the two apart.

    Raises:
        ValueError: If ``name`` collides case-insensitively with a DIFFERENT already-declared
            profile (e.g. ``Default`` vs ``default``) — the filesystem is case-sensitive but
            humans routinely are not; this exact confusion previously produced two unrelated
            top-level profile directories nobody intended to create separately.

    Examples:
        >>> materialize_edge_profile('example').name
        'example'
        >>> materialize_edge_profile('example').is_dir()
        True
    """

    profile_dir = edge_profile_dir(name)
    for existing in discover_edge_profiles():
        if existing.name != name and existing.name.casefold() == name.casefold():
            raise ValueError(
                f"profile name {name!r} collides case-insensitively with existing profile "
                f"{existing.name!r} — choose a visually distinct name"
            )
    profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return profile_dir


def edge_profile_state(path: Path) -> str:
    """Purpose: classify one profile path with the single authoritative real/declared predicate.

    Args:
        path (Path): Candidate persistent Edge profile directory, typically ``edge_profile_dir(name)``.

    Returns:
        str: ``"not_declared"`` when the path is not a real directory (never `materialize`d);
        ``"declared"`` when browser-proxy created the directory but Edge has never actually booted
        against it (no marker file yet — `admin edge start` may have failed after `mkdir`, or
        `profile-start` has not run yet); ``"initialized"`` when Edge has genuinely started there
        at least once (``config.EDGE_PROFILE_MARKER_FILENAME`` present at the directory root). This
        is the ONE predicate used identically by ``profile-list``, ``admin edge status``,
        ``admin status``, and ``_profile()`` — never a different ad hoc check in each place.

    Examples:
        >>> import tempfile
        >>> edge_profile_state(Path(tempfile.gettempdir()) / 'does-not-exist-xyz')
        'not_declared'
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     edge_profile_state(Path(tmp))
        'declared'
    """

    if not path.is_dir() or path.is_symlink():
        return "not_declared"
    if (path / config.EDGE_PROFILE_MARKER_FILENAME).is_file():
        return "initialized"
    return "declared"


def saved_windows_path(profile: str) -> Path:
    """Purpose: return one profile's persisted saved-window snapshots file.

    Args:
        profile (str): Safe browser-proxy profile name — one file per profile, never shared.

    Returns:
        Path: ``<persistent_state_dir>/saved-windows/<profile>.json`` — the SAME durability class
        as the extension pairing secret and the Edge profile root (survives reboot/logout), never
        ``runtime_dir()`` (tmpfs). This is browser-proxy's OWN local "workspace" substitute (KπX,
        GRAVÉ): real Edge Workspaces expose no public CDP or extension API at all (confirmed
        live — see ``## Workspaces`` in ``CONTRACT.md``), so `window-save`/`window-restore` build
        an honest, fully-owned equivalent instead of pretending to read what cannot be read.

    Examples:
        >>> saved_windows_path('default').name
        'default.json'
        >>> saved_windows_path('default').parent.name
        'saved-windows'
    """

    validate_profile_name(profile)
    return persistent_state_dir() / "saved-windows" / f"{profile}.json"


def edge_cdp_port(name: str) -> int:
    """Purpose: derive a stable loopback CDP port for one named Edge profile.

    Args:
        name (str): Safe persistent Edge profile name used as the hash seed.

    Returns:
        int: Deterministic port in ``33000..41999``, so a systemd-managed Edge
        instance and the daemon always agree on where to find it without any
        inter-process handoff. Overridable via ``BROWSER_PROXY_EDGE_PORT`` for tests.

    Examples:
        >>> edge_cdp_port('test') == edge_cdp_port('test')
        True
        >>> 33000 <= edge_cdp_port('test') < 42000
        True
    """

    override = os.environ.get(config.ENV_EDGE_PORT)
    if override:
        return int(override)
    digest = hashlib.sha256(name.encode()).hexdigest()
    return config.EDGE_PORT_RANGE_START + (int(digest[:8], 16) % config.EDGE_PORT_RANGE_SIZE)
