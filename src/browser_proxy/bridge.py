"""Authenticated loopback WebSocket bridge for the Edge extension."""

import asyncio
import json
import secrets
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve

from browser_proxy.paths import extension_port, pairing_token_path, runtime_dir


@dataclass
class ExtensionBridge:
    """Own extension authentication and typed request/response dispatch."""

    timeout_seconds: float = 600.0
    _server: Server | None = None
    _connection: ServerConnection | None = None
    _pending: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)

    def pair(self) -> None:
        """Purpose: create a protected pairing capability without exposing it in output.

        Args:
            self (ExtensionBridge): Bridge whose private runtime capability is rotated.

        Returns:
            None: Persists a newly generated mode-0600 local capability.

        Examples:
            >>> callable(ExtensionBridge.pair)
            True
            >>> ExtensionBridge(timeout_seconds=1).timeout_seconds
            1
        """

        runtime_dir().mkdir(parents=True, exist_ok=True)
        path = pairing_token_path()
        path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        path.chmod(0o600)

    def _token(self) -> str:
        """Purpose: load or initialize the private extension pairing capability.

        Args:
            self (ExtensionBridge): Bridge whose private token path is consulted.

        Returns:
            str: Non-empty local capability used only for constant-time comparison.

        Examples:
            >>> callable(ExtensionBridge._token)
            True
            >>> ExtensionBridge()._server is None
            True
        """
        path = pairing_token_path()
        if not path.exists():
            self.pair()
        return path.read_text(encoding="utf-8").strip()

    async def start(self) -> None:
        """Purpose: start the private loopback bridge once per daemon.

        Args:
            self (ExtensionBridge): Bridge that opens the loopback WebSocket server.

        Returns:
            None: Leaves an existing server untouched or starts a new one.

        Examples:
            >>> asyncio.iscoroutinefunction(ExtensionBridge.start)
            True
            >>> ExtensionBridge().connected
            False
        """

        if self._server is None:
            self._server = await serve(self._handle, "127.0.0.1", extension_port())

    async def stop(self) -> None:
        """Purpose: stop the bridge and fail pending extension operations.

        Args:
            self (ExtensionBridge): Bridge whose server and pending requests are closed.

        Returns:
            None: Completes outstanding requests with ``EXTENSION_UNAVAILABLE``.

        Examples:
            >>> asyncio.iscoroutinefunction(ExtensionBridge.stop)
            True
            >>> ExtensionBridge()._pending == {}
            True
        """

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("EXTENSION_UNAVAILABLE"))
        self._pending.clear()
        self._connection = None

    @property
    def connected(self) -> bool:
        """Purpose: report whether an authenticated extension is currently connected.

        Args:
            self (ExtensionBridge): Bridge whose active WebSocket is inspected.

        Returns:
            bool: ``True`` only while an authenticated extension connection exists.

        Examples:
            >>> ExtensionBridge().connected
            False
            >>> isinstance(ExtensionBridge().connected, bool)
            True
        """

        return self._connection is not None

    @property
    def port(self) -> int:
        """Purpose: return the actual bound loopback port, including test allocation.

        Args:
            self (ExtensionBridge): Bridge whose bound server socket is inspected.

        Returns:
            int: Bound port or configured port before the server starts.

        Examples:
            >>> isinstance(ExtensionBridge().port, int)
            True
            >>> ExtensionBridge().port > 0
            True
        """

        if self._server is None or not self._server.sockets:
            return extension_port()
        address = next(iter(self._server.sockets)).getsockname()
        return int(address[1])

    async def request(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Purpose: dispatch a typed request to the paired extension and await its reply.

        Args:
            self (ExtensionBridge): Authenticated bridge that sends the request.
            kind (str): Typed extension operation such as ``bookmark.list``.
            payload (dict[str, Any]): Complete single action object to forward unchanged.

        Returns:
            dict[str, Any]: Typed extension reply with ``ok`` and object-valued ``data``.

        Examples:
            >>> asyncio.iscoroutinefunction(ExtensionBridge.request)
            True
            >>> ExtensionBridge(timeout_seconds=2).timeout_seconds
            2
        """

        if self._connection is None:
            raise RuntimeError("EXTENSION_UNAVAILABLE")
        request_id = secrets.token_urlsafe(12)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._connection.send(
            json.dumps({"type": "request", "id": request_id, "kind": kind, "payload": payload})
        )
        try:
            return await asyncio.wait_for(future, timeout=self.timeout_seconds)
        finally:
            self._pending.pop(request_id, None)

    async def _handle(self, connection: ServerConnection) -> None:
        """Purpose: authenticate one extension and route its typed responses.

        Args:
            self (ExtensionBridge): Bridge storing the authenticated connection.
            connection (ServerConnection): Newly accepted loopback WebSocket connection.

        Returns:
            None: Closes malformed connections and resolves matching pending requests.

        Examples:
            >>> asyncio.iscoroutinefunction(ExtensionBridge._handle)
            True
            >>> callable(ExtensionBridge._handle)
            True
        """

        try:
            raw = await asyncio.wait_for(connection.recv(), timeout=10)
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("type") != "handshake":
                await connection.close(code=4000, reason="handshake required")
                return
            token = message.get("token")
            if not isinstance(token, str) or not secrets.compare_digest(token, self._token()):
                await connection.close(code=4001, reason="authentication failed")
                return
            if not isinstance(message.get("extension_id"), str):
                await connection.close(code=4002, reason="extension_id required")
                return
            self._connection = connection
            await connection.send(
                json.dumps({"type": "handshake", "status": "accepted", "protocol": 1})
            )
            async for raw in connection:
                reply = json.loads(raw)
                if not isinstance(reply, dict) or reply.get("type") != "response":
                    continue
                request_id = reply.get("id")
                if (
                    isinstance(request_id, str)
                    and (future := self._pending.get(request_id))
                    and not future.done()
                ):
                    future.set_result({"ok": bool(reply.get("ok")), "data": reply.get("data", {})})
        except (TimeoutError, json.JSONDecodeError):
            await connection.close(code=4003, reason="invalid bridge message")
        finally:
            if self._connection is connection:
                self._connection = None
