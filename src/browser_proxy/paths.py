"""Resolve XDG-safe runtime locations without hardcoded user paths.

Examples:
    >>> runtime_dir().name == 'browser-proxy'
    True
    >>> socket_path().name
    'browser-proxy.sock'
"""

import os
from pathlib import Path


def runtime_dir() -> Path:
    """Return the daemon state directory, creating no files by itself.

    Args:
        None.

    Returns:
        The configured test/runtime directory ending in ``browser-proxy``.

    Examples:
        >>> runtime_dir().name
        'browser-proxy'
        >>> runtime_dir().is_absolute()
        True
    """

    if override := os.environ.get("BROWSER_PROXY_STATE_DIR"):
        return Path(override)
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "browser-proxy"


def socket_path() -> Path:
    """Return the local Unix-domain socket path.

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
    """Return the exclusive daemon ownership lock path.

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
    """Return the private extension-pairing capability path."""

    return runtime_dir() / "extension.token"


def extension_port() -> int:
    """Return the loopback extension bridge port, allowing isolated test overrides."""

    return int(os.environ.get("BROWSER_PROXY_EXTENSION_PORT", "37291"))
