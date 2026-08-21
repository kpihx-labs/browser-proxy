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
    """Purpose: return the daemon state directory, creating no files by itself.

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
        None: Reads only configured runtime path settings.

    Returns:
        Path: Protected local path reserved for the pairing capability.

    Examples:
        >>> pairing_token_path().name
        'extension.token'
        >>> pairing_token_path().parent == runtime_dir()
        True
    """

    return runtime_dir() / "extension.token"


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

    return int(os.environ.get("BROWSER_PROXY_EXTENSION_PORT", "37291"))
