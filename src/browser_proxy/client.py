"""Implement the tiny CLI-to-daemon Unix-socket client.

Examples:
    >>> request.__name__
    'request'
    >>> asyncio.iscoroutinefunction(request)
    True
"""

import asyncio
import json
import subprocess
import uuid
from typing import Any

from browser_proxy.models import Envelope
from browser_proxy.paths import socket_path

_DAEMON_UNIT = "browser-proxy.service"
_UNIT_SETTLING_STATES = {"activating", "deactivating"}


async def _ensure_daemon_starting() -> bool:
    """Purpose: issue ``systemctl --user start`` ONLY once the unit is not mid-transition.

    Args:
        None.

    Returns:
        bool: ``True`` once a real ``start`` was issued (the unit was genuinely settled —
        ``inactive``/``failed``/anything but ``activating``/``deactivating``); ``False`` while it
        is still transitioning, so the caller keeps waiting instead of stacking a redundant
        ``start``. Root-caused live (KπX): the previous unconditional ``start`` on every connection
        failure raced an in-flight ``admin stop`` teardown — a second, genuinely new daemon process
        could win the startup lock right as the OLD process was still unwinding its own listening
        socket, leaving the socket PATH pointing at an orphaned inode nobody was actually listening
        on (``ECONNREFUSED`` despite ``systemctl status`` reporting the unit "active").

    Examples:
        >>> asyncio.iscoroutinefunction(_ensure_daemon_starting)
        True
        >>> callable(_ensure_daemon_starting)
        True
    """
    state = await asyncio.to_thread(
        subprocess.run,
        ["systemctl", "--user", "is-active", _DAEMON_UNIT],
        capture_output=True,
        text=True,
        check=False,
    )
    if state.stdout.strip() in _UNIT_SETTLING_STATES:
        return False
    await asyncio.to_thread(
        subprocess.run, ["systemctl", "--user", "start", _DAEMON_UNIT], check=False
    )
    return True


async def request(method: str, params: dict[str, Any]) -> Envelope:
    """Purpose: send one request to the socket-activated daemon and parse its envelope.

    Args:
        method (str): Daemon method name.
        params (dict[str, Any]): JSON object for that method.

    Returns:
        The daemon's validated response envelope.

    Examples:
        >>> asyncio.iscoroutinefunction(request)
        True
        >>> request.__name__
        'request'
    """

    try:
        reader, writer = await asyncio.open_unix_connection(str(socket_path()))
    except OSError:
        started = False
        for _ in range(30):
            if not started:
                started = await _ensure_daemon_starting()
            await asyncio.sleep(0.1)
            try:
                reader, writer = await asyncio.open_unix_connection(str(socket_path()))
                break
            except OSError:
                continue
        else:
            return Envelope.error(
                "DAEMON_UNAVAILABLE", message="systemd daemon did not become ready"
            )
    writer.write(
        (json.dumps({"id": str(uuid.uuid4()), "method": method, "params": params}) + "\n").encode()
    )
    await writer.drain()
    response = await reader.readline()
    writer.close()
    await writer.wait_closed()
    return Envelope.model_validate_json(response)
