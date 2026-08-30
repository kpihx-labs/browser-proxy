"""User-facing ``do`` payload, help, autosave, and bounded-preview tests."""

import json
from pathlib import Path

from browser_proxy import cli
from browser_proxy.doc import get_compact_help, get_full_help
from browser_proxy.models import Envelope


def test_payload_accepts_inline_json_file_and_omission(tmp_path: Path) -> None:
    """The same object is accepted inline, from a JSON path, or omitted as an empty object."""
    payload_path = tmp_path / "payload.json"
    payload_path.write_text('{"profile":"default"}')
    assert cli._payload('{"profile":"default"}') == {"profile": "default"}
    assert cli._payload(str(payload_path)) == {"profile": "default"}
    assert cli._payload(None) == {}


def test_do_result_path_uses_explicit_destination_or_timestamped_autosave(
    tmp_path: Path, monkeypatch
) -> None:
    """Every do result has exactly one full-envelope destination visible to the caller."""
    monkeypatch.setattr(cli, "AUTOSAVE_DIR", tmp_path / "autosave")
    explicit = tmp_path / "chosen.json"
    assert cli._result_path("profile-list", explicit) == explicit
    autosave = cli._result_path("profile-list", None)
    assert autosave.parent == tmp_path / "autosave"
    assert autosave.name.startswith("profile-list_") and autosave.suffix == ".json"


def test_json_preview_keeps_n_over_two_leading_and_trailing_lines(
    monkeypatch, tmp_path: Path
) -> None:
    """Only do-result JSON is shortened; the persisted envelope remains complete elsewhere."""
    monkeypatch.setattr(cli, "PREVIEW_LINES", 4)
    preview = cli._preview_json(
        "\n".join(str(number) for number in range(8)), tmp_path / "full.json"
    )
    assert preview.splitlines() == [
        "0",
        "1",
        "… 4 JSON lines omitted; full envelope: " + str(tmp_path / "full.json") + " …",
        "6",
        "7",
    ]


def test_emit_do_persists_full_envelope_before_preview(tmp_path: Path, monkeypatch, capsys) -> None:
    """A large do envelope is written in full while terminal output names its complete file."""
    monkeypatch.setattr(cli, "AUTOSAVE_DIR", tmp_path)
    monkeypatch.setattr(cli, "PREVIEW_LINES", 4)
    envelope = Envelope.ok({"rows": [{"index": number} for number in range(10)]})
    cli._emit_do("sample", envelope, None, "json")
    captured = capsys.readouterr()
    paths = list(tmp_path.glob("sample_*.json"))
    assert len(paths) == 1
    assert json.loads(paths[0].read_text())["data"]["rows"][9]["index"] == 9
    assert "Full envelope:" in captured.err
    assert "JSON lines omitted" in captured.out


def test_full_help_is_user_facing_and_has_three_command_result_examples() -> None:
    """Generated help hides implementation context and gives three command-to-result examples."""
    handler = cli.REGISTRY["bookmark-list"].handler
    help_text = get_full_help(handler)
    assert "DaemonContext" not in help_text
    assert help_text.count("browser-proxy do bookmark-list") == 3
    assert "Parameters:" in help_text
    assert "→" in help_text
    compact = get_compact_help(handler)
    assert "Parameters:" in compact
    assert "Examples:" not in compact
