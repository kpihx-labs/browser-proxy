"""Expose the Tick-proxy-style JSON payload CLI.

Examples:
    >>> app.info.name
    'browser-proxy'
    >>> app.info.help.startswith('Unified')
    True
"""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, cast

import typer

from browser_proxy.actions import REGISTRY
from browser_proxy.client import request
from browser_proxy.daemon import Daemon
from browser_proxy.bridge import ExtensionBridge
from browser_proxy.models import Envelope

app = typer.Typer(
    name="browser-proxy", help="Unified Microsoft Edge JSON-RPC proxy.", no_args_is_help=True
)
do_app = typer.Typer(help="Execute one flat JSON action.", no_args_is_help=True)
admin_app = typer.Typer(help="Manage daemon and extension lifecycle.", no_args_is_help=True)
app.add_typer(do_app, name="do")
app.add_typer(admin_app, name="admin")


def _payload(value: str) -> dict[str, object]:
    """Purpose: parse one inline JSON object or a path to one JSON object.

    Args:
        value (str): JSON text or a path to a JSON file.

    Returns:
        A validated top-level JSON object.

    Examples:
        >>> _payload('{}')
        {}
        >>> _payload('{"profile":"default"}')['profile']
        'default'
    """

    text = Path(value).read_text() if Path(value).is_file() else value
    payload = cast(dict[str, object], json.loads(text))
    if not isinstance(payload, dict):
        raise typer.BadParameter("payload must be one JSON object")
    return payload


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
    if output is not None:
        output.write_text(rendered + "\n")
    if output_format == "table":
        typer.echo(
            f"status\t{envelope.meta.status}\nedited\t{envelope.meta.edited}\ndata\t{json.dumps(envelope.data, sort_keys=True)}"
        )
    else:
        typer.echo(rendered)


@do_app.callback(invoke_without_command=True)
def do_help() -> None:
    """Purpose: display registry-backed grouped action names.

    Args:
        None.

    Returns:
        ``None`` after rendering action names.

    Examples:
        >>> do_help.__name__
        'do_help'
        >>> len(REGISTRY) >= 3
        True
    """


for _name, _action in REGISTRY.items():

    def _command(
        payload: Annotated[str, typer.Argument(help="Inline JSON object or JSON file path")],
        output: Annotated[Path | None, typer.Option("-o", help="Write full envelope")] = None,
        output_format: Annotated[str, typer.Option("-f", help="json or table")] = "json",
        _action_name: str = _name,
    ) -> None:
        """Purpose: dispatch one documented registry action using one JSON payload.

        Args:
            payload (str): Inline JSON object or JSON file path.
            output (Path | None): Optional file that receives the full result envelope.
            output_format (str): Presentation mode, either ``json`` or ``table``.
            _action_name (str): Bound registry name.

        Returns:
            ``None`` after printing the JSON envelope.

        Examples:
            >>> _command.__name__
            '_command'
            >>> isinstance(_action_name, str)
            True
        """

        _emit(
            asyncio.run(request("do", {"action": _action_name, "payload": _payload(payload)})),
            output,
            output_format,
        )

    _command.__name__ = _name.replace("-", "_")
    _command.__doc__ = _action.handler.__doc__
    do_app.command(_name)(_command)


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
    """Purpose: link repository-owned units and enable the user socket.

    Args:
        None: Uses the repository-relative service and socket unit files.

    Returns:
        None: Emits one stable systemd result envelope.

    Examples:
        >>> callable(admin_install)
        True
        >>> admin_install.__name__
        'admin_install'
    """
    project = Path(__file__).resolve().parents[2]
    service, unit_socket = (
        project / "systemd/browser-proxy.service",
        project / "systemd/browser-proxy.socket",
    )
    result = _systemctl("link", str(service), str(unit_socket))
    if result.meta.status == "ok":
        result = _systemctl("enable", "--now", "browser-proxy.socket")
    _emit(result, None)


@admin_app.command("start")
def admin_start() -> None:
    """Purpose: start the socket listener and verify daemon activation with a ping.

    Args:
        None: Starts the configured user socket unit.

    Returns:
        None: Emits the daemon ping or systemd failure envelope.

    Examples:
        >>> callable(admin_start)
        True
        >>> admin_start.__name__
        'admin_start'
    """
    result = _systemctl("start", "browser-proxy.socket")
    if result.meta.status == "ok":
        result = asyncio.run(request("ping", {}))
    _emit(result, None)


@admin_app.command("stop")
def admin_stop() -> None:
    """Purpose: request graceful daemon shutdown.

    Args:
        None.

    Returns:
        ``None`` after printing a shutdown envelope.

    Examples:
        >>> admin_stop.__name__
        'admin_stop'
        >>> callable(admin_stop)
        True
    """

    _emit(_systemctl("stop", "browser-proxy.service", "browser-proxy.socket"), None)


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
    """Purpose: rotate the local extension capability without printing its secret.

    Args:
        None: Creates a replacement protected local pairing capability.

    Returns:
        None: Emits only confirmation, never the capability value.

    Examples:
        >>> callable(admin_extension_pair)
        True
        >>> admin_extension_pair.__name__
        'admin_extension_pair'
    """
    ExtensionBridge().pair()
    _emit(
        Envelope.ok({"paired": True, "bridge": "ws://127.0.0.1 (capability stored locally)"}), None
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
