"""Singleton Unix-socket daemon for Microsoft Edge-only automation."""

import asyncio
import os
import socket
import subprocess
from time import monotonic
from typing import Any

import shutil

from browser_proxy import config
from browser_proxy.actions import REGISTRY, validate_registry
from browser_proxy.bridge import ExtensionBridge
from browser_proxy.cdp import CdpBrowser, is_read_only_method
from browser_proxy.models import Envelope, RpcRequest
from browser_proxy.paths import (
    discover_edge_profiles,
    edge_cdp_port,
    edge_profile_dir,
    edge_profile_state,
    lock_path,
    materialize_edge_profile,
    runtime_dir,
    socket_path,
)
from browser_proxy.profile_state import describe_edge_profile, edge_unit_name, is_edge_unit_active


class Daemon:
    """Own local transport, Edge profile processes, policy, and extension bridge."""

    def __init__(
        self, idle_seconds: int | None = None, max_lifetime_seconds: int | None = None
    ) -> None:
        """Purpose: initialize daemon lifecycle, profile, and extension bridge state.

        Args:
            idle_seconds (int | None): Idle TTL override; environment default when absent.
            max_lifetime_seconds (int | None): Hard lifetime override; environment default when absent.

        Returns:
            None: Creates an unstarted daemon; persistent profile identity remains on disk.

        Examples:
            >>> Daemon(idle_seconds=5).idle_seconds
            5
            >>> Daemon(max_lifetime_seconds=10).bridge.connected
            False
        """
        self.idle_seconds = idle_seconds or int(
            os.environ.get(config.ENV_IDLE_SECONDS, str(config.IDLE_SECONDS_DEFAULT))
        )
        self.max_lifetime_seconds = max_lifetime_seconds or int(
            os.environ.get(
                config.ENV_MAX_LIFETIME_SECONDS, str(config.MAX_LIFETIME_SECONDS_DEFAULT)
            )
        )
        self._stop = asyncio.Event()
        self._last_work = monotonic()
        self.bridge = ExtensionBridge()

    async def serve(self) -> None:
        """Purpose: serve requests on systemd's or a private Unix socket until shutdown.

        Args:
            self (Daemon): Daemon instance owning lifecycle state and local transports.

        Returns:
            None: Stops only after an idle, lifetime, or explicit shutdown event.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon.serve)
            True
            >>> Daemon(idle_seconds=1).idle_seconds
            1
        """
        validate_registry()
        runtime_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self._acquire_lock():
            raise RuntimeError("DAEMON_ALREADY_RUNNING")
        local_socket = socket_path()
        server: asyncio.AbstractServer | None = None
        try:
            await self.bridge.start()
            activated = self._activated_socket()
            if activated is None:
                local_socket.unlink(missing_ok=True)
                server = await asyncio.start_unix_server(self._handle, path=str(local_socket))
                local_socket.chmod(0o600)
            else:
                server = await asyncio.start_unix_server(self._handle, sock=activated)
            await asyncio.gather(server.serve_forever(), self._lifecycle())
        except RuntimeError as error:
            if str(error) != "DAEMON_STOP":
                raise
        finally:
            if server is not None:
                server.close()
                await server.wait_closed()
            await self.bridge.stop()
            if activated is None:
                local_socket.unlink(missing_ok=True)
            lock_path().unlink(missing_ok=True)

    @staticmethod
    def _activated_socket() -> socket.socket | None:
        """Purpose: adopt systemd's first passed Unix listening socket when activated.

        Args:
            None: Reads only the standard systemd activation environment.

        Returns:
            socket.socket | None: Duplicated activated socket or ``None`` when inactive.

        Examples:
            >>> isinstance(Daemon._activated_socket(), socket.socket)
            False
            >>> callable(Daemon._activated_socket)
            True
        """
        if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
            return None
        return socket.fromfd(3, socket.AF_UNIX, socket.SOCK_STREAM)

    def _acquire_lock(self) -> bool:
        """Purpose: atomically claim daemon ownership with a mode-0600 lockfile.

        Args:
            self (Daemon): Daemon instance claiming the configured runtime lock.

        Returns:
            bool: ``True`` for the sole owner and ``False`` for an existing owner.

        Examples:
            >>> callable(Daemon._acquire_lock)
            True
            >>> isinstance(Daemon()._last_work, float)
            True
        """
        try:
            fd = os.open(lock_path(), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                owner = int(lock_path().read_text().strip())
                os.kill(owner, 0)
            except (OSError, ValueError):
                lock_path().unlink(missing_ok=True)
                return self._acquire_lock()
            return False
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True

    async def _lifecycle(self) -> None:
        """Purpose: stop after the configured idle or maximum-lifetime limit.

        Args:
            self (Daemon): Daemon whose activity timestamps, bridge connection, and stop event
                are monitored.

        Returns:
            None: Raises the internal ``DAEMON_STOP`` signal after setting stop state. The idle
            timer is suspended entirely while an authenticated extension is connected — a paired,
            idle-but-connected extension must never be force-disconnected just because no CLI
            command happened to run in the last ``idle_seconds`` (previously the daemon stopped
            regardless, silently dropping a healthy bridge connection). ``max_lifetime_seconds``
            still applies unconditionally as the hard, unavoidable ceiling.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon._lifecycle)
            True
            >>> Daemon(idle_seconds=1).idle_seconds
            1
        """
        started = monotonic()
        while not self._stop.is_set():
            await asyncio.sleep(0.2)
            lifetime_expired = monotonic() - started >= self.max_lifetime_seconds
            idle_expired = (
                not self.bridge.connected and monotonic() - self._last_work >= self.idle_seconds
            )
            if lifetime_expired or idle_expired:
                self._stop.set()
        raise RuntimeError("DAEMON_STOP")

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Purpose: handle one newline-delimited request on a local client connection.

        Args:
            self (Daemon): Daemon instance dispatching the validated request.
            reader (asyncio.StreamReader): Connected local client's byte stream.
            writer (asyncio.StreamWriter): Connected local client's response stream.

        Returns:
            None: Writes one complete JSON envelope before closing the connection.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon._handle)
            True
            >>> callable(Daemon.dispatch)
            True
        """
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=10)
            if not raw:
                raise ValueError("empty request")
            envelope = await self.dispatch(RpcRequest.model_validate_json(raw))
        except (ValueError, OSError, UnicodeDecodeError) as error:
            envelope = Envelope.error("VALIDATION_ERROR", message=str(error))
        writer.write((envelope.model_dump_json() + "\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def start_profile(self, name: str) -> int:
        """Purpose: ensure one systemd-managed Edge profile is running and CDP-ready.

        Args:
            self (Daemon): Daemon instance resolving the profile's CDP endpoint.
            name (str): Safe persistent profile directory name.

        Returns:
            int: Loopback CDP port assigned to the started or already-running profile.
            Always a real, visible Edge window — 100% Transparency, no headless
            mode exists.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon.start_profile)
            True
            >>> isinstance(Daemon().bridge.connected, bool)
            True
        """
        materialize_edge_profile(name)
        port = edge_cdp_port(name)
        unit = edge_unit_name(name)
        if not await is_edge_unit_active(unit):
            started = await asyncio.to_thread(
                subprocess.run,
                ["systemctl", "--user", "start", unit],
                capture_output=True,
                text=True,
                check=False,
            )
            if started.returncode:
                raise RuntimeError(
                    f"PROFILE_UNAVAILABLE: systemctl could not start {unit}: "
                    f"{started.stderr.strip() or 'unknown systemctl failure'}"
                )
        for _ in range(100):
            try:
                await CdpBrowser(port).call("Browser.getVersion", {})
            except RuntimeError:
                await asyncio.sleep(0.1)
                continue
            return port
        raise RuntimeError("CDP_UNAVAILABLE: Microsoft Edge CDP readiness timed out")

    async def profile_inventory(self) -> list[dict[str, Any]]:
        """Purpose: report every persistent on-disk profile with its live service AND bridge state.

        Args:
            None: Discovers profile directories; does not create or start any profile.

        Returns:
            list[dict[str, Any]]: ``profile_state.describe_edge_profile()``'s 3 daemon-independent
            axes (disk identity, systemd activation, CDP reachability) plus the one axis only a
            running daemon can know: ``extension_connected`` (this exact profile name is a key in
            ``self.bridge.connected_profiles()`` — never a global aggregate hiding which profile).

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon.profile_inventory)
            True
            >>> isinstance(Daemon().bridge.connected, bool)
            True
        """
        connected = self.bridge.connected_profiles()
        profiles: list[dict[str, Any]] = []
        for profile_dir in discover_edge_profiles():
            description = await describe_edge_profile(profile_dir.name)
            description["extension_connected"] = profile_dir.name in connected
            profiles.append(description)
        return profiles

    async def remove_profile(self, name: str) -> dict[str, Any]:
        """Purpose: stop and safely trash one persistent Edge profile — never a permanent delete.

        Args:
            self (Daemon): Daemon instance stopping the profile's systemd unit if active.
            name (str): Persistent Edge profile name to remove.

        Returns:
            dict[str, Any]: ``profile``, ``removed`` (always ``True`` on success), ``was_active``
            (whether the unit had to be stopped first), and ``trashed_path`` (the exact former
            profile directory, now living in the KpihX trash — recoverable with ``trash-restore``,
            never a ``shutil.rmtree``). Calls ``trash-put`` (the same `trash-cli` binary the
            interactive ``rm`` wrapper delegates to) by its resolved absolute path rather than
            relying on this process's ``$PATH`` containing the wrapper — a systemd-spawned daemon
            subprocess is not guaranteed the same shell ``$PATH`` ordering an interactive session has.

        Raises:
            RuntimeError: ``PROFILE_UNAVAILABLE: ...`` when the profile was never declared, or when
                ``trash-put`` is missing or fails.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon.remove_profile)
            True
            >>> isinstance(Daemon().bridge.connected, bool)
            True
        """
        profile_dir = edge_profile_dir(name)
        if edge_profile_state(profile_dir) == "not_declared":
            raise RuntimeError(f"PROFILE_UNAVAILABLE: {name} is not declared — nothing to remove")
        unit = edge_unit_name(name)
        was_active = await is_edge_unit_active(unit)
        if was_active:
            await asyncio.to_thread(
                subprocess.run,
                ["systemctl", "--user", "stop", unit],
                capture_output=True,
                text=True,
                check=False,
            )
        trash_put = shutil.which(config.TRASH_PUT_BINARY)
        if trash_put is None:
            raise RuntimeError(
                f"PROFILE_UNAVAILABLE: {name} could not be removed: "
                f"'{config.TRASH_PUT_BINARY}' binary not found — refusing to fall back to a "
                "permanent delete"
            )
        trashed = await asyncio.to_thread(
            subprocess.run,
            [trash_put, str(profile_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        if trashed.returncode:
            raise RuntimeError(
                f"PROFILE_UNAVAILABLE: {name} could not be trashed: "
                f"{trashed.stderr.strip() or 'unknown trash-put failure'}"
            )
        return {
            "profile": name,
            "removed": True,
            "was_active": was_active,
            "trashed_path": str(profile_dir),
        }

    async def extension_request(
        self, kind: str, payload: dict[str, Any], profile: str
    ) -> dict[str, Any]:
        """Purpose: dispatch a typed request to THAT profile's authenticated extension bridge.

        Args:
            self (Daemon): Daemon instance owning the extension bridge.
            kind (str): Typed bridge operation such as ``bookmark.list``.
            payload (dict[str, Any]): Complete single action object sent to the bridge.
            profile (str): Target browser-proxy profile; never silently answered by a different
                profile's connected extension.

        Returns:
            dict[str, Any]: Object-valued successful extension response data.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon.extension_request)
            True
            >>> Daemon().bridge.connected
            False
        """
        reply = await self.bridge.request(kind, payload, profile)
        if not reply.get("ok"):
            raise RuntimeError(f"EXTENSION_UNAVAILABLE: {profile}")
        data = reply.get("data", {})
        if not isinstance(data, dict):
            raise RuntimeError(f"EXTENSION_UNAVAILABLE: {profile}")
        return data

    async def _approve(
        self, action: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str, bool]:
        """Purpose: request fail-closed, editable approval from the SAME profile's paired extension.

        Args:
            self (Daemon): Daemon instance owning the extension bridge.
            action (str): Public action requiring approval.
            payload (dict[str, Any]): Complete single action object proposed for approval; its
                ``profile`` field selects which Edge window shows the overlay — never routed to
                an unrelated profile's window, which would let the wrong human approve the action.

        Returns:
            tuple[dict[str, Any], str, bool]: Approved object, reviewer comment, edit flag.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon._approve)
            True
            >>> Daemon().bridge.connected
            False
        """
        profile = str(payload.get("profile", "default"))
        reply = await self.bridge.request(
            "approval",
            {"action": action, "payload": payload, "timeout_seconds": 600},
            profile,
        )
        if not reply.get("ok"):
            raise PermissionError("APPROVAL_REJECTED")
        data = reply.get("data", {})
        if not isinstance(data, dict) or data.get("decision") != "approved":
            raise PermissionError("APPROVAL_REJECTED")
        edited = data.get("payload", payload)
        if not isinstance(edited, dict):
            raise PermissionError("APPROVAL_REJECTED")
        return edited, str(data.get("comment", "")), edited != payload

    async def dispatch(self, request: RpcRequest) -> Envelope:
        """Purpose: dispatch health, administration, or registered browser actions.

        Args:
            self (Daemon): Daemon state containing policies, profiles, and bridge.
            request (RpcRequest): Validated local JSON-RPC-like request object.

        Returns:
            Envelope: Stable success or failure envelope with no secret fields.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon.dispatch)
            True
            >>> isinstance(Daemon().bridge.connected, bool)
            True
        """
        if request.method == "ping":
            return Envelope.ok(
                {
                    "ready": True,
                    "profiles": await self.profile_inventory(),
                    "extension_connected": self.bridge.connected,
                    "extension_connected_profiles": list(self.bridge.connected_profiles()),
                }
            )
        self._last_work = monotonic()
        if request.method == "shutdown":
            self._stop.set()
            return Envelope.ok({"stopping": True})
        if request.method == "admin.status":
            return Envelope.ok(
                {
                    "profiles": await self.profile_inventory(),
                    "socket": str(socket_path()),
                    "extension_connected": self.bridge.connected,
                    "extension_connected_profiles": list(self.bridge.connected_profiles()),
                }
            )
        if request.method != "do":
            return Envelope.error("VALIDATION_ERROR", message=f"unknown method: {request.method}")
        action_name = request.params.get("action")
        payload = request.params.get("payload")
        action = REGISTRY.get(action_name) if isinstance(action_name, str) else None
        if action is None or not isinstance(payload, dict):
            return Envelope.error(
                "VALIDATION_ERROR", message="unknown action or non-object payload"
            )
        try:
            for field in action.policy.preflight_fields:
                if field not in payload or payload[field] in (None, ""):
                    raise ValueError(f"preflight requires {field}")
            comment, edited = "", False
            raw_method = str(payload.get("method", ""))
            requires_approval = action.policy.approval or (
                action.name == "raw" and not is_read_only_method(raw_method)
            )
            if requires_approval:
                payload, comment, edited = await self._approve(action.name, payload)
            result = await action.handler(payload, self)
            for field in action.policy.verification:
                if field in payload and result.get(field) != payload[field]:
                    raise RuntimeError(f"verification failed for {field}")
            if action.policy.verification:
                result["verification"] = {
                    field: result.get(field) == payload.get(field)
                    for field in action.policy.verification
                }
            return Envelope.ok(result, comment=comment, edited=edited)
        except PermissionError as error:
            return Envelope.error(str(error))
        except ValueError as error:
            code = "RAW_METHOD_DENIED" if str(error) == "RAW_METHOD_DENIED" else "VALIDATION_ERROR"
            return Envelope.error(code, message=str(error))
        except RuntimeError as error:
            code = str(error).split(":", 1)[0]
            if code not in {"CDP_UNAVAILABLE", "EXTENSION_UNAVAILABLE", "PROFILE_UNAVAILABLE"}:
                code = "CDP_UNAVAILABLE"
            return Envelope.error(code, message=str(error))
