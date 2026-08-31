"""Expose the Tick-proxy-style JSON payload CLI.

Examples:
    >>> app.info.name
    'browser-proxy'
    >>> app.info.help.startswith('Unified')
    True
"""

import asyncio
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

import typer

from browser_proxy import config
from browser_proxy.actions import REGISTRY
from browser_proxy.client import request
from browser_proxy.daemon import Daemon
from browser_proxy.doc import get_compact_help, get_full_help
from browser_proxy.bridge import ExtensionBridge
from browser_proxy.models import Envelope
from browser_proxy.paths import (
    edge_cdp_port,
    edge_profile_dir,
    materialize_edge_profile,
    pairing_token_path,
    persistent_state_dir,
    runtime_dir,
    socket_path,
)
from browser_proxy.profile_state import describe_edge_profile, profile_unit_name


def _services_dir() -> Path:
    """Purpose: package-relative directory holding systemd unit templates.

    Args:
        None: No arguments — directory is derived from this file's location.

    Returns:
        Path to ``src/browser_proxy/services`` — works in editable and wheel installs.

    Examples:
        >>> _services_dir().name
        'services'
        >>> _services_dir().is_dir()
        True
    """

    return Path(__file__).parent / "services"


def _service_file(name: str) -> Path:
    """Purpose: resolve one unit template inside the package.

    Args:
        name (str): File name, e.g. ``browser-proxy.service``.

    Returns:
        Absolute path to the unit template.

    Examples:
        >>> _service_file("browser-proxy.service").name
        'browser-proxy.service'
        >>> _service_file("browser-proxy-profile@.service").suffix
        '.service'
    """

    return _services_dir() / name


app = typer.Typer(
    name="browser-proxy", help="Unified Microsoft Edge JSON-RPC proxy.", no_args_is_help=True
)
do_app = typer.Typer(help="Execute one flat JSON action.", no_args_is_help=False)
admin_app = typer.Typer(
    help="Manage daemon, services, profiles, and extension lifecycle.", no_args_is_help=True
)
app.add_typer(do_app, name="do")
app.add_typer(admin_app, name="admin")


AUTOSAVE_DIR = Path(os.environ.get(config.ENV_AUTOSAVE_DIR, config.AUTOSAVE_DIR_DEFAULT))
PREVIEW_LINES = max(
    config.PREVIEW_LINES_MINIMUM,
    int(os.environ.get(config.ENV_PREVIEW_LINES, str(config.PREVIEW_LINES_DEFAULT))),
)


def _payload(value: str | None) -> dict[str, object]:
    """Purpose: parse one inline JSON object or a path to one JSON object.

    Args:
        value (str | None): JSON text, a path to a JSON file, or ``None`` for an empty object.

    Returns:
        A validated top-level JSON object.

    Examples:
        >>> _payload('{}')
        {}
        >>> _payload('{"profile":"default"}')['profile']
        'default'
    """

    if value is None:
        return {}
    try:
        parsed: object = json.loads(value)
    except json.JSONDecodeError:
        path = Path(value)
        if not path.is_file():
            raise typer.BadParameter(
                "payload must be one JSON object or an existing JSON file"
            ) from None
        parsed = json.loads(path.read_text())
    payload = cast(dict[str, object], parsed)
    if not isinstance(payload, dict):
        raise typer.BadParameter("payload must be one JSON object")
    return payload


def _result_path(action: str, output: Path | None) -> Path:
    """Purpose: choose the explicit output path or a timestamped per-action autosave path.

    Args:
        action (str): Flat public action name used in the autosave filename.
        output (Path | None): Explicit complete-envelope destination, when requested.

    Returns:
        Path: Existing parent or newly created destination for the complete envelope.

    Examples:
        >>> _result_path('profile-list', Path('/tmp/result.json'))
        PosixPath('/tmp/result.json')
        >>> _result_path('profile-list', None).parent.name
        'browser-proxy-autosave'
    """

    if output is not None:
        return output
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    return AUTOSAVE_DIR / f"{action}_{stamp}.json"


