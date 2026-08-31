"""Single canonical profile-state description — the ONE place that computes disk identity,
systemd activation, and real CDP reachability for one Edge profile.

Used identically by `do profile-list` (`Daemon.profile_inventory()`), `admin profile status`
(`cli.admin_profile_status`), and `Daemon.start_profile()`. Before this module existed, the same
systemd `is-active` probe and CDP `Browser.getVersion` call were hand-duplicated in both
`daemon.py` and `cli.py` — this is the fix: exactly one implementation, imported everywhere,
so the two paths can never silently drift apart again.

Examples:
    >>> profile_unit_name('test')
    'browser-proxy-profile@test.service'
    >>> asyncio.iscoroutinefunction(describe_edge_profile)
    True
"""

import asyncio
import subprocess
from typing import Any

from browser_proxy.cdp import CdpBrowser
from browser_proxy.paths import edge_cdp_port, edge_profile_dir, edge_profile_state


def profile_unit_name(profile: str) -> str:
    """Purpose: build the templated Edge systemd unit name for one profile.

    Args:
        profile (str): Persistent Edge profile name used as the template instance.

    Returns:
        str: Fully qualified templated systemd user unit name.

    Examples:
        >>> profile_unit_name('test')
        'browser-proxy-profile@test.service'
        >>> profile_unit_name('research')
        'browser-proxy-profile@research.service'
    """

    return f"browser-proxy-profile@{profile}.service"


async def is_profile_unit_active(unit: str) -> bool:
    """Purpose: report whether a systemd-managed Edge instance is already active.

    Args:
        unit (str): Fully qualified templated systemd user unit, for example
            ``browser-proxy-profile@test.service``.

    Returns:
        bool: ``True`` only when ``systemctl --user is-active`` reports ``active``.

    Examples:
        >>> asyncio.iscoroutinefunction(is_profile_unit_active)
        True
        >>> callable(is_profile_unit_active)
        True
    """

    result = await asyncio.to_thread(
        subprocess.run,
        ["systemctl", "--user", "is-active", unit],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() == "active"


async def describe_edge_profile(name: str) -> dict[str, Any]:
    """Purpose: compute the 3 daemon-independent axes for one profile in a single canonical place.

    Args:
        name (str): Persistent Edge profile name to describe.

    Returns:
        dict[str, Any]: ``name``, ``profile_dir``, ``state`` (``not_declared``/``declared``/
        ``initialized`` — see ``paths.edge_profile_state()``), ``cdp_port``, ``systemd_active``,
        and ``cdp_reachable``. The CDP probe is attempted unconditionally (never gated on
        ``systemd_active``) — a real network round-trip is more trustworthy than trusting
        systemd's reported state as a proxy for it. Never includes ``extension_connected``: that
        4th axis lives only inside a running daemon's ``ExtensionBridge`` and is layered on top
        by ``Daemon.profile_inventory()``, since this function has zero dependency on daemon
        state and must work identically from a fresh CLI process or the daemon's own event loop.

    Examples:
        >>> asyncio.iscoroutinefunction(describe_edge_profile)
        True
        >>> isinstance(edge_cdp_port('test'), int)
        True
    """

    profile_dir = edge_profile_dir(name)
    port = edge_cdp_port(name)
    active = await is_profile_unit_active(profile_unit_name(name))
    reachable = False
    try:
        await CdpBrowser(port).call("Browser.getVersion", {})
        reachable = True
    except RuntimeError:
        reachable = False
    return {
        "name": name,
        "profile_dir": str(profile_dir),
        "state": edge_profile_state(profile_dir),
        "cdp_port": port,
        "systemd_active": active,
        "cdp_reachable": reachable,
    }
