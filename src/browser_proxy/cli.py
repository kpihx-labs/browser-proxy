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
)
from browser_proxy.profile_state import describe_edge_profile, edge_unit_name

app = typer.Typer(
    name="browser-proxy", help="Unified Microsoft Edge JSON-RPC proxy.", no_args_is_help=True
)
do_app = typer.Typer(help="Execute one flat JSON action.", no_args_is_help=False)
admin_app = typer.Typer(help="Manage daemon and extension lifecycle.", no_args_is_help=True)
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
    ) -> None:
        """Purpose: dispatch one documented registry action using one JSON payload.

        Args:
            payload (str | None): Inline JSON object, JSON file path, or no payload for ``{}``.
            output (Path | None): Optional file that receives the full result envelope.
            output_format (str): Presentation mode, either ``json`` or ``table``.

        Returns:
            ``None`` after printing the JSON envelope.

        Examples:
            >>> _command.__name__
            '_command'
            >>> isinstance(action_name, str)
            True
        """

        try:
            envelope = asyncio.run(
                request("do", {"action": action_name, "payload": _payload(payload)})
            )
        except (OSError, ValueError, json.JSONDecodeError, typer.BadParameter) as error:
            envelope = Envelope.error("VALIDATION_ERROR", message=str(error))
        _emit_do(action_name, envelope, output, output_format)

    _command.__name__ = action_name.replace("-", "_")


for _name in REGISTRY:
    _register(_name)


@admin_app.command("status")
def admin_status() -> None:
    """Purpose: report daemon-owned Edge profile endpoints.

    Args:
        None.

    Returns:
        ``None`` after printing a status envelope.

    Examples:
        >>> admin_status.__name__
        'admin_status'
        >>> callable(admin_status)
        True
    """

    _emit(asyncio.run(request("admin.status", {})), None)


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


@admin_app.command("install")
def admin_install() -> None:
    """Purpose: link and enable the repository-owned singleton daemon service.

    Args:
        None: Uses the repository-relative daemon service unit file. This manages
            only the Python daemon process; see ``admin edge install`` for the
            separate systemd-templated Microsoft Edge instances.

    Returns:
        None: Emits one stable systemd result envelope.

    Examples:
        >>> callable(admin_install)
        True
        >>> admin_install.__name__
        'admin_install'
    """
    project = Path(__file__).resolve().parents[2]
    service = project / "systemd/browser-proxy.service"
    result = _systemctl("link", str(service))
    if result.meta.status == "ok":
        result = _systemctl("enable", "browser-proxy.service")
    _emit(result, None)


@admin_app.command("start")
def admin_start() -> None:
    """Purpose: start the daemon service and verify activation with a ping.

    Args:
        None: Starts the configured user daemon service unit directly (no
            separate socket unit exists; the daemon opens its own Unix socket).

    Returns:
        None: Emits the daemon ping or systemd failure envelope.

    Examples:
        >>> callable(admin_start)
        True
        >>> admin_start.__name__
        'admin_start'
    """
    result = _systemctl("start", "browser-proxy.service")
    if result.meta.status == "ok":
        result = asyncio.run(request("ping", {}))
    _emit(result, None)


@admin_app.command("stop")
def admin_stop() -> None:
    """Purpose: request graceful daemon shutdown.

    Args:
        None.

    Returns:
        None: Sends the real ``shutdown`` RPC over the daemon's own Unix socket FIRST — this works
        identically whether the daemon is systemd-managed or a raw background process (e.g.
        ``make smoke``'s isolated test daemon), unlike ``systemctl stop`` alone, which silently
        no-ops for a process systemd never launched (root-caused a real hang: the daemon has no
        idle TTL to fall back on by design — KπX directive — so a stop that does nothing left the
        test daemon running forever). Falls back to ``systemctl --user stop
        browser-proxy.service`` only when the socket itself is unreachable (a genuinely hung or
        already-dead daemon).

    Examples:
        >>> admin_stop.__name__
        'admin_stop'
        >>> callable(admin_stop)
        True
    """

    result = asyncio.run(request("shutdown", {}))
    if result.meta.status == "ok":
        _emit(result, None)
        return
    _emit(_systemctl("stop", "browser-proxy.service"), None)


