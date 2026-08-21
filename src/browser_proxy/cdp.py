"""Provide a minimal direct CDP browser-level client.

Examples:
    >>> is_read_only_method('Target.getTargets')
    True
    >>> is_read_only_method('Target.createTarget')
    False
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast
from urllib.request import urlopen

from websockets.asyncio.client import connect


READ_ONLY_METHODS = frozenset(
    {
        "Browser.getVersion",
        "Target.getTargets",
        "Target.getTargetInfo",
        "Browser.getWindowForTarget",
        "Browser.getWindowBounds",
        "Page.getNavigationHistory",
        "Runtime.evaluate",
    }
)


def is_read_only_method(method: str) -> bool:
    """Return whether a raw CDP method belongs to the conservative read-only allowlist.

    Args:
        method: Browser-level CDP method name.

    Returns:
        ``True`` only for methods known to have no browser mutation.

    Examples:
        >>> is_read_only_method('Browser.getVersion')
        True
        >>> is_read_only_method('Browser.close')
        False
    """

    return method in READ_ONLY_METHODS


@dataclass
class CdpBrowser:
    """Represent one direct browser-level CDP endpoint.

    Args:
        port: Loopback remote-debugging port for a managed Edge profile.
        _next_id: Monotonic CDP request identifier.
        _pending: Responses received before the awaited request.

    Returns:
        A client capable of issuing browser-level CDP calls.

    Examples:
        >>> CdpBrowser(port=9222).port
        9222
        >>> CdpBrowser(port=9223)._next_id
        0
    """

    port: int
    _next_id: int = 0

    async def _browser_ws_url(self) -> str:
        """Read Edge's browser-level debugger WebSocket URL.

        Args:
            None.

        Returns:
            The browser-level ``ws://`` endpoint supplied by CDP.

        Examples:
            >>> CdpBrowser(9222)._browser_ws_url.__name__
            '_browser_ws_url'
            >>> asyncio.iscoroutinefunction(CdpBrowser._browser_ws_url)
            True
        """

        url = f"http://127.0.0.1:{self.port}/json/version"
        try:
            payload = cast(
                dict[str, Any], await asyncio.to_thread(lambda: json.load(urlopen(url, timeout=2)))
            )
        except OSError as error:
            raise RuntimeError(f"CDP_UNAVAILABLE: {self.port}") from error
        endpoint = payload.get("webSocketDebuggerUrl")
        if not isinstance(endpoint, str):
            raise RuntimeError("CDP_UNAVAILABLE: browser endpoint absent")
        return endpoint

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Issue one browser-level CDP method and return its result object.

        Args:
            method: Fully qualified CDP method, such as ``Target.getTargets``.
            params: JSON object accepted by that CDP method.

        Returns:
            The CDP ``result`` object.

        Examples:
            >>> CdpBrowser(9222).call.__name__
            'call'
            >>> asyncio.iscoroutinefunction(CdpBrowser.call)
            True
        """

        self._next_id += 1
        request_id = self._next_id
        async with connect(await self._browser_ws_url()) as websocket:
            await websocket.send(json.dumps({"id": request_id, "method": method, "params": params}))
            async for raw in _messages(websocket):
                response = json.loads(raw)
                if response.get("id") != request_id:
                    continue
                if error := response.get("error"):
                    raise RuntimeError(f"CDP_ERROR: {error.get('message', error)}")
                result = cast(dict[str, Any], response.get("result", {}))
                if not isinstance(result, dict):
                    raise RuntimeError("CDP_ERROR: non-object result")
                return result
        raise RuntimeError("CDP_UNAVAILABLE: connection closed")

    async def targets(self) -> list[dict[str, Any]]:
        """Return inspectable page targets for this Edge profile."""

        result = await self.call("Target.getTargets", {})
        infos = result.get("targetInfos", [])
        if not isinstance(infos, list):
            raise RuntimeError("CDP_ERROR: targetInfos is not a list")
        return [item for item in infos if isinstance(item, dict)]

    async def page_call(
        self, target_id: str, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Call a page-scoped CDP method through a short-lived flattened session."""

        attached = await self.call(
            "Target.attachToTarget", {"targetId": target_id, "flatten": True}
        )
        session_id = attached.get("sessionId")
        if not isinstance(session_id, str):
            raise RuntimeError("CDP_ERROR: attach did not return sessionId")
        try:
            return await self.call(
                "Target.sendMessageToTarget",
                {
                    "sessionId": session_id,
                    "message": json.dumps({"id": 1, "method": method, "params": params}),
                },
            )
        finally:
            await self.call("Target.detachFromTarget", {"sessionId": session_id})


async def _messages(websocket: Any) -> AsyncIterator[str]:
    """Yield text frames from a WebSocket with an explicit narrow type boundary.

    Args:
        websocket: A connected ``websockets`` client protocol.

    Returns:
        An async iterator of CDP JSON frames.

    Examples:
        >>> _messages.__name__
        '_messages'
        >>> hasattr(_messages, '__annotations__')
        True
    """

    async for message in websocket:
        if isinstance(message, str):
            yield message
