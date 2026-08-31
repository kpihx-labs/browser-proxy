"""Provide a minimal direct CDP browser-level client.

Examples:
    >>> is_read_only_method('Target.getTargets')
    True
    >>> is_read_only_method('Target.createTarget')
    False
"""

import asyncio
import json
import os
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast
from urllib.request import urlopen

from websockets.asyncio.client import connect

from browser_proxy import config


def _cdp_max_frame_bytes() -> int:
    """Purpose: resolve the configured per-frame CDP WebSocket ceiling at call time.

    Args:
        None: Reads the optional ``BROWSER_PROXY_CDP_MAX_FRAME_BYTES`` environment value.

    Returns:
        int: The maximum allowed single WebSocket frame in bytes, always at least 1 so a
        misconfigured override can never silently disable the bound entirely.

    Examples:
        >>> _cdp_max_frame_bytes() > 0
        True
        >>> isinstance(_cdp_max_frame_bytes(), int)
        True
    """
    raw = os.environ.get(config.ENV_CDP_MAX_FRAME_BYTES, str(config.CDP_MAX_FRAME_BYTES_DEFAULT))
    return max(1, int(raw))


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
        profile: The managed profile name (used for auto-recovery).
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
    profile: str = ""
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

    async def _with_retry(self, coro_func: Callable[[], Awaitable[Any]]) -> Any:
        """Purpose: Wrap a CDP call with auto-recovery logic.

        Args:
            coro_func (Callable): The CDP execution coroutine.

        Returns:
            The result of the CDP execution.

        Examples:
            >>> CdpBrowser(9222)._with_retry.__name__
            '_with_retry'
            >>> asyncio.iscoroutinefunction(CdpBrowser._with_retry)
            True
        """
        start = time.monotonic()
        last_err = None
        while time.monotonic() - start < 3.0:
            try:
                return await coro_func()
            except Exception as e:
                if "CDP_UNAVAILABLE" in str(e) or isinstance(e, OSError):
                    last_err = e
                    if self.profile:
                        subprocess.run(
                            [
                                "systemctl",
                                "--user",
                                "start",
                                f"browser-proxy-profile@{self.profile}.service",
                            ],
                            check=False,
                        )
                    await asyncio.sleep(0.5)
                else:
                    raise
        raise RuntimeError(f"CDP_UNAVAILABLE: failed after 3s retries (last error: {last_err})")

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

        async def _inner() -> dict[str, Any]:
            """Purpose: run actual logic.
            Args: None.
            Returns: dict.
            Examples:
                >>> True
                True
                >>> False
                False
            """
            self._next_id += 1
            request_id = self._next_id
            async with connect(
                await self._browser_ws_url(), max_size=_cdp_max_frame_bytes()
            ) as websocket:
                await websocket.send(
                    json.dumps({"id": request_id, "method": method, "params": params})
                )
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

        return await self._with_retry(_inner)

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

        async def _inner() -> list[dict[str, Any]]:
            """Purpose: run inner logic.
            Args: None.
            Returns: list.
            Examples:
                >>> True
                True
                >>> False
                False
            """
            async with connect(
                await self._browser_ws_url(), max_size=_cdp_max_frame_bytes()
            ) as websocket:
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
                        resolved_params = (
                            raw_params(results) if callable(raw_params) else raw_params
                        )
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

        return await self._with_retry(_inner)


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


