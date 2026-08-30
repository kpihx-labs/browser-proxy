"""Authenticated loopback WebSocket bridge for the Edge extension."""

import asyncio
import json
import secrets
from dataclasses import dataclass, field
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve

from browser_proxy.paths import extension_port, pairing_token_path, persistent_state_dir


@dataclass
class ExtensionBridge:
    """Own extension authentication and typed request/response dispatch.

    One systemd-templated Edge instance == one browser-proxy profile == one isolated extension
    install (separate ``chrome.storage.local``, separate options page). Each install declares its
    own profile identity in its handshake; connections are keyed by that name so a request for
    profile "research" is never silently answered by profile "default"'s extension — the single
    root cause of the "3 profiles returned the exact same bookmark tree" confusion.
    """

    timeout_seconds: float = 600.0
    _server: Server | None = None
    _connections: dict[str, ServerConnection] = field(default_factory=dict)
    _pending: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)

    def pair(self, token: str) -> None:
        """Purpose: persist an operator-provisioned pairing capability without exposing it in output.

        Args:
            self (ExtensionBridge): Bridge whose private runtime capability is rotated.
            token (str): Non-empty capability generated visibly in the extension options page and
                entered through the CLI's hidden terminal prompt.

        Returns:
            None: Persists the supplied mode-0600 local capability under ``persistent_state_dir()``
            — a directory that survives reboot/logout, unlike the daemon's ephemeral runtime dir.

        Examples:
            >>> callable(ExtensionBridge.pair)
            True
            >>> ExtensionBridge(timeout_seconds=1).timeout_seconds
            1
        """

        if len(token) < 16 or not token.isascii():
            raise ValueError("pairing secret must be ASCII and contain at least 16 characters")
        persistent_state_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
        path = pairing_token_path()
        path.write_text(token, encoding="utf-8")
        path.chmod(0o600)

    def _token(self) -> str:
        """Purpose: load the private extension pairing capability without implicit creation.

        Args:
            self (ExtensionBridge): Bridge whose private token path is consulted.

        Returns:
            str: Existing local capability used only for constant-time comparison, or ``""`` when
            explicit operator pairing has not happened yet.

        Examples:
            >>> callable(ExtensionBridge._token)
            True
            >>> ExtensionBridge()._server is None
            True
        """
        path = pairing_token_path()
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

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
        self._connections.clear()

    @property
    def connected(self) -> bool:
        """Purpose: report whether ANY authenticated extension is currently connected.

        Args:
            self (ExtensionBridge): Bridge whose active connections are inspected.

        Returns:
            bool: ``True`` while at least one profile's extension is connected. This is a coarse
            "is anything at all attached" glance only — use ``is_connected(profile)`` or
            ``connected_profiles()`` for the precise per-profile truth a real request depends on.

        Examples:
            >>> ExtensionBridge().connected
            False
            >>> isinstance(ExtensionBridge().connected, bool)
            True
        """

        return bool(self._connections)

    def is_connected(self, profile: str) -> bool:
        """Purpose: report whether ONE specific profile's extension is currently connected.

        Args:
            profile (str): Browser-proxy profile name declared by the extension at handshake.

        Returns:
            bool: ``True`` only when that exact profile has an authenticated connection.

        Examples:
            >>> ExtensionBridge().is_connected('default')
            False
            >>> isinstance(ExtensionBridge().is_connected('default'), bool)
            True
        """

        return profile in self._connections

    def connected_profiles(self) -> tuple[str, ...]:
        """Purpose: list every profile with a currently authenticated extension connection.

        Args:
            self (ExtensionBridge): Bridge whose active connections are inspected.

        Returns:
            tuple[str, ...]: Sorted profile names, never a bare aggregate boolean — the same
            "one predicate everywhere" discipline as ``paths.edge_profile_state()``, so
            ``admin status``/``admin doctor`` can never hide which specific profile is attached.

        Examples:
            >>> ExtensionBridge().connected_profiles()
            ()
            >>> isinstance(ExtensionBridge().connected_profiles(), tuple)
            True
        """

        return tuple(sorted(self._connections))

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

    async def request(
        self,
        kind: str,
        payload: dict[str, Any],
        profile: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Purpose: dispatch a typed request to ONE specific profile's paired extension.

        Args:
            self (ExtensionBridge): Authenticated bridge that sends the request.
            kind (str): Typed extension operation such as ``bookmark.list``.
            payload (dict[str, Any]): Complete single action object to forward unchanged.
            profile (str): Target browser-proxy profile — the request is routed exclusively to
                that profile's own connection, never to whichever extension happens to be attached.
            timeout_seconds (float | None): Per-call override for how long THIS call waits for a
                reply; ``None`` (the default, used by every non-approval typed request) keeps the
                instance-wide ``self.timeout_seconds``. ``daemon._approve()`` passes an explicit
                override here so the daemon-side wait is ALWAYS derived from the exact same
                configured HITL timeout it tells the extension to honor (see
                ``config.HITL_TIMEOUT_SECONDS_DEFAULT``) — never two independently hardcoded
                numbers that can silently drift apart (root-caused live, KπX: the daemon used to
                give up before a still-open, still-waiting-for-a-click overlay ever settled).

        Returns:
            dict[str, Any]: Typed extension reply with ``ok`` and object-valued ``data``.

        Raises:
            RuntimeError: ``EXTENSION_UNAVAILABLE: <profile>`` when that exact profile has no
                authenticated connection — even if a different profile's extension is connected.

        Examples:
            >>> asyncio.iscoroutinefunction(ExtensionBridge.request)
            True
            >>> ExtensionBridge(timeout_seconds=2).timeout_seconds
            2
        """

        connection = self._connections.get(profile)
        if connection is None:
            raise RuntimeError(f"EXTENSION_UNAVAILABLE: {profile}")
        request_id = secrets.token_urlsafe(12)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await connection.send(
            json.dumps({"type": "request", "id": request_id, "kind": kind, "payload": payload})
        )
        try:
            return await asyncio.wait_for(
                future,
                timeout=timeout_seconds if timeout_seconds is not None else self.timeout_seconds,
            )
        finally:
            self._pending.pop(request_id, None)

    async def _handle(self, connection: ServerConnection) -> None:
        """Purpose: authenticate one extension for its declared profile and route its replies.

        Args:
            self (ExtensionBridge): Bridge storing the authenticated per-profile connection.
            connection (ServerConnection): Newly accepted loopback WebSocket connection.

        Returns:
            None: Closes malformed connections and resolves matching pending requests. A second
            connection declaring the same ``profile`` replaces the first (last-connect-wins), the
            same recovery semantics as a single-profile bridge had before profiles were multiplexed.

        Examples:
            >>> asyncio.iscoroutinefunction(ExtensionBridge._handle)
            True
            >>> callable(ExtensionBridge._handle)
            True
        """

        profile = ""
        try:
            raw = await asyncio.wait_for(connection.recv(), timeout=10)
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("type") != "handshake":
                await connection.close(code=4000, reason="handshake required")
                return
            token = message.get("token")
            expected = self._token()
            if (
                not isinstance(token, str)
                or not expected
                or not secrets.compare_digest(token.encode(), expected.encode())
            ):
                await connection.close(code=4001, reason="authentication failed")
                return
            if not isinstance(message.get("extension_id"), str):
                await connection.close(code=4002, reason="extension_id required")
                return
            declared_profile = message.get("profile")
            if not isinstance(declared_profile, str) or not declared_profile:
                await connection.close(code=4004, reason="profile required")
                return
            profile = declared_profile
            self._connections[profile] = connection
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
            if profile and self._connections.get(profile) is connection:
                del self._connections[profile]