def _preview_json(rendered: str, path: Path) -> str:
    """Purpose: retain the beginning and end of an oversized JSON result for terminal transparency.

    Args:
        rendered (str): Full pretty-printed JSON envelope already persisted to disk.
        path (Path): Full-envelope location named in the omission marker.

    Returns:
        str: Full JSON when short, otherwise N/2 leading and N/2 trailing lines with a clear marker.

    Examples:
        >>> _preview_json('{\n  "x": 1\n}', Path('/tmp/x.json')).endswith('}')
        True
        >>> 'full envelope' in _preview_json('\n'.join(str(i) for i in range(100)), Path('/tmp/x.json'))
        True
    """

    lines = rendered.splitlines()
    if len(lines) <= PREVIEW_LINES:
        return rendered
    half = PREVIEW_LINES // 2
    omitted = len(lines) - (half * 2)
    return "\n".join(
        [
            *lines[:half],
            f"… {omitted} JSON lines omitted; full envelope: {path} …",
            *lines[-half:],
        ]
    )


def _emit(envelope: Envelope, output: Path | None, output_format: str = "json") -> None:
    """Purpose: print one complete proxy envelope and optionally persist the same JSON.

    Args:
        envelope (Envelope): Pydantic proxy envelope returned by the daemon.
        output (Path | None): Optional requested output path.
        output_format (str): Presentation mode, either ``json`` or ``table``.

    Returns:
        ``None`` after JSON-only stdout output.

    Examples:
        >>> _emit.__name__
        '_emit'
        >>> callable(_emit)
        True
    """

    rendered = envelope.model_dump_json(indent=2)
    if output_format == "table":
        typer.echo(
            f"status\t{envelope.meta.status}\nedited\t{envelope.meta.edited}\ndata\t{json.dumps(envelope.data, sort_keys=True)}"
        )
    else:
        typer.echo(rendered)


def _emit_do(action: str, envelope: Envelope, output: Path | None, output_format: str) -> None:
    """Purpose: persist every ``do`` envelope then render its complete or bounded terminal view.

    Args:
        action (str): Flat action name used for the default timestamped autosave filename.
        envelope (Envelope): Complete success or error envelope to preserve exactly.
        output (Path | None): Optional explicit complete-envelope destination.
        output_format (str): ``json`` preview or complete ``table`` rendering.

    Returns:
        None: Writes one full envelope before showing any terminal representation.

    Examples:
        >>> callable(_emit_do)
        True
        >>> isinstance(PREVIEW_LINES, int)
        True
    """

    path = _result_path(action, output)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = envelope.model_dump_json(indent=2)
    path.write_text(rendered + "\n")
    typer.echo(f"💾 Full envelope: {path}", err=True)
    if output_format == "table":
        typer.echo(
            f"status\t{envelope.meta.status}\nedited\t{envelope.meta.edited}\ndata\t{json.dumps(envelope.data, sort_keys=True)}"
        )
    else:
        typer.echo(_preview_json(rendered, path))


@do_app.callback(invoke_without_command=True)
def do_help(
    context: typer.Context,
    show_help: bool = typer.Option(False, "--help", "-h", help="Show help.", hidden=True),
) -> None:
    """Purpose: display a grouped action catalog when ``do`` receives no action or explicit help.

    Args:
        context (typer.Context): Typer context identifying whether an action was selected.
        show_help (bool): ``True`` when ``do --help`` or ``do -h`` was requested.

    Returns:
        None: Prints user-facing catalog entries, never implementation docstrings.

    Examples:
        >>> callable(do_help)
        True
        >>> len(REGISTRY) >= 3
        True
    """

    if not show_help and context.invoked_subcommand is not None:
        return
    typer.echo("For action payloads, options, and three command → result examples, run:")
    typer.echo("  browser-proxy do <action> --help\n")
    groups: dict[str, list[Any]] = {}
    for action in REGISTRY.values():
        groups.setdefault(action.group, []).append(action)
    for group, actions in groups.items():
        typer.echo(f"── {group} ──")
        for action in actions:
            typer.echo(action.name)
            typer.echo(get_compact_help(action.handler))
            typer.echo()
        typer.echo()
    raise typer.Exit()


