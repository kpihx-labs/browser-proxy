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
        """Create a fresh protected pairing capability without exposing it in output."""

        runtime_dir().mkdir(parents=True, exist_ok=True)
        path = pairing_token_path()
        path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        path.chmod(0o600)

    def _token(self) -> str:
        path = pairing_token_path()
        if not path.exists():
            self.pair()
        return path.read_text(encoding="utf-8").strip()

    async def start(self) -> None:
        """Start the private loopback bridge once per daemon."""

        if self._server is None:
            self._server = await serve(self._handle, "127.0.0.1", extension_port())

    async def stop(self) -> None:
        """Stop the bridge and fail pending extension operations."""

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
        """Whether an authenticated extension is currently connected."""

        return self._connection is not None

    @property
    def port(self) -> int:
        """Return the actual bound loopback port, including port-zero test allocation."""

        if self._server is None or not self._server.sockets:
            return extension_port()
        address = next(iter(self._server.sockets)).getsockname()
        return int(address[1])

    async def request(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a typed request to the paired extension and await its response."""

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
        """Authenticate one extension then route its typed responses."""

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
