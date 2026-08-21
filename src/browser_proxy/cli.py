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
    """Parse one inline JSON object or a path to one JSON object.

    Args:
        value: JSON text or a path to a JSON file.

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
    """Print one complete proxy envelope and optionally persist the same JSON.

    Args:
        envelope: Pydantic proxy envelope returned by the daemon.
        output: Optional requested output path.

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
    """Display registry-backed grouped action names.

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
        """Dispatch one documented registry action using one JSON payload.

        Args:
            payload: Inline JSON object or JSON file path.
            output: Optional file that receives the full result envelope.
            _action_name: Bound registry name.

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
    """Report daemon-owned Edge profile endpoints.

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
    """Run a non-interactive user-systemd operation and return a stable envelope."""
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
    """Link the repository-owned user service and socket units, then enable the socket."""
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
    """Enable the socket listener and verify daemon activation with a ping."""
    result = _systemctl("start", "browser-proxy.socket")
    if result.meta.status == "ok":
        result = asyncio.run(request("ping", {}))
    _emit(result, None)


@admin_app.command("stop")
def admin_stop() -> None:
    """Request graceful daemon shutdown.

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
    """Report Edge binary, bridge pairing, and daemon connectivity without secrets."""
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
    """Rotate the protected local extension capability without printing its secret."""
    ExtensionBridge().pair()
    _emit(
        Envelope.ok({"paired": True, "bridge": "ws://127.0.0.1 (capability stored locally)"}), None
    )


@app.command("daemon", hidden=True)
def daemon() -> None:
    """Run the managed daemon entrypoint used only by systemd.

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
