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
from browser_proxy.cdp import is_read_only_method
from browser_proxy.models import Envelope, RpcRequest
from browser_proxy.paths import lock_path, runtime_dir, socket_path


class Daemon:
    """Own local transport, Edge profile processes, policy, and extension bridge."""

    def __init__(
        self, idle_seconds: int | None = None, max_lifetime_seconds: int | None = None
    ) -> None:
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
        """Bind the supplied systemd socket or a private Unix socket until shutdown."""
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
        """Adopt systemd's first passed listening socket when activation is active."""
        if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
            return None
        return socket.fromfd(3, socket.AF_UNIX, socket.SOCK_STREAM)

    def _acquire_lock(self) -> bool:
        """Atomically claim daemon ownership using a mode-0600 lockfile."""
        try:
            fd = os.open(lock_path(), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True

    async def _lifecycle(self) -> None:
        """Stop after the configured idle or maximum-lifetime limit."""
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
        """Handle precisely one newline-delimited request on a local client connection."""
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
        """Start Microsoft Edge only, with an isolated persistent profile and loopback CDP."""
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
        process = await asyncio.to_thread(
            subprocess.Popen,
            [
                executable,
                f"--remote-debugging-port={port}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile_root / name}",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._processes[name] = process
        self.profiles[name] = port
        return port

    @staticmethod
    def _free_port() -> int:
        """Allocate a currently free loopback TCP port for a new Edge process."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    async def extension_request(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a privileged action to the authenticated extension bridge."""
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
        """Request fail-closed, editable human approval from the paired extension."""
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
        """Dispatch health, administration, or registered browser action requests."""
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