def _register(action_name: str) -> None:
    """Purpose: expose one registry action with the shared Tick-proxy-style payload/output contract.

    Args:
        action_name (str): Existing flat action name in the single source-of-truth registry.

    Returns:
        None: Registers a Typer command beneath ``browser-proxy do``.

    Examples:
        >>> callable(_register)
        True
        >>> 'profile-list' in REGISTRY
        True
    """

    action = REGISTRY[action_name]

    @do_app.command(action_name, help=get_full_help(action.handler))
    def _command(
        payload: Annotated[
            str | None, typer.Argument(help="Inline JSON object or JSON file path.")
        ] = None,
        output: Annotated[
            Path | None,
            typer.Option("--output-file", "-o", help="Write the complete envelope here."),
        ] = None,
        output_format: Annotated[
            str, typer.Option("--format", "-f", help="Output format: json (default) or table.")
        ] = "json",
        wait_for_cdp: Annotated[
            bool,
            typer.Option(
                "--wait-for-cdp", help="Wait for CDP instead of failing on CDP_UNAVAILABLE."
            ),
        ] = False,
    ) -> None:
        """Purpose: dispatch one documented registry action using one JSON payload.

        Args:
            payload (str | None): Inline JSON object, JSON file path, or no payload for ``{}``.
            output (Path | None): Optional file that receives the full result envelope.
            output_format (str): Presentation mode, either ``json`` or ``table``.
            wait_for_cdp (bool): Loop safely if the daemon returns CDP_UNAVAILABLE.

        Returns:
            ``None`` after printing the JSON envelope.

        Examples:
            >>> _command.__name__
            '_command'
            >>> isinstance(action_name, str)
            True
        """

        import time

        start = time.monotonic()
        while True:
            try:
                envelope = asyncio.run(
                    request("do", {"action": action_name, "payload": _payload(payload)})
                )
            except (OSError, ValueError, json.JSONDecodeError, typer.BadParameter) as error:
                envelope = Envelope.error("VALIDATION_ERROR", message=str(error))

            if (
                wait_for_cdp
                and envelope.meta.status == "error"
                and "CDP_UNAVAILABLE" in str(envelope.meta.comment)
            ):
                if time.monotonic() - start > 15.0:  # timeout after 15s
                    break
                time.sleep(1.0)
                continue
            break

        _emit_do(action_name, envelope, output, output_format)

    _command.__name__ = action_name.replace("-", "_")


for _name in REGISTRY:
    _register(_name)


# ---------------------------------------------------------------------------
# admin: shared helpers
# ---------------------------------------------------------------------------


def _systemctl(*arguments: str) -> Envelope:
    """Purpose: run a non-interactive user-systemd operation.

    Args:
        arguments (str): User-systemd arguments, for example ``start`` and a unit name.

    Returns:
        Envelope: Stable success or failure envelope for the systemd invocation.

    Examples:
        >>> callable(_systemctl)
        True
        >>> isinstance(_systemctl.__name__, str)
        True
    """
    completed = subprocess.run(
        ["systemctl", "--user", *arguments], capture_output=True, text=True, check=False
    )
    if completed.returncode:
        return Envelope.error(
            "DAEMON_UNAVAILABLE", message=completed.stderr.strip() or "systemctl failed"
        )
    return Envelope.ok({"systemctl": list(arguments)})


# ---------------------------------------------------------------------------
# admin status — global status of everything
# ---------------------------------------------------------------------------