class ConsoleCapture:
    """Persistent per-target console capture via a long-lived attached CDP session.

    The pre-existing `page-console-list` used a per-call detached `Runtime.evaluate` override,
    which died with its session — messages emitted by an already-loaded page before the first
    read stayed permanently invisible. A script injected through
    ``Page.addScriptToEvaluateOnNewDocument`` survives page reloads ONLY while its session stays
    attached, so this class holds ONE attached flattened session open (a pump task keeps the
    websocket alive), re-installing the override before every document's own scripts run. Root
    cause of the limitation (KπX, GRAVÉ: "comment avoir les logs meme quand deja charge ?"):
    transient sessions could never install a persistent hook.
    """

    def __init__(self, port: int, target_id: str) -> None:
        """Purpose: prepare an unstarted persistent console capture for one CDP page target.

        Args:
            port (int): Loopback CDP browser port (``edge_cdp_port(profile)``).
            target_id (str): CDP page target ID whose console output this capture observes.

        Returns:
            None: The capture only connects once ``start()`` is awaited.

        Examples:
            >>> ConsoleCapture(9222, "t").target_id
            't'
            >>> ConsoleCapture(9222, "t")._port
            9222
        """
        self._port = port
        self.target_id = target_id
        self._session_id: str | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._websocket: Any = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 0

    async def start(self) -> None:
        """Purpose: connect, attach one flattened session, and install the boot override.

        Args:
            self (ConsoleCapture): Capture instance (unstarted).

        Returns:
            None: The session stays attached and the pump task keeps the websocket open until
            ``stop()`` — the injected script therefore survives reloads.

        Examples:
            >>> asyncio.iscoroutinefunction(ConsoleCapture.start)
            True
            >>> ConsoleCapture(9222, "t").target_id
            't'
        """
        self._websocket = await connect(
            await self._ws_url(), max_size=_cdp_max_frame_bytes()
        ).__aenter__()
        self._next_id += 1
        attach_id = self._next_id
        await self._websocket.send(
            json.dumps(
                {
                    "id": attach_id,
                    "method": "Target.attachToTarget",
                    "params": {"targetId": self.target_id, "flatten": True},
                }
            )
        )
        async for raw in _messages(self._websocket):
            response = json.loads(raw)
            if response.get("id") == attach_id:
                result = response.get("result", {})
                if isinstance(result, dict) and result.get("sessionId"):
                    self._session_id = str(result["sessionId"])
                break
        if not self._session_id:
            await self._websocket.__aexit__(None, None, None)
            self._websocket = None
            raise RuntimeError("CDP_ERROR: attach did not return sessionId")
        self._pump_task = asyncio.create_task(self._pump())
        # ⚠️ Edge REQUIRES Page.enable on the session BEFORE addScriptToEvaluateOnNewDocument —
        # without it the script registers (returns an identifier) but is SILENTLY dropped on the
        # next navigation: verified live with a minimal flag script (probe 4 vs 5): flag=0 across
        # navigation without Page.enable, flag=1 with it. This was the exact root cause of the
        # persistent-hook capture returning empty after reload.
        await self._send("Page.enable", {})
        await self._send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": _CONSOLE_OVERRIDE_JS},
        )
        # Install into the CURRENT document too, so live messages are captured from now.
        await self._send(
            "Runtime.evaluate",
            {"expression": _CONSOLE_OVERRIDE_JS, "returnByValue": False},
        )

    async def _ws_url(self) -> str:
        """Purpose: resolve the browser's WebSocket debugger URL (offloaded to a thread).

        Args:
            self (ConsoleCapture): Capture instance.

        Returns:
            str: ``ws://127.0.0.1:<port>/devtools/browser/<id>``.

        Examples:
            >>> asyncio.iscoroutinefunction(ConsoleCapture._ws_url)
            True
            >>> callable(ConsoleCapture._ws_url)
            True
        """
        version = await asyncio.to_thread(
            lambda: json.load(urlopen(f"http://127.0.0.1:{self._port}/json/version", timeout=5))
        )
        return cast(str, version["webSocketDebuggerUrl"])

    async def _send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Purpose: send one session-scoped CDP call and await its response.

        Args:
            self (ConsoleCapture): Capture instance.
            method (str): CDP method name.
            params (dict[str, Any]): CDP method parameters.

        Returns:
            dict[str, Any]: The method's result object.

        Examples:
            >>> asyncio.iscoroutinefunction(ConsoleCapture._send)
            True
            >>> callable(ConsoleCapture._send)
            True
        """
        self._next_id += 1
        call_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[call_id] = future
        await self._websocket.send(
            json.dumps(
                {
                    "id": call_id,
                    "sessionId": self._session_id,
                    "method": method,
                    "params": params,
                }
            )
        )
        try:
            return await asyncio.wait_for(future, timeout=30)
        finally:
            self._pending.pop(call_id, None)

    async def _pump(self) -> None:
        """Purpose: drain the websocket, resolving pending calls and discarding events.

        Args:
            self (ConsoleCapture): Capture instance.

        Returns:
            None: Runs until the websocket closes or ``stop()`` cancels the task.

        Examples:
            >>> asyncio.iscoroutinefunction(ConsoleCapture._pump)
            True
            >>> callable(ConsoleCapture._pump)
            True
        """
        try:
            async for raw in _messages(self._websocket):
                response = json.loads(raw)
                if response.get("sessionId") != self._session_id:
                    continue
                call_id = response.get("id")
                if isinstance(call_id, int) and call_id in self._pending:
                    future = self._pending[call_id]
                    if not future.done():
                        error = response.get("error")
                        if error is not None:
                            future.set_exception(
                                RuntimeError(f"CDP_ERROR: {error.get('message', error)}")
                            )
                        else:
                            future.set_result(response.get("result", {}))
        except (TimeoutError, ConnectionError, json.JSONDecodeError) as error:
            # The pump only keeps the session alive; a websocket drop at shutdown/timeout is
            # expected (the daemon stops captures in `serve()`'s `finally`). The error is
            # deliberately surfaced nowhere: pending calls get their own timeout from `_send`.
            del error

    async def entries(self, clear: bool = False) -> list[dict[str, Any]]:
        """Purpose: read (and optionally wipe) the per-page console buffer.

        Args:
            self (ConsoleCapture): Capture instance.
            clear (bool): Whether the buffer is emptied after reading.

        Returns:
            list[dict[str, Any]]: Captured message records from this target's page, in order.

        Examples:
            >>> asyncio.iscoroutinefunction(ConsoleCapture.entries)
            True
            >>> callable(ConsoleCapture.entries)
            True
        """
        result = await self._send(
            "Runtime.evaluate",
            {
                "expression": (
                    "(() => { const m = window.__browserProxyConsole?.slice() ?? []; "
                    f"if ({json.dumps(clear)}) {{ window.__browserProxyConsole.length = 0; }} "
                    "return m; })()"
                ),
                "returnByValue": True,
            },
        )
        value = result.get("result", {}).get("value", [])
        return list(value) if isinstance(value, list) else []

    async def stop(self) -> None:
        """Purpose: detach the session, close the websocket, and cancel the pump task.

        Args:
            self (ConsoleCapture): Capture instance.

        Returns:
            None: Idempotent; safe to call more than once.

        Examples:
            >>> asyncio.iscoroutinefunction(ConsoleCapture.stop)
            True
            >>> callable(ConsoleCapture.stop)
            True
        """
        if self._pump_task is not None:
            self._pump_task.cancel()
            try:
                await asyncio.gather(self._pump_task, return_exceptions=True)
            finally:
                self._pump_task = None
        if self._websocket is not None:
            if self._session_id:
                try:
                    await self._send("Target.detachFromTarget", {"sessionId": self._session_id})
                except (TimeoutError, ConnectionError, RuntimeError):
                    pass  # best-effort teardown — the websocket is closed right after anyway
            self._session_id = None
            try:
                await self._websocket.__aexit__(None, None, None)
            except (TimeoutError, ConnectionError, RuntimeError):
                pass  # best-effort teardown — already closing/closed are fine
            self._websocket = None


_CONSOLE_OVERRIDE_JS = r"""
(() => {
  if (window.__browserProxyConsole) return;
  window.__browserProxyConsole = [];
  const BUFFER = window.__browserProxyConsole;
  const MAX = 2000;
  for (const level of ["log", "warn", "error", "info", "debug"]) {
    const original = console[level]?.bind(console);
    console[level] = (...args) => {
      try { BUFFER.push({ level, args: args.map(String), ts: Date.now() }); } catch {}
      if (BUFFER.length > MAX) BUFFER.splice(0, BUFFER.length - MAX);
      if (typeof original === "function") original(...args);
    };
  }
})();"""
"""Boot-time console override injected via ``Page.addScriptToEvaluateOnNewDocument`` — runs
before every document's own scripts (hence capturing the FULL load sequence, not only
post-install messages), wraps the real ``console.*`` methods (kept in passthrough), appends
structured records to ``window.__browserProxyConsole`` (bounded at 2000), and is idempotent
(guarded by the buffer's own existence)."""
