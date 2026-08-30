"""Provide a minimal direct CDP browser-level client.

Examples:
    >>> is_read_only_method('Target.getTargets')
    True
    >>> is_read_only_method('Target.createTarget')
    False
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast
from urllib.request import urlopen

from websockets.asyncio.client import connect

CdpParams = dict[str, Any] | Callable[[list[dict[str, Any]]], dict[str, Any]]
"""One call's CDP params in a `page_session()` chain — either a plain, precomputed dict, or a
callable receiving the LIST of every earlier result in the SAME session so far and returning the
real params dict. Needed whenever a later call's params depend on an earlier call's result (e.g.
`DOM.querySelector`'s `nodeId` root, or `DOM.getBoxModel`'s target `nodeId`) — resolving such a
chain across SEPARATE `page_session()` invocations is unsafe: each invocation attaches then
detaches a brand-new CDP session, and DOM-domain `nodeId`s are scoped to the specific session that
minted them (root-caused live, KπX, GRAVÉ: `DOM.getBoxModel` on a `nodeId` obtained from an already
DETACHED session deterministically fails with `Could not find node with given id`)."""


READ_ONLY_METHODS = frozenset(
    {
        "Browser.getVersion",
        "Target.getTargets",
        "Target.getTargetInfo",
        "Browser.getWindowForTarget",
        "Browser.getWindowBounds",
        "Page.getNavigationHistory",
    }
)
"""Conservative allowlist consulted ONLY by `raw`'s dynamic approval check (`daemon.dispatch()`).
`"Runtime.evaluate"` was REMOVED (KπX, GRAVÉ: live-verified dead entry) — `CdpBrowser.call()` (what
`raw` actually uses) connects to the flat BROWSER-LEVEL CDP endpoint only, never attaching a
per-page session; `Runtime.evaluate` (like every other Page/DOM/Input/Runtime domain method)
requires a `sessionId` from `Target.attachToTarget` and simply does not exist at that level —
confirmed live: `raw {"method":"Runtime.evaluate", ...}` always fails with `CDP_ERROR: 'Runtime.
evaluate' wasn't found`, approval-gated or not. Leaving it whitelisted here was never an actual
security gap (the call could never succeed either way) — purely dead, misleading documentation of
a capability `raw` never had."""


def is_read_only_method(method: str) -> bool:
    """Purpose: identify conservative read-only raw CDP methods.

    Args:
        method (str): Browser-level CDP method name.

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
        """Purpose: read Edge's browser-level debugger WebSocket URL.

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
        """Purpose: issue one browser-level CDP method and return its result object.

        Args:
        method (str): Fully qualified CDP method, such as ``Target.getTargets``.
        params (dict[str, Any]): JSON object accepted by that CDP method.

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
        """Purpose: return inspectable CDP targets for this Edge profile.

        Args:
            self (CdpBrowser): Client bound to one managed Edge debugging port.

        Returns:
            list[dict[str, Any]]: Object-valued entries from ``Target.getTargets``.

        Examples:
            >>> CdpBrowser(9222).port
            9222
            >>> asyncio.iscoroutinefunction(CdpBrowser.targets)
            True
        """

        result = await self.call("Target.getTargets", {})
        infos = result.get("targetInfos", [])
        if not isinstance(infos, list):
            raise RuntimeError("CDP_ERROR: targetInfos is not a list")
        return [item for item in infos if isinstance(item, dict)]

    async def page_call(
        self, target_id: str, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Purpose: call a page-scoped CDP method through a flattened session.

        Args:
            self (CdpBrowser): Client bound to one managed Edge debugging port.
            target_id (str): CDP page target receiving the method.
            method (str): Page-scoped CDP method such as ``Page.getNavigationHistory``.
            params (dict[str, Any]): Object-valued parameters accepted by the method.

        Returns:
            dict[str, Any]: Result object returned by the detached page session.

        Examples:
            >>> asyncio.iscoroutinefunction(CdpBrowser.page_call)
            True
            >>> CdpBrowser(9222)._next_id
            0
        """

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

    async def page_session(
        self, target_id: str, calls: Sequence[tuple[str, CdpParams]]
    ) -> list[dict[str, Any]]:
        """Purpose: run several page-scoped CDP calls within ONE attached flattened session.

        Args:
            self (CdpBrowser): Client bound to one managed Edge debugging port.
            target_id (str): CDP page target receiving every call in this session.
            calls (Sequence[tuple[str, CdpParams]]): Ordered ``(method, params)`` pairs — ``params`` is
                either a plain dict, or a callable receiving the list of every EARLIER result in
                this SAME session so far (see ``CdpParams``) and returning the real params dict.
                Use the callable form whenever a call's params depend on an earlier call's result
                within the SAME chain (e.g. resolving a CSS selector to a `nodeId` then using that
                `nodeId` in a later `DOM.*` call) — splitting such a chain across separate
                `page_session()` invocations is a real, confirmed bug (each invocation attaches a
                brand-new session; DOM-domain `nodeId`s do not survive a detach/reattach).

        Returns:
            list[dict[str, Any]]: Result objects in the same order as ``calls``.

        Examples:
            >>> asyncio.iscoroutinefunction(CdpBrowser.page_session)
            True
            >>> CdpBrowser(9222)._next_id
            0
        """
        async with connect(await self._browser_ws_url()) as websocket:
            self._next_id += 1
            attach_id = self._next_id
            await websocket.send(
                json.dumps(
                    {
                        "id": attach_id,
                        "method": "Target.attachToTarget",
                        "params": {"targetId": target_id, "flatten": True},
                    }
                )
            )
            session_id: str | None = None
            async for raw in _messages(websocket):
                response = json.loads(raw)
                if response.get("id") == attach_id:
                    result = response.get("result", {})
                    session_id = result.get("sessionId") if isinstance(result, dict) else None
                    break
            if not session_id:
                raise RuntimeError("CDP_ERROR: attach did not return sessionId")
            results: list[dict[str, Any]] = []
            try:
                for method, raw_params in calls:
                    resolved_params = raw_params(results) if callable(raw_params) else raw_params
                    self._next_id += 1
                    call_id = self._next_id
                    await websocket.send(
                        json.dumps(
                            {
                                "id": call_id,
                                "sessionId": session_id,
                                "method": method,
                                "params": resolved_params,
                            }
                        )
                    )
                    async for raw in _messages(websocket):
                        response = json.loads(raw)
                        if (
                            response.get("id") == call_id
                            and response.get("sessionId") == session_id
                        ):
                            if error := response.get("error"):
                                raise RuntimeError(f"CDP_ERROR: {error.get('message', error)}")
                            result = response.get("result", {})
                            results.append(result if isinstance(result, dict) else {})
                            break
            finally:
                self._next_id += 1
                detach_id = self._next_id
                await websocket.send(
                    json.dumps(
                        {
                            "id": detach_id,
                            "method": "Target.detachFromTarget",
                            "params": {"sessionId": session_id},
                        }
                    )
                )
            return results


async def _messages(websocket: Any) -> AsyncIterator[str]:
    """Purpose: yield text frames from a WebSocket at a narrow type boundary.

    Args:
        websocket (Any): A connected ``websockets`` client protocol.

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
