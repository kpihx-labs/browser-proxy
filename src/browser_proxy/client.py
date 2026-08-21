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
        subprocess.run(["systemctl", "--user", "start", "browser-proxy.socket"], check=False)
        for _ in range(30):
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