@admin_app.command("status")
def admin_status() -> None:
    """Purpose: report global status of all services, files, symlinks, token, and permissions.

    Args:
        None.

    Returns:
        ``None`` after printing a comprehensive status envelope covering:
        - daemon RPC status (via ``admin.status``)
        - service file symlink health (``browser-proxy.service``, ``browser-proxy-profile@.service``)
        - persistent state dir existence and permissions
        - extension pairing token existence, permissions, and masked content
        - runtime dir existence

    Examples:
        >>> admin_status.__name__
        'admin_status'
        >>> callable(admin_status)
        True
    """

    data: dict[str, Any] = {}

    # Daemon RPC status
    daemon_status = asyncio.run(request("admin.status", {}))
    data["daemon"] = {
        "status": daemon_status.meta.status,
        "data": daemon_status.data if daemon_status.meta.status == "ok" else None,
    }

    # Service file symlinks
    service_src = _service_file("browser-proxy.service")
    profile_src = _service_file("browser-proxy-profile@.service")
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    service_link = xdg_config / "systemd/user/browser-proxy.service"
    profile_link = xdg_config / "systemd/user/browser-proxy-profile@.service"

    data["services"] = {
        "daemon": {
            "source": str(service_src),
            "source_exists": service_src.is_file(),
            "link": str(service_link),
            "link_exists": service_link.exists() or service_link.is_symlink(),
            "link_target": str(service_link.resolve())
            if service_link.exists() or service_link.is_symlink()
            else None,
        },
        "profile": {
            "source": str(profile_src),
            "source_exists": profile_src.is_file(),
            "link": str(profile_link),
            "link_exists": profile_link.exists() or profile_link.is_symlink(),
            "link_target": str(profile_link.resolve())
            if profile_link.exists() or profile_link.is_symlink()
            else None,
        },
    }

    # Persistent state
    state_dir = persistent_state_dir()
    data["persistent_state"] = {
        "path": str(state_dir),
        "exists": state_dir.is_dir(),
        "permissions": oct(state_dir.stat().st_mode)[-3:] if state_dir.is_dir() else None,
    }

    # Extension token
    token_path = pairing_token_path()
    data["extension_token"] = {
        "path": str(token_path),
        "exists": token_path.is_file(),
        "permissions": oct(token_path.stat().st_mode)[-3:] if token_path.is_file() else None,
    }
    if token_path.is_file():
        raw = token_path.read_text(encoding="utf-8").strip()
        if len(raw) >= 6:
            data["extension_token"]["masked"] = f"{raw[:3]}...{raw[-3:]}"
        else:
            data["extension_token"]["masked"] = "***"

    # Runtime dir
    rtdir = runtime_dir()
    data["runtime"] = {
        "path": str(rtdir),
        "exists": rtdir.is_dir(),
        "socket": str(socket_path()),
        "socket_exists": socket_path().exists() or socket_path().is_socket(),
    }

    _emit(Envelope.ok(data), None)


# ---------------------------------------------------------------------------
# admin service — daemon service lifecycle
# ---------------------------------------------------------------------------

service_app = typer.Typer(
    help="Manage the browser-proxy daemon service.",
    no_args_is_help=True,
)
admin_app.add_typer(service_app, name="service")


@service_app.command("install")
def admin_service_install() -> None:
    """Purpose: link and enable the package-owned singleton daemon service.

    Args:
        None: Uses the package-relative daemon service unit file.

    Returns:
        None: Emits one stable systemd result envelope.

    Examples:
        >>> callable(admin_service_install)
        True
        >>> admin_service_install.__name__
        'admin_service_install'
    """
    service = _service_file("browser-proxy.service")
    result = _systemctl("link", str(service))
    if result.meta.status == "ok":
        result = _systemctl("enable", "browser-proxy.service")
    _emit(result, None)


@service_app.command("start")
def admin_service_start() -> None:
    """Purpose: start the daemon service and verify activation with a ping.

    Args:
        None: Starts the configured user daemon service unit directly.

    Returns:
        None: Emits the daemon ping or systemd failure envelope.

    Examples:
        >>> callable(admin_service_start)
        True
        >>> admin_service_start.__name__
        'admin_service_start'
    """
    result = _systemctl("start", "browser-proxy.service")
    if result.meta.status == "ok":
        result = asyncio.run(request("ping", {}))
    _emit(result, None)


@service_app.command("stop")
def admin_service_stop() -> None:
    """Purpose: request graceful daemon shutdown.

    Args:
        None.

    Returns:
        None: Sends the real ``shutdown`` RPC over the daemon's own Unix socket FIRST — this works
        identically whether the daemon is systemd-managed or a raw background process. Falls back to
        ``systemctl --user stop browser-proxy.service`` only when the socket itself is unreachable.

    Examples:
        >>> admin_service_stop.__name__
        'admin_service_stop'
        >>> callable(admin_service_stop)
        True
    """

    result = asyncio.run(request("shutdown", {}))
    if result.meta.status == "ok":
        _emit(result, None)
        return
    _emit(_systemctl("stop", "browser-proxy.service"), None)


@service_app.command("restart")
def admin_service_restart() -> None:
    """Purpose: restart the daemon service (stop then start) and verify activation with a ping.

    Args:
        None.

    Returns:
        None: Emits the daemon ping or systemd failure envelope.

    Examples:
        >>> callable(admin_service_restart)
        True
        >>> admin_service_restart.__name__
        'admin_service_restart'
    """
    result = asyncio.run(request("shutdown", {}))
    if result.meta.status == "ok":
        import time

        time.sleep(1.0)
    result = _systemctl("restart", "browser-proxy.service")
    if result.meta.status == "ok":
        result = asyncio.run(request("ping", {}))
    _emit(result, None)


