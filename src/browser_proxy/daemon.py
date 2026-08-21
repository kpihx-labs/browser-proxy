"""Singleton Unix-socket daemon for Microsoft Edge-only automation."""

import asyncio
import os
import shutil
import socket
import subprocess
from pathlib import Path
from time import monotonic
from typing import Any

from browser_proxy.actions import REGISTRY, validate_registry
from browser_proxy.bridge import ExtensionBridge
from browser_proxy.cdp import CdpBrowser, is_read_only_method
from browser_proxy.models import Envelope, RpcRequest
from browser_proxy.paths import lock_path, runtime_dir, socket_path


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
            None: Creates an unstarted daemon with no managed profiles.

        Examples:
            >>> Daemon(idle_seconds=5).idle_seconds
            5
            >>> Daemon(max_lifetime_seconds=10).profiles
            {}
        """
        self.idle_seconds = idle_seconds or int(
            os.environ.get("BROWSER_PROXY_IDLE_SECONDS", "1800")
        )
        self.max_lifetime_seconds = max_lifetime_seconds or int(
            os.environ.get("BROWSER_PROXY_MAX_LIFETIME_SECONDS", "28800")
        )
        self.profiles: dict[str, int] = {}
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
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
            >>> Daemon(idle_seconds=1).profiles
            {}
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
            for process in self._processes.values():
                if process.poll() is None:
                    process.terminate()
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
            self (Daemon): Daemon whose activity timestamps and stop event are monitored.

        Returns:
            None: Raises the internal ``DAEMON_STOP`` signal after setting stop state.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon._lifecycle)
            True
            >>> Daemon(idle_seconds=1).idle_seconds
            1
        """
        started = monotonic()
        while not self._stop.is_set():
            await asyncio.sleep(0.2)
            if (
                monotonic() - started >= self.max_lifetime_seconds
                or monotonic() - self._last_work >= self.idle_seconds
            ):
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
        """Purpose: start Edge with an isolated persistent profile and loopback CDP.

        Args:
            self (Daemon): Daemon instance that owns the spawned Edge process.
            name (str): Safe persistent profile directory name.

        Returns:
            int: Loopback CDP port assigned to the started or existing profile.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon.start_profile)
            True
            >>> 'default' not in Daemon().profiles
            True
        """
        if name in self.profiles:
            return self.profiles[name]
        if not name or any(part in name for part in ("/", "\\", "..")):
            raise ValueError("invalid profile name")
        executable = os.environ.get("BROWSER_PROXY_EDGE_PATH") or shutil.which("microsoft-edge")
        if executable is None:
            raise RuntimeError("PROFILE_UNAVAILABLE: Microsoft Edge executable not found")
        port = self._free_port()
        profile_root = Path(
            os.environ.get(
                "BROWSER_PROXY_PROFILE_ROOT",
                str(Path.home() / ".local/share/browser-proxy/profiles"),
            )
        )
        profile_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        env = os.environ.copy()
        process = await asyncio.to_thread(
            subprocess.Popen,
            [
                executable,
                f"--remote-debugging-port={port}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile_root / name}",
                "--no-first-run",
                "--no-default-browser-check",
                "--no-startup-window",
                "--no-sandbox",
                "about:blank",
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._processes[name] = process
        for _ in range(100):
            if process.poll() is not None:
                self._processes.pop(name, None)
                stderr = process.stderr.read().decode() if process.stderr else "no stderr"
                raise RuntimeError(
                    f"PROFILE_UNAVAILABLE: Microsoft Edge exited before CDP became ready. Stderr: {stderr}"
                )
            try:
                await CdpBrowser(port).call("Browser.getVersion", {})
            except RuntimeError:
                await asyncio.sleep(0.1)
                continue
            self.profiles[name] = port
            return port
        process.terminate()
        self._processes.pop(name, None)
        raise RuntimeError("CDP_UNAVAILABLE: Microsoft Edge CDP readiness timed out")

    @staticmethod
    def _free_port() -> int:
        """Purpose: allocate a currently free loopback TCP port for Edge startup.

        Args:
            None: Binds an ephemeral local TCP listener temporarily.

        Returns:
            int: Candidate loopback port number released before process startup.

        Examples:
            >>> 0 < Daemon._free_port() < 65536
            True
            >>> isinstance(Daemon._free_port(), int)
            True
        """
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    async def extension_request(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Purpose: dispatch a typed request to the authenticated extension bridge.

        Args:
            self (Daemon): Daemon instance owning the extension bridge.
            kind (str): Typed bridge operation such as ``bookmark.list``.
            payload (dict[str, Any]): Complete single action object sent to the bridge.

        Returns:
            dict[str, Any]: Object-valued successful extension response data.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon.extension_request)
            True
            >>> Daemon().bridge.connected
            False
        """
        reply = await self.bridge.request(kind, payload)
        if not reply.get("ok"):
            raise RuntimeError("EXTENSION_UNAVAILABLE")
        data = reply.get("data", {})
        if not isinstance(data, dict):
            raise RuntimeError("EXTENSION_UNAVAILABLE")
        return data

    async def _approve(
        self, action: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], str, bool]:
        """Purpose: request fail-closed, editable approval from the paired extension.

        Args:
            self (Daemon): Daemon instance owning the extension bridge.
            action (str): Public action requiring approval.
            payload (dict[str, Any]): Complete single action object proposed for approval.

        Returns:
            tuple[dict[str, Any], str, bool]: Approved object, reviewer comment, edit flag.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon._approve)
            True
            >>> Daemon().bridge.connected
            False
        """
        reply = await self.bridge.request(
            "approval", {"action": action, "payload": payload, "timeout_seconds": 600}
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
            >>> Daemon().profiles == {}
            True
        """
        if request.method == "ping":
            return Envelope.ok(
                {
                    "ready": True,
                    "profiles": self.profiles,
                    "extension_connected": self.bridge.connected,
                }
            )
        self._last_work = monotonic()
        if request.method == "shutdown":
            self._stop.set()
            return Envelope.ok({"stopping": True})
        if request.method == "admin.status":
            return Envelope.ok(
                {
                    "profiles": self.profiles,
                    "socket": str(socket_path()),
                    "extension_connected": self.bridge.connected,
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