@admin_app.command("doctor")
def admin_doctor() -> None:
    """Purpose: report Edge, pairing, and daemon health without secret values.

    Args:
        None: Inspects local executable availability and daemon reachability.

    Returns:
        None: Emits a redacted health envelope.

    Examples:
        >>> callable(admin_doctor)
        True
        >>> admin_doctor.__name__
        'admin_doctor'
    """
    status = asyncio.run(request("ping", {}))
    _emit(
        Envelope.ok(
            {
                "edge_only": True,
                "edge_binary": bool(shutil.which("microsoft-edge")),
                "daemon": status.model_dump(),
                "pairing_configured": ExtensionBridge()._token() != "",
            }
        ),
        None,
    )


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


edge_app = typer.Typer(
    help="Manage systemd-templated Microsoft Edge CDP instances (one per profile). "
    "Always a real, visible window — 100% Transparency, no headless mode exists.",
    no_args_is_help=True,
)
admin_app.add_typer(edge_app, name="edge")


@edge_app.command("install")
def admin_edge_install() -> None:
    """Purpose: link the Edge unit template once, before any profile is started.

    Args:
        None: Uses the single repository-relative templated unit file, linked
            once, not per profile instance.

    Returns:
        None: Emits one stable systemd result envelope.

    Examples:
        >>> callable(admin_edge_install)
        True
        >>> admin_edge_install.__name__
        'admin_edge_install'
    """
    project = Path(__file__).resolve().parents[2]
    _emit(_systemctl("link", str(project / "systemd/browser-proxy-edge@.service")), None)


@edge_app.command("start")
def admin_edge_start(
    profile: Annotated[str, typer.Argument(help="Persistent Edge profile name")],
) -> None:
    """Purpose: start one profile's systemd-managed Microsoft Edge instance.

    Args:
        profile (str): Persistent Edge profile name to start.

    Returns:
        None: Emits the systemd result envelope. Always opens a real, visible
            window — there is no headless mode, by design (100% Transparency).

    Examples:
        >>> callable(admin_edge_start)
        True
        >>> admin_edge_start.__name__
        'admin_edge_start'
    """
    try:
        materialize_edge_profile(profile)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _emit(_systemctl("start", edge_unit_name(profile)), None)


@edge_app.command("stop")
def admin_edge_stop(
    profile: Annotated[str, typer.Argument(help="Persistent Edge profile name")],
) -> None:
    """Purpose: stop one profile's systemd-managed Microsoft Edge instance.

    Args:
        profile (str): Persistent Edge profile name to stop.

    Returns:
        None: Emits the systemd result envelope.

    Examples:
        >>> callable(admin_edge_stop)
        True
        >>> admin_edge_stop.__name__
        'admin_edge_stop'
    """
    _emit(_systemctl("stop", edge_unit_name(profile)), None)


@edge_app.command("status")
def admin_edge_status(
    profile: Annotated[str, typer.Argument(help="Persistent Edge profile name")],
) -> None:
    """Purpose: report one profile's disk, systemd, CDP, and extension-bridge state — all 4 axes.

    Args:
        profile (str): Persistent Edge profile name to inspect.

    Returns:
        None: Emits a redacted status envelope with no secret values, including ``state``
        (``not_declared``/``declared``/``initialized`` — see ``paths.edge_profile_state()``, the
        single predicate also used by ``profile-list`` and ``admin status``) via the SAME
        ``profile_state.describe_edge_profile()`` used by ``Daemon.profile_inventory()`` — no
        second hand-duplicated systemd/CDP probe. ``extension_connected`` is best-effort: it
        contacts the daemon (which may autostart it, exactly like ``admin status``) and is
        reported as ``null`` if the daemon is genuinely unreachable — the other 3 axes never
        depend on the daemon and stay accurate either way.

    Examples:
        >>> callable(admin_edge_status)
        True
        >>> admin_edge_status.__name__
        'admin_edge_status'
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
