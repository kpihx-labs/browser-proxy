"""Singleton Unix-socket daemon for Microsoft Edge-only automation."""

import asyncio
import os
import socket
import subprocess
from typing import Any

import shutil

from browser_proxy import config
from browser_proxy.actions import (
    REGISTRY,
    _profile,
    format_window_preview,
    validate_registry,
    windows_preview_for_targets,
)
from browser_proxy.bridge import ExtensionBridge
from browser_proxy.cdp import CdpBrowser, is_read_only_method
from browser_proxy import ipc
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

_APPROVAL_BRIDGE_GRACE_SECONDS = 5.0
"""Extra seconds the daemon-side bridge wait adds ON TOP of the exact HITL timeout it tells the
extension to honor — guarantees the daemon can never give up a hair before the extension's own
alarm-based expiry could legitimately still fire and reply (see ``Daemon._approve``)."""


class Daemon:
    """Own local transport, Edge profile processes, policy, and extension bridge."""

    def __init__(self) -> None:
        """Purpose: initialize daemon lifecycle, profile, and extension bridge state.

        Args:
            None.

        Returns:
            None: Creates an unstarted daemon; persistent profile identity remains on disk.

        Notes:
            Deliberately no idle TTL and no maximum lifetime (KπX directive): the daemon is
            lançable/arrêtable purely on request — `admin start`/`admin stop`, or the OS itself —
            never on an automatic timer. Every managed Edge window is already always visible, so
            KπX can directly see and close one an agent forgot; there is no case where an
            unattended timeout is the right way to reclaim it.

        Examples:
            >>> isinstance(Daemon()._stop, asyncio.Event)
            True
            >>> Daemon().bridge.connected
            False
        """
        self._stop = asyncio.Event()
        self.bridge = ExtensionBridge()

    async def serve(self) -> None:
        """Purpose: serve requests on systemd's or a private Unix socket until explicitly stopped.

        Args:
            self (Daemon): Daemon instance owning lifecycle state and local transports.

        Returns:
            None: Stops ONLY on an explicit ``admin stop``/``shutdown`` RPC (or the process being
            killed) — no idle TTL, no maximum lifetime (KπX directive: purely lançable/arrêtable).

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon.serve)
            True
            >>> isinstance(Daemon()._stop, asyncio.Event)
            True
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
            await asyncio.gather(server.serve_forever(), self._await_explicit_stop())
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
            >>> isinstance(Daemon()._stop, asyncio.Event)
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

    async def _await_explicit_stop(self) -> None:
        """Purpose: block until an explicit stop is requested — the ONLY way this daemon stops.

        Args:
            self (Daemon): Daemon whose stop event is awaited.

        Returns:
            None: Raises the internal ``DAEMON_STOP`` signal once ``self._stop`` is set by the
            ``shutdown`` RPC (``admin stop``).

        Notes:
            Deliberately no idle TTL, no maximum lifetime (KπX directive, root-caused a real
            complaint: an idle-suspended-while-connected TTL still resumed and killed the whole
            daemon — CDP included — the instant the extension bridge dropped for any unrelated
            reason). Every managed Edge window is already always visible, so KπX can directly see
            and close one an agent forgot; there is no case where an unattended timeout is the
            right way to reclaim it. The daemon is purely lançable/arrêtable on request.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon._await_explicit_stop)
            True
            >>> Daemon()._stop.is_set()
            False
        """
        await self._stop.wait()
        raise RuntimeError("DAEMON_STOP")

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Purpose: handle one length-prefixed request on a local client connection.

        Args:
            self (Daemon): Daemon instance dispatching the validated request.
            reader (asyncio.StreamReader): Connected local client's byte stream.
            writer (asyncio.StreamWriter): Connected local client's response stream.

        Returns:
            None: Writes one complete, length-prefixed JSON envelope before closing the
            connection — never truncated, regardless of how large the result is (see ``ipc.py``).

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon._handle)
            True
            >>> callable(Daemon.dispatch)
            True
        """
        try:
            raw = await asyncio.wait_for(ipc.read_message(reader), timeout=10)
            if not raw:
                raise ValueError("empty request")
            envelope = await self.dispatch(RpcRequest.model_validate_json(raw))
        except (ValueError, OSError, UnicodeDecodeError, ConnectionError) as error:
            envelope = Envelope.error("VALIDATION_ERROR", message=str(error))
        await ipc.write_message(writer, envelope.model_dump_json().encode())
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

        Raises:
            RuntimeError: ``EXTENSION_REJECTED: <real message>`` when the extension WAS reachable,
                received the request, and explicitly declined it with its own specific reason (a
                validation error, a Chrome platform restriction, an unknown id, ...) — genuinely
                different from ``EXTENSION_UNAVAILABLE`` (no connection at all) and never collapsed
                into it: KπX, GRAVÉ — "le code retourne souvent le même code d'erreur qui n'est pas
                exact" — a real, specific extension-side rejection reason must never be silently
                discarded and relabeled as if the connection itself were the problem.
                ``EXTENSION_UNAVAILABLE: <profile> (malformed reply data)`` only for the genuinely
                separate case of a syntactically broken reply (not a dict at all).

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon.extension_request)
            True
            >>> Daemon().bridge.connected
            False
        """
        reply = await self.bridge.request(kind, payload, profile)
        data = reply.get("data", {})
        if not reply.get("ok"):
            message = data.get("message") if isinstance(data, dict) else None
            raise RuntimeError(
                f"EXTENSION_REJECTED: {message or 'the extension declined this request'}"
            )
        if not isinstance(data, dict):
            raise RuntimeError(f"EXTENSION_UNAVAILABLE: {profile} (malformed reply data)")
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

        Notes:
            Root-caused live (KπX): a rejection used to always report the bare code
            ``APPROVAL_REJECTED`` with an empty message, indistinguishable whether a real human
            clicked "deny", the extension's own alarm genuinely timed out, or the extension could
            not even DISPLAY the overlay at all (a technical delivery failure, e.g. every candidate
            tab's content script was stale right after an extension reload) — this now raises one
            of three distinct codes (``APPROVAL_REJECTED``/``APPROVAL_TIMEOUT``/
            ``APPROVAL_UNAVAILABLE``) carrying the extension's REAL diagnostic message, never
            silently discarded. Also fixed the exact bug KπX caught live: the daemon-side wait used
            to be a SEPARATE hardcoded ``600`` never actually read from anywhere configurable, now
            derived from the single ``config.HITL_TIMEOUT_SECONDS_DEFAULT``/
            ``config.ENV_HITL_TIMEOUT_SECONDS`` source of truth, with a small fixed grace margin on
            the daemon's own wait so it can never expire before the extension's own alarm does.
        """
        profile = str(payload.get("profile", "default"))
        hitl_timeout = float(
            os.environ.get(
                config.ENV_HITL_TIMEOUT_SECONDS, str(config.HITL_TIMEOUT_SECONDS_DEFAULT)
            )
        )
        try:
            reply = await self.bridge.request(
                "approval",
                {"action": action, "payload": payload, "timeout_seconds": hitl_timeout},
                profile,
                timeout_seconds=hitl_timeout + _APPROVAL_BRIDGE_GRACE_SECONDS,
            )
        except TimeoutError as error:
            raise PermissionError(
                "APPROVAL_TIMEOUT: daemon gave up waiting for the extension's reply"
            ) from error
        if not reply.get("ok"):
            data = reply.get("data", {})
            message = str(data.get("message", "")) if isinstance(data, dict) else ""
            decision = str(data.get("decision", "")) if isinstance(data, dict) else ""
            code = {"rejected": "APPROVAL_REJECTED", "timeout": "APPROVAL_TIMEOUT"}.get(
                decision, "APPROVAL_UNAVAILABLE"
            )
            raise PermissionError(f"{code}: {message}" if message else code)
        data = reply.get("data", {})
        if not isinstance(data, dict) or data.get("decision") != "approved":
            raise PermissionError("APPROVAL_REJECTED")
        edited = data.get("payload", payload)
        if not isinstance(edited, dict):
            raise PermissionError("APPROVAL_REJECTED")
        return edited, str(data.get("comment", "")), edited != payload

    async def _target_approval_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Purpose: enrich ANY approval payload carrying CDP ``target_id``/``target_ids`` with
        per-window first/last context — centralized for EVERY gated action, never a
        ``window-close``-only special case.

        Args:
            self (Daemon): Daemon instance, unused beyond profile resolution (mirrors ``_profile``'s
                own ``DaemonContext`` shape).
            payload (dict[str, Any]): The real gated-action payload about to be approved — may be
                ``window-close`` (``target_ids`` list), ``tab-activate``/``storage-local-set``
                (singular ``target_id``), or any future action using either field.

        Returns:
            dict[str, Any]: The SAME payload plus one extra ``"context"`` field — a list of
            human-readable ``format_window_preview()`` lines, one per REAL window touched by the
            resolved target(s) — so the HITL overlay always shows first/last tab titles instead of
            opaque CDP target ids alone (KπX root-caused live: "je ne connais pas quel id
            correspond à quel window"). Returns the ORIGINAL, unenriched payload untouched when
            neither field is present, is malformed, or the profile/CDP connection is unavailable —
            the real validation/connection error still surfaces normally once the actual handler
            runs afterward; enrichment failure never blocks the real action.

        Examples:
            >>> asyncio.iscoroutinefunction(Daemon._target_approval_preview)
            True
            >>> callable(Daemon._target_approval_preview)
            True
        """
        raw_target_ids = payload.get("target_ids")
        single_target_id = payload.get("target_id")
        if isinstance(raw_target_ids, list) and raw_target_ids:
            target_ids = [str(value) for value in raw_target_ids]
        elif single_target_id not in (None, ""):
            target_ids = [str(single_target_id)]
        else:
            return payload
        try:
            _, browser = _profile(payload, self)
            previews = await windows_preview_for_targets(browser, target_ids)
        except RuntimeError:
            return payload
        return {**payload, "context": [format_window_preview(preview) for preview in previews]}

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
                approval_payload = await self._target_approval_preview(payload)
                payload, comment, edited = await self._approve(action.name, approval_payload)
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
            code = str(error).split(":", 1)[0]
            if code not in {"APPROVAL_REJECTED", "APPROVAL_TIMEOUT", "APPROVAL_UNAVAILABLE"}:
                code = "APPROVAL_REJECTED"
            return Envelope.error(code, message=str(error))
        except ValueError as error:
            code = "RAW_METHOD_DENIED" if str(error) == "RAW_METHOD_DENIED" else "VALIDATION_ERROR"
            return Envelope.error(code, message=str(error))
        except (RuntimeError, TimeoutError) as error:
            # Every internal RuntimeError/TimeoutError in this codebase is deliberately raised as
            # "SOME_REAL_CODE: details" (CDP_ERROR, CDP_UNAVAILABLE, EXTENSION_UNAVAILABLE,
            # EXTENSION_REJECTED, EXTENSION_TIMEOUT, PROFILE_UNAVAILABLE, NOT_FOUND,
            # DAEMON_ALREADY_RUNNING, DAEMON_STOP, ...). A fixed whitelist here used to silently
            # relabel any code NOT on the list back to a misleading "CDP_UNAVAILABLE" the instant a
            # new one was introduced elsewhere without this list being remembered too (KπX, GRAVÉ:
            # "le code retourne souvent le même code d'erreur qui n'est pas exact" — confirmed live
            # against a real "chrome.management.uninstall requires a user gesture" rejection that
            # this exact bug had been silently discarding). Trust the raiser's own real code instead
            # of re-deriving/second-guessing it — only an UNRECOGNIZABLE shape (no genuine
            # "UPPER_SNAKE_CASE: message" prefix at all — a true internal-bug fallback, not a code
            # we simply forgot to whitelist) falls back to a generic label.
            code, _, rest = str(error).partition(":")
            if not (code and rest and code.replace("_", "").isalpha() and code.isupper()):
                code = "CDP_UNAVAILABLE"
            return Envelope.error(code, message=str(error))
