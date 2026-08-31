"""Systemd-managed Edge lifecycle tests: deterministic ports, launch args, and start_profile."""

import asyncio
from typing import Any

import pytest

from browser_proxy.cli import edge_launch_args
from browser_proxy.daemon import Daemon
from browser_proxy.paths import (
    discover_edge_profiles,
    edge_cdp_port,
    edge_profile_dir,
    edge_profile_root,
    edge_profile_state,
    materialize_edge_profile,
)
from browser_proxy.profile_state import profile_unit_name


def test_edge_cdp_port_is_deterministic_and_bounded() -> None:
    """The same profile name always resolves to the same in-range port."""
    assert edge_cdp_port("test") == edge_cdp_port("test")
    assert edge_cdp_port("test") != edge_cdp_port("other")
    assert 33000 <= edge_cdp_port("test") < 42000


def test_edge_cdp_port_honors_test_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """BROWSER_PROXY_EDGE_PORT overrides the deterministic hash-derived port."""
    monkeypatch.setenv("BROWSER_PROXY_EDGE_PORT", "40001")
    assert edge_cdp_port("anything") == 40001


def test_edge_profile_dir_nests_under_the_configured_root(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A named profile directory always lives directly under the profile root."""
    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path))
    assert edge_profile_dir("test") == edge_profile_root() / "test"
    assert edge_profile_dir("test").parent == tmp_path


def test_edge_unit_naming_is_a_single_template_per_profile() -> None:
    """Every profile maps to exactly one templated unit — no headless/visible split."""
    assert profile_unit_name("test") == "browser-proxy-profile@test.service"
    assert profile_unit_name("research") == "browser-proxy-profile@research.service"


def test_edge_profile_state_distinguishes_not_declared_declared_and_initialized(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one real/declared predicate used everywhere: a `mkdir`'d directory is only
    "declared" until Edge itself writes its "Local State" marker at least once."""
    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    never_declared = edge_profile_dir("never-created")
    assert edge_profile_state(never_declared) == "not_declared"
    declared_only = materialize_edge_profile("declared-only")
    assert edge_profile_state(declared_only) == "declared"
    (declared_only / "Local State").write_text("{}")
    assert edge_profile_state(declared_only) == "initialized"


def test_profile_discovery_reads_disk_without_creating_the_root(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Profile identity is discovered from profile directories, never daemon memory."""
    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    assert discover_edge_profiles() == ()
    assert not (tmp_path / "profiles").exists()
    materialize_edge_profile("default")
    materialize_edge_profile("research")
    assert [path.name for path in discover_edge_profiles()] == ["default", "research"]


def test_materialize_edge_profile_rejects_a_case_insensitive_collision(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: 'Default' and 'default' previously coexisted as two unrelated
    directories nobody intended to create separately — the filesystem is case-sensitive but
    humans routinely are not."""
    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    materialize_edge_profile("default")
    with pytest.raises(ValueError, match="collides case-insensitively"):
        materialize_edge_profile("Default")
    assert [path.name for path in discover_edge_profiles()] == ["default"]
    # Restarting the SAME profile again (not a collision with itself) must still succeed.
    materialize_edge_profile("default")


def test_profile_inventory_survives_a_fresh_daemon_and_reports_live_state(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new daemon rediscovers persistent profiles instead of relying on a prior cache."""
    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    materialize_edge_profile("default")

    async def unit_active(unit: str) -> bool:
        return unit == "browser-proxy-profile@default.service"

    async def call(self: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "Browser.getVersion"
        assert params == {}
        return {"product": "Microsoft Edge/140"}

    from browser_proxy.cdp import CdpBrowser

    monkeypatch.setattr("browser_proxy.profile_state.is_profile_unit_active", unit_active)
    monkeypatch.setattr(CdpBrowser, "call", call)
    inventory = asyncio.run(Daemon().profile_inventory())
    assert inventory == [
        {
            "name": "default",
            "profile_dir": str(tmp_path / "profiles/default"),
            "state": "declared",
            "cdp_port": edge_cdp_port("default"),
            "systemd_active": True,
            "cdp_reachable": True,
            "extension_connected": False,
        }
    ]


def test_edge_launch_args_never_hide_the_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """No flag exists to suppress the window: 100% Transparency, always visible."""
    monkeypatch.setenv("BROWSER_PROXY_EDGE_PATH", "/usr/bin/microsoft-edge")
    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path))
    args = edge_launch_args("test")
    assert "--no-startup-window" not in args
    assert args[0] == "/usr/bin/microsoft-edge"
    assert any(arg.startswith("--remote-debugging-port=") for arg in args)
    assert (tmp_path / "test").is_dir()


def test_edge_launch_args_rejects_path_traversal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A profile name cannot escape the managed profile root."""
    monkeypatch.setenv("BROWSER_PROXY_EDGE_PATH", "/usr/bin/microsoft-edge")
    with pytest.raises(Exception, match="invalid profile name"):
        edge_launch_args("../escape")


def test_start_profile_reuses_an_already_active_systemd_unit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """An already-active Edge unit is polled directly without a systemctl start call."""
    started_calls: list[list[str]] = []

    async def call(self: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"product": "Microsoft Edge/140"}

    async def unit_active(unit: str) -> bool:
        return unit == "browser-proxy-profile@test.service"

    def fake_run(argv: list[str], **_kwargs: Any) -> Any:
        started_calls.append(argv)
        raise AssertionError("systemctl start must not be called when the unit is already active")

    from browser_proxy.cdp import CdpBrowser

    monkeypatch.setattr(CdpBrowser, "call", call)
    monkeypatch.setattr("browser_proxy.daemon.is_profile_unit_active", unit_active)
    monkeypatch.setattr("browser_proxy.daemon.subprocess.run", fake_run)

    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    daemon = Daemon()
    port = asyncio.run(daemon.start_profile("test"))
    assert port == edge_cdp_port("test")
    assert edge_profile_dir("test").is_dir()
    assert started_calls == []


def test_start_profile_starts_an_inactive_unit_then_polls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """An inactive Edge unit is started via systemctl before the CDP readiness poll."""
    started_calls: list[list[str]] = []

    async def call(self: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"product": "Microsoft Edge/140"}

    async def unit_active(unit: str) -> bool:
        return False

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(argv: list[str], **_kwargs: Any) -> Any:
        started_calls.append(argv)
        return _Result()

    from browser_proxy.cdp import CdpBrowser

    monkeypatch.setattr(CdpBrowser, "call", call)
    monkeypatch.setattr("browser_proxy.daemon.is_profile_unit_active", unit_active)
    monkeypatch.setattr("browser_proxy.daemon.subprocess.run", fake_run)

    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    daemon = Daemon()
    port = asyncio.run(daemon.start_profile("test"))
    assert port == edge_cdp_port("test")
    assert edge_profile_dir("test").is_dir()
    assert started_calls == [["systemctl", "--user", "start", "browser-proxy-profile@test.service"]]


def test_start_profile_surfaces_a_failed_systemctl_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A failing systemctl start raises PROFILE_UNAVAILABLE before any CDP poll."""

    async def unit_active(unit: str) -> bool:
        return False

    class _Result:
        returncode = 1
        stderr = "Unit browser-proxy-profile@test.service not found."

    def fake_run(argv: list[str], **_kwargs: Any) -> Any:
        return _Result()

    monkeypatch.setattr("browser_proxy.daemon.is_profile_unit_active", unit_active)
    monkeypatch.setattr("browser_proxy.daemon.subprocess.run", fake_run)

    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    daemon = Daemon()
    with pytest.raises(RuntimeError, match="PROFILE_UNAVAILABLE"):
        asyncio.run(daemon.start_profile("test"))
    assert edge_profile_dir("test").is_dir()


def test_start_profile_rejects_unsafe_profile_names() -> None:
    """A traversal-shaped profile name is rejected before any systemd interaction."""
    with pytest.raises(ValueError, match="invalid profile name"):
        asyncio.run(Daemon().start_profile("../escape"))


def test_describe_edge_profile_probes_cdp_unconditionally(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The canonical describer trusts a real CDP round-trip over systemd's reported state — it
    must attempt the probe even when `systemd_active` is False (a manually-launched, non-systemd
    Edge on that same port is still honestly reported as reachable)."""
    from browser_proxy.cdp import CdpBrowser
    from browser_proxy.profile_state import describe_edge_profile

    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    materialize_edge_profile("test")

    async def unit_active(unit: str) -> bool:
        return False

    async def call(self: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"product": "Microsoft Edge/140"}

    monkeypatch.setattr("browser_proxy.profile_state.is_profile_unit_active", unit_active)
    monkeypatch.setattr(CdpBrowser, "call", call)
    description = asyncio.run(describe_edge_profile("test"))
    assert description["systemd_active"] is False
    assert description["cdp_reachable"] is True


def test_remove_profile_stops_an_active_unit_then_trashes_the_directory(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`remove_profile` stops the unit if active, then calls `trash-put` by absolute path — never
    a permanent `shutil.rmtree`, and never relying on `$PATH` resolving the `rm` wrapper."""
    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    profile_dir = materialize_edge_profile("test")
    calls: list[list[str]] = []

    async def unit_active(unit: str) -> bool:
        return True

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(argv: list[str], **_kwargs: Any) -> Any:
        calls.append(argv)
        return _Result()

    monkeypatch.setattr("browser_proxy.daemon.is_profile_unit_active", unit_active)
    monkeypatch.setattr("browser_proxy.daemon.subprocess.run", fake_run)
    monkeypatch.setattr("browser_proxy.daemon.shutil.which", lambda _name: "/usr/bin/trash-put")

    result = asyncio.run(Daemon().remove_profile("test"))
    assert result == {
        "profile": "test",
        "removed": True,
        "was_active": True,
        "trashed_path": str(profile_dir),
    }
    assert calls == [
        ["systemctl", "--user", "stop", "browser-proxy-profile@test.service"],
        ["/usr/bin/trash-put", str(profile_dir)],
    ]


def test_remove_profile_rejects_a_never_declared_profile(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing to remove: fails closed by name instead of silently succeeding on a no-op."""
    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    with pytest.raises(RuntimeError, match="PROFILE_UNAVAILABLE: ghost"):
        asyncio.run(Daemon().remove_profile("ghost"))


def test_remove_profile_refuses_to_permanently_delete_when_trash_put_is_missing(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing `trash-put` binary is a hard failure — never a silent fallback to permanent
    deletion (0 Trust: a "safe delete" feature must never quietly become an unsafe one)."""
    monkeypatch.setenv("BROWSER_PROXY_PROFILE_ROOT", str(tmp_path / "profiles"))
    profile_dir = materialize_edge_profile("test")

    async def unit_active(unit: str) -> bool:
        return False

    monkeypatch.setattr("browser_proxy.daemon.is_profile_unit_active", unit_active)
    monkeypatch.setattr("browser_proxy.daemon.shutil.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="trash-put"):
        asyncio.run(Daemon().remove_profile("test"))
    assert profile_dir.is_dir()


def test_profile_remove_action_has_a_locked_identity_but_no_extension_approval() -> None:
    """`profile-remove` is deliberately admin-tier, not extension-approval-gated: the most likely
    removal candidates (orphaned/never-initialized profiles) have no reachable extension to ask.
    Safety instead comes from the mandatory named `profile` preflight identity."""
    from browser_proxy.actions import REGISTRY

    policy = REGISTRY["profile-remove"].policy
    assert policy.approval is False
    assert policy.preflight_fields == ("profile",)