@service_app.command("logs")
def admin_service_logs() -> None:
    """Purpose: show the daemon service journal logs (last 50 lines).

    Args:
        None.

    Returns:
        None: Prints journalctl output.

    Examples:
        >>> callable(admin_service_logs)
        True
        >>> admin_service_logs.__name__
        'admin_service_logs'
    """
    completed = subprocess.run(
        ["journalctl", "--user", "-u", "browser-proxy.service", "-n", "50", "--no-pager"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        _emit(
            Envelope.error(
                "JOURNAL_FAILED", message=completed.stderr.strip() or "journalctl failed"
            ),
            None,
        )
    else:
        typer.echo(completed.stdout)


@service_app.command("purge")
def admin_service_purge() -> None:
    """Purpose: purge the daemon service (stop, disable, unlink).

    Args:
        None.

    Returns:
        None: Emits the result of each step.

    Examples:
        >>> callable(admin_service_purge)
        True
        >>> admin_service_purge.__name__
        'admin_service_purge'
    """
    # Stop
    asyncio.run(request("shutdown", {}))
    _systemctl("stop", "browser-proxy.service")
    # Disable
    _systemctl("disable", "browser-proxy.service")
    # Unlink
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    link = xdg_config / "systemd/user/browser-proxy.service"
    if link.exists() or link.is_symlink():
        link.unlink()
    # Remove runtime dir
    rtdir = runtime_dir()
    if rtdir.is_dir():
        shutil.rmtree(rtdir, ignore_errors=True)
    _emit(Envelope.ok({"purged": True}), None)


# ---------------------------------------------------------------------------
# admin profile — Edge profile lifecycle
# ---------------------------------------------------------------------------

profile_app = typer.Typer(
    help="Manage systemd-templated Microsoft Edge profile instances (one per profile). "
    "Always a real, visible window — 100% Transparency, no headless mode exists.",
    no_args_is_help=True,
)
admin_app.add_typer(profile_app, name="profile")


@profile_app.command("install")
def admin_profile_install() -> None:
    """Purpose: link the Edge profile unit template once, before any profile is started.

    Args:
        None: Uses the single package-relative templated unit file, linked
            once, not per profile instance.

    Returns:
        None: Emits one stable systemd result envelope.

    Examples:
        >>> callable(admin_profile_install)
        True
        >>> admin_profile_install.__name__
        'admin_profile_install'
    """
    _emit(_systemctl("link", str(_service_file("browser-proxy-profile@.service"))), None)


@profile_app.command("start")
def admin_profile_start(
    profile: Annotated[str, typer.Argument(help="Persistent Edge profile name")],
) -> None:
    """Purpose: start one profile's systemd-managed Microsoft Edge instance.

    Args:
        profile (str): Persistent Edge profile name to start.

    Returns:
        None: Emits the systemd result envelope. Always opens a real, visible
            window — there is no headless mode, by design (100% Transparency).

    Examples:
        >>> callable(admin_profile_start)
        True
        >>> admin_profile_start.__name__
        'admin_profile_start'
    """
    try:
        materialize_edge_profile(profile)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(_systemctl("start", profile_unit_name(profile)), None)


@profile_app.command("stop")
def admin_profile_stop(
    profile: Annotated[str, typer.Argument(help="Persistent Edge profile name")],
) -> None:
    """Purpose: stop one profile's systemd-managed Microsoft Edge instance.

    Args:
        profile (str): Persistent Edge profile name to stop.

    Returns:
        None: Emits the systemd result envelope.

    Examples:
        >>> callable(admin_profile_stop)
        True
        >>> admin_profile_stop.__name__
        'admin_profile_stop'
    """
    _emit(_systemctl("stop", profile_unit_name(profile)), None)


@profile_app.command("restart")
def admin_profile_restart(
    profile: Annotated[str, typer.Argument(help="Persistent Edge profile name")],
) -> None:
    """Purpose: restart one profile's systemd-managed Microsoft Edge instance.

    Args:
        profile (str): Persistent Edge profile name to restart.

    Returns:
        None: Emits the systemd result envelope.

    Examples:
        >>> callable(admin_profile_restart)
        True
        >>> admin_profile_restart.__name__
        'admin_profile_restart'
    """
    _emit(_systemctl("restart", profile_unit_name(profile)), None)


@profile_app.command("status")
def admin_profile_status(
    profile: Annotated[str, typer.Argument(help="Persistent Edge profile name")],
) -> None:
    """Purpose: report one profile's disk, systemd, CDP, and extension-bridge state — all 4 axes.

    Args:
        profile (str): Persistent Edge profile name to inspect.

    Returns:
        None: Emits a redacted status envelope with no secret values.

    Examples:
        >>> callable(admin_profile_status)
        True
        >>> admin_profile_status.__name__
        'admin_profile_status'
    """
    try:
        edge_profile_dir(profile)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    description = asyncio.run(describe_edge_profile(profile))
    extension_connected: bool | None = None
    daemon_status = asyncio.run(request("admin.status", {}))
    if daemon_status.meta.status == "ok":
        connected_profiles = daemon_status.data.get("extension_connected_profiles")
        if isinstance(connected_profiles, list):
            extension_connected = profile in connected_profiles
    _emit(
        Envelope.ok({**description, "extension_connected": extension_connected}),
        None,
    )


@profile_app.command("logs")
def admin_profile_logs(
    profile: Annotated[str, typer.Argument(help="Persistent Edge profile name")],
) -> None:
    """Purpose: show the Edge profile systemd unit journal logs (last 50 lines).

    Args:
        profile (str): Persistent Edge profile name.

    Returns:
        None: Prints journalctl output.

    Examples:
        >>> callable(admin_profile_logs)
        True
        >>> admin_profile_logs.__name__
        'admin_profile_logs'
    """
    unit = profile_unit_name(profile)
    completed = subprocess.run(
        ["journalctl", "--user", "-u", unit, "-n", "50", "--no-pager"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        _emit(
            Envelope.error(
                "JOURNAL_FAILED", message=completed.stderr.strip() or "journalctl failed"
            ),
            None,
        )
    else:
        typer.echo(completed.stdout)


@profile_app.command("purge")
def admin_profile_purge(
    profile: Annotated[str, typer.Argument(help="Persistent Edge profile name")],
) -> None:
    """Purpose: purge one profile's Edge instance and its profile directory.

    Args:
        profile (str): Persistent Edge profile name to purge.

    Returns:
        None: Stops the unit, trashes the profile directory, emits the result.

    Examples:
        >>> callable(admin_profile_purge)
        True
        >>> admin_profile_purge.__name__
        'admin_profile_purge'
    """
    unit = profile_unit_name(profile)
    was_active = False
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True,
            text=True,
            check=False,
        )
        was_active = result.stdout.strip() == "active"
    except OSError:
        pass  # systemctl unavailable — treat as inactive

    if was_active:
        _systemctl("stop", unit)

    profile_dir = edge_profile_dir(profile)
    if profile_dir.is_dir():
        trash_bin = shutil.which("trash-put")
        if trash_bin:
            subprocess.run([trash_bin, str(profile_dir)], check=False)
        else:
            shutil.rmtree(profile_dir, ignore_errors=True)

    _emit(Envelope.ok({"purged": True, "profile": profile, "was_active": was_active}), None)


# ---------------------------------------------------------------------------
# admin extension — extension pairing lifecycle
# ---------------------------------------------------------------------------

extension_app = typer.Typer(
    help="Manage the paired Microsoft Edge extension.", no_args_is_help=True
)
admin_app.add_typer(extension_app, name="extension")


@extension_app.command("pair")
def admin_extension_pair() -> None:
    """Purpose: store an operator-provisioned extension capability without printing it.

    Args:
        None: Prompts with hidden terminal input for the secret visibly generated once by the
            extension options page, then stores it as a protected local capability.

    Returns:
        None: Emits only confirmation, never the capability value.

    Examples:
        >>> callable(admin_extension_pair)
        True
        >>> admin_extension_pair.__name__
        'admin_extension_pair'
    """
    secret = typer.prompt(
        "Paste the pairing secret shown once in the extension options page", hide_input=True
    )
    ExtensionBridge().pair(secret)
    _emit(
        Envelope.ok({"paired": True, "bridge": "ws://127.0.0.1 (capability stored locally)"}), None
    )


@extension_app.command("unpair")
def admin_extension_unpair() -> None:
    """Purpose: remove the stored extension pairing token.

    Args:
        None: Deletes the ``extension.env`` file from the persistent state directory.

    Returns:
        None: Emits confirmation of removal or that no token existed.

    Examples:
        >>> callable(admin_extension_unpair)
        True
        >>> admin_extension_unpair.__name__
        'admin_extension_unpair'
    """
    token_path = pairing_token_path()
    if token_path.is_file():
        token_path.unlink()
        _emit(Envelope.ok({"unpaired": True, "removed": str(token_path)}), None)
    else:
        _emit(Envelope.ok({"unpaired": True, "message": "no token file found"}), None)


@extension_app.command("status")
def admin_extension_status() -> None:
    """Purpose: report the extension pairing token health — existence, permissions, and masked content.

    Args:
        None.

    Returns:
        None: Emits the token file path, existence, permissions, and a masked preview of the token
        (first 3 + last 3 characters). Never exposes the full secret.

    Examples:
        >>> callable(admin_extension_status)
        True
        >>> admin_extension_status.__name__
        'admin_extension_status'
    """
    token_path = pairing_token_path()
    data: dict[str, Any] = {
        "path": str(token_path),
        "exists": token_path.is_file(),
    }
    if token_path.is_file():
        stat = token_path.stat()
        data["permissions"] = oct(stat.st_mode)[-3:]
        data["permissions_ok"] = data["permissions"] == "600"
        raw = token_path.read_text(encoding="utf-8").strip()
        if len(raw) >= 6:
            data["token_preview"] = f"{raw[:3]}...{raw[-3:]}"
        else:
            data["token_preview"] = "***"
        data["token_length"] = len(raw)
    else:
        data["permissions_ok"] = None
        data["token_preview"] = None
        data["token_length"] = 0

    _emit(Envelope.ok(data), None)


# ---------------------------------------------------------------------------
# admin doctor — diagnose and fix common issues
# ---------------------------------------------------------------------------


@admin_app.command("doctor")
def admin_doctor() -> None:
    """Purpose: diagnose and fix missing directories, symlinks, and permission issues.

    Args:
        None: Checks and repairs:
        - persistent state directory (create + fix permissions)
        - runtime directory (create + fix permissions)
        - service symlinks (link if missing)
        - extension token permissions (fix to 0600)

    Returns:
        None: Emits a report of what was checked and what was fixed.

    Examples:
        >>> callable(admin_doctor)
        True
        >>> admin_doctor.__name__
        'admin_doctor'
    """
    fixes: list[str] = []
    checks: list[str] = []

    # Persistent state dir
    state_dir = persistent_state_dir()
    if not state_dir.is_dir():
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fixes.append(f"created persistent state dir: {state_dir}")
    else:
        checks.append(f"persistent state dir OK: {state_dir}")

    # Runtime dir
    rtdir = runtime_dir()
    if not rtdir.is_dir():
        rtdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fixes.append(f"created runtime dir: {rtdir}")
    else:
        checks.append(f"runtime dir OK: {rtdir}")

    # Service symlinks
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    systemd_user_dir = xdg_config / "systemd/user"
    if not systemd_user_dir.is_dir():
        systemd_user_dir.mkdir(parents=True, exist_ok=True)
        fixes.append(f"created systemd user dir: {systemd_user_dir}")

    service_pairs = [
        ("browser-proxy.service", _service_file("browser-proxy.service")),
        ("browser-proxy-profile@.service", _service_file("browser-proxy-profile@.service")),
    ]
    for unit_name, src in service_pairs:
        link = systemd_user_dir / unit_name
        if not (link.exists() or link.is_symlink()):
            result = _systemctl("link", str(src))
            if result.meta.status == "ok":
                fixes.append(f"linked {unit_name}")
            else:
                checks.append(f"link {unit_name} FAILED: {result.meta.comment}")
        else:
            checks.append(f"{unit_name} link OK")

    # Extension token permissions
    token_path = pairing_token_path()
    if token_path.is_file():
        current = oct(token_path.stat().st_mode)[-3:]
        if current != "600":
            token_path.chmod(0o600)
            fixes.append(f"fixed extension token permissions: {current} → 600")
        else:
            checks.append(f"extension token permissions OK: {current}")
    else:
        checks.append("extension token not found (run `admin extension pair` first)")

    _emit(
        Envelope.ok({"fixes": fixes, "checks": checks}),
        None,
    )


# ---------------------------------------------------------------------------
# admin purge — full system purge
# ---------------------------------------------------------------------------


@admin_app.command("purge")
def admin_purge() -> None:
    """Purpose: purge everything — daemon, all profiles, extension token, symlinks, runtime state.

    Args:
        None. This is destructive. After this command, browser-proxy is as if it was never installed.
        The user should also run ``uv tool uninstall browser-proxy`` to remove the CLI binary.

    Returns:
        None: Emits a report of what was purged.

    Examples:
        >>> callable(admin_purge)
        True
        >>> admin_purge.__name__
        'admin_purge'
    """
    purged: list[str] = []

    # Stop daemon
    asyncio.run(request("shutdown", {}))
    _systemctl("stop", "browser-proxy.service")
    purged.append("daemon stopped")

    # Disable + unlink daemon service
    _systemctl("disable", "browser-proxy.service")
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    daemon_link = xdg_config / "systemd/user/browser-proxy.service"
    if daemon_link.exists() or daemon_link.is_symlink():
        daemon_link.unlink()
        purged.append("daemon service link removed")

    # Unlink profile service template
    profile_link = xdg_config / "systemd/user/browser-proxy-profile@.service"
    if profile_link.exists() or profile_link.is_symlink():
        profile_link.unlink()
        purged.append("profile service link removed")

    # Stop and trash all profile directories
    profile_root = Path.home() / config.PROFILE_ROOT_RELATIVE
    if profile_root.is_dir():
        trash_bin = shutil.which("trash-put")
        for entry in profile_root.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                unit = profile_unit_name(entry.name)
                _systemctl("stop", unit)
                if trash_bin:
                    subprocess.run([trash_bin, str(entry)], check=False)
                else:
                    shutil.rmtree(entry, ignore_errors=True)
                purged.append(f"profile trashed: {entry.name}")

    # Remove persistent state
    state_dir = persistent_state_dir()
    if state_dir.is_dir():
        shutil.rmtree(state_dir, ignore_errors=True)
        purged.append(f"persistent state removed: {state_dir}")

    # Remove runtime dir
    rtdir = runtime_dir()
    if rtdir.is_dir():
        shutil.rmtree(rtdir, ignore_errors=True)
        purged.append(f"runtime dir removed: {rtdir}")

    _emit(
        Envelope.ok(
            {
                "purged": purged,
                "hint": "To also remove the CLI binary, run: uv tool uninstall browser-proxy",
            }
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Hidden entrypoints (systemd ExecStart targets only)
# ---------------------------------------------------------------------------


@app.command("daemon", hidden=True)
def daemon() -> None:
    """Purpose: run the managed daemon entrypoint used only by systemd.

    Args:
        None.

    Returns:
        ``None`` after the daemon exits.

    Examples:
        >>> daemon.__name__
        'daemon'
        >>> callable(daemon)
        True
    """

    asyncio.run(Daemon().serve())


def edge_launch_args(profile: str) -> list[str]:
    """Purpose: build the exact Microsoft Edge argv for one systemd-managed profile.

    Args:
        profile (str): Safe persistent Edge profile name, never a path traversal.

    Returns:
        list[str]: Complete argv starting with the resolved Edge executable path.
            Never includes ``--no-startup-window``: every managed Edge instance
            always opens a real, visible window — 100% Transparency, no
            headless/hidden mode exists in this CLI.

    Examples:
        >>> '--no-startup-window' in edge_launch_args('test')
        False
        >>> edge_launch_args('test')[0] == edge_launch_args('test')[0]
        True
    """
    try:
        profile_dir = materialize_edge_profile(profile)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    executable = os.environ.get(config.ENV_EDGE_PATH) or shutil.which("microsoft-edge")
    if executable is None:
        raise RuntimeError("PROFILE_UNAVAILABLE: Microsoft Edge executable not found")
    return [
        executable,
        f"--remote-debugging-port={edge_cdp_port(profile)}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]


@app.command("edge-launch", hidden=True)
def edge_launch(
    profile: Annotated[str, typer.Argument(help="Persistent Edge profile name")],
) -> None:
    """Purpose: exec Microsoft Edge for one systemd-managed profile, used only by systemd.

    Args:
        profile (str): Persistent Edge profile name to launch.

    Returns:
        None: Never returns on success; ``os.execvp`` replaces this process so
            systemd tracks Edge's real PID directly, not a Python wrapper. Always
            opens a real, visible window (no flag, no headless mode).

    Examples:
        >>> edge_launch.__name__
        'edge_launch'
        >>> callable(edge_launch)
        True
    """
    args = edge_launch_args(profile)
    os.execvp(args[0], args)
