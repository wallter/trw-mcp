"""Grok Build CLI bootstrap: MCP merge, AGENTS.md, uninstall strip-not-delete."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import tomllib

from tests._ide_detection_isolation import isolate_ide_detection
from trw_mcp.bootstrap import init_project
from trw_mcp.bootstrap._grok import generate_grok_config, merge_grok_config
from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.server._subcommands import _run_uninstall


@pytest.fixture(autouse=True)
def _isolate_ide_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    isolate_ide_detection(monkeypatch)


def _seed_project_venv(target: Path) -> None:
    launcher = target / ".venv" / "bin" / "trw-mcp"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)


@pytest.mark.unit
def test_grok_profile_is_not_the_claude_code_fallback() -> None:
    profile = resolve_client_profile("grok")
    assert profile.client_id == "grok"
    assert profile.write_targets.agents_md is True
    assert profile.write_targets.instruction_path == "AGENTS.md"
    assert profile.hooks_enabled is False


@pytest.mark.unit
def test_merge_uses_relative_venv_launcher_and_preserves_user_keys(tmp_path: Path) -> None:
    _seed_project_venv(tmp_path)
    existing = {
        "permission": {"mode": "ask"},
        "mcp_servers": {
            "other": {"command": "echo", "args": ["hi"]},
            "trw": {
                "command": "trw-mcp",
                "args": ["--debug"],
                "env": {"TRW_SESSION_ID": "keep-me"},
                "startup_timeout_sec": 45,
            },
        },
        "models": {"default": "must-not-be-written-by-us"},
    }
    merged = merge_grok_config(existing, target_dir=tmp_path)
    trw = merged["mcp_servers"]["trw"]  # type: ignore[index]
    assert trw["command"] == ".venv/bin/trw-mcp"
    assert trw["args"] == []
    assert trw["enabled"] is True
    assert "--debug" not in trw["args"]
    assert not str(trw["command"]).startswith("/")
    assert trw["env"] == {"TRW_SESSION_ID": "keep-me"}
    assert trw["startup_timeout_sec"] == 45
    assert merged["mcp_servers"]["other"]["command"] == "echo"  # type: ignore[index]
    assert merged["permission"] == {"mode": "ask"}
    # User-authored [models] is preserved, not invented.
    assert merged["models"] == {"default": "must-not-be-written-by-us"}


@pytest.mark.unit
def test_generate_does_not_write_models_or_ui(tmp_path: Path) -> None:
    _seed_project_venv(tmp_path)
    result = generate_grok_config(tmp_path)
    assert not result.get("errors"), result
    raw = (tmp_path / ".grok" / "config.toml").read_text(encoding="utf-8")
    data = tomllib.loads(raw)
    assert "models" not in data
    assert "ui" not in data
    assert data["mcp_servers"]["trw"]["command"] == ".venv/bin/trw-mcp"


@pytest.mark.unit
def test_init_project_ide_grok_writes_mcp_agents_and_ceremony(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    _seed_project_venv(tmp_path)

    result = init_project(tmp_path, ide="grok")
    assert not result["errors"], result["errors"]

    config_yaml = (tmp_path / ".trw" / "config.yaml").read_text(encoding="utf-8")
    assert "grok" in config_yaml
    assert resolve_client_profile("grok").client_id == "grok"

    grok_toml = tomllib.loads((tmp_path / ".grok" / "config.toml").read_text(encoding="utf-8"))
    command = grok_toml["mcp_servers"]["trw"]["command"]
    assert command == ".venv/bin/trw-mcp"
    assert not command.startswith("/")
    assert grok_toml["mcp_servers"]["trw"]["args"] == []

    agents_md = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "<!-- trw:start -->" in agents_md
    assert "trw_session_start" in agents_md
    assert "trw_deliver" in agents_md

    grok_agents = tmp_path / ".grok" / "agents"
    assert grok_agents.is_dir()
    assert any(grok_agents.glob("trw-*.md"))
    # Must not share antigravity-cli's destination.
    assert not (tmp_path / ".agents" / "agents").exists() or not any((tmp_path / ".agents" / "agents").glob("trw-*.md"))


@pytest.mark.unit
def test_uninstall_strips_only_trw_mcp_server(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    _seed_project_venv(tmp_path)
    result = init_project(tmp_path, ide="grok")
    assert not result["errors"], result["errors"]

    config_path = tmp_path / ".grok" / "config.toml"
    existing = tomllib.loads(config_path.read_text(encoding="utf-8"))
    existing["permission"] = {"mode": "ask"}
    existing["mcp_servers"]["other"] = {"command": "echo", "args": ["keep"]}
    from trw_mcp.bootstrap._codex_toml import _toml_dumps

    config_path.write_text(_toml_dumps(existing), encoding="utf-8")

    _run_uninstall(argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=True))

    assert config_path.exists()
    leftover = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "trw" not in leftover.get("mcp_servers", {})
    assert leftover["mcp_servers"]["other"]["command"] == "echo"
    assert leftover["permission"] == {"mode": "ask"}
    assert not (tmp_path / ".grok" / "agents").exists()


# --------------------------------------------------------------------------- #
# Adversarial audit 2026-09-19 (C1/C2/C4/C8): the write paths, not the happy path.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_update_does_not_scaffold_grok_for_a_project_that_never_asked(tmp_path: Path) -> None:
    """C1: the update gates on the RECORDED targets, not on a bare .grok/ directory.

    resolve_ide_targets falls through to detect_ide, which fires on ``.grok/`` --
    the directory this function itself creates -- so a project whose record says
    codex would still get .grok/config.toml and the AGENTS.md block, and that
    output would be the next run's "evidence". _update_targets asks the record
    first, which is what every other client's update does.
    """
    from trw_mcp.bootstrap._grok import update_grok_artifacts

    (tmp_path / ".git").mkdir()
    _seed_project_venv(tmp_path)
    init_project(tmp_path, ide="codex")  # the record says codex, and only codex
    (tmp_path / ".grok").mkdir(exist_ok=True)  # the user's own grok dir
    before = (tmp_path / "AGENTS.md").read_text(encoding="utf-8") if (tmp_path / "AGENTS.md").exists() else None
    result: dict[str, list[str]] = {"created": [], "updated": [], "errors": []}

    update_grok_artifacts(tmp_path, result, None, None)

    assert not (tmp_path / ".grok" / "config.toml").exists()
    after = (tmp_path / "AGENTS.md").read_text(encoding="utf-8") if (tmp_path / "AGENTS.md").exists() else None
    assert after == before
    assert result == {"created": [], "updated": [], "errors": []}


@pytest.mark.unit
@pytest.mark.parametrize("force", [False, True])
def test_a_malformed_user_config_is_never_truncated(tmp_path: Path, force: bool) -> None:
    """C2: force rewrites TRW's own keys; it is not licence to delete the file.

    Under force, a parse failure used to fall back to ``existing = {}``, so one
    typo silently dropped [permission], other MCP servers and every other table.
    """
    (tmp_path / ".git").mkdir()
    _seed_project_venv(tmp_path)
    config_path = tmp_path / ".grok" / "config.toml"
    config_path.parent.mkdir(parents=True)
    broken = '[permission]\nmode = "ask\n[mcp_servers.other]\ncommand = "echo"\n'
    config_path.write_text(broken, encoding="utf-8")

    result = generate_grok_config(tmp_path, force=force)

    assert result["errors"], "a file TRW cannot parse must be reported, not overwritten"
    assert config_path.read_text(encoding="utf-8") == broken, "the user's bytes are untouched"


@pytest.mark.unit
def test_an_unrenderable_toml_value_is_an_error_not_a_crash(tmp_path: Path) -> None:
    """C4: _toml_value covers a subset, so an unknown type raises rather than emit bad TOML."""
    import datetime

    from trw_mcp.bootstrap import _grok

    (tmp_path / ".git").mkdir()
    _seed_project_venv(tmp_path)
    (tmp_path / ".grok").mkdir()
    (tmp_path / ".grok" / "config.toml").write_text('[ui]\ntheme = "dark"\n', encoding="utf-8")

    def _with_a_datetime(existing: dict[str, object], *, target_dir: Path) -> dict[str, object]:
        return {"ui": {"last_seen": datetime.datetime(2026, 9, 19, tzinfo=datetime.timezone.utc)}}

    _grok_merge = _grok.merge_grok_config
    try:
        _grok.merge_grok_config = _with_a_datetime  # type: ignore[assignment]
        result = generate_grok_config(tmp_path)
    finally:
        _grok.merge_grok_config = _grok_merge  # type: ignore[assignment]

    assert result["errors"] and "Failed to write" in result["errors"][0]


@pytest.mark.unit
def test_install_forwards_the_preserved_bucket(tmp_path: Path) -> None:
    """C8: a preserved path reported by a writer must reach the caller's payload."""
    from trw_mcp.bootstrap import _grok

    (tmp_path / ".git").mkdir()
    _seed_project_venv(tmp_path)
    result: dict[str, list[str]] = {"created": [], "updated": [], "errors": []}

    def _preserving(target_dir: Path, *, force: bool = False) -> dict[str, list[str]]:
        return {"created": [], "updated": [], "preserved": ["AGENTS.md"], "errors": []}

    original = _grok.generate_grok_agents_md
    try:
        _grok.generate_grok_agents_md = _preserving  # type: ignore[assignment]
        _grok.install_grok_artifacts(tmp_path, False, result, None)
    finally:
        _grok.generate_grok_agents_md = original  # type: ignore[assignment]

    assert "AGENTS.md" in result.get("preserved", [])


@pytest.mark.unit
def test_detection_needs_the_trw_server_not_just_a_grok_directory(tmp_path: Path) -> None:
    """Audit addendum: every grok user has .grok/ (skills, hooks, config) without TRW.

    Keying detection on the directory made update-project scaffold TRW's grok
    artifacts into projects that never asked, and TRW's own output then read as
    evidence on the next run. grok's INTEGRATION-GUIDE forbids dir-only detection.
    """
    from trw_mcp.bootstrap._utils import detect_ide

    (tmp_path / ".git").mkdir()
    grok_dir = tmp_path / ".grok"
    (grok_dir / "skills").mkdir(parents=True)
    assert "grok" not in detect_ide(tmp_path), "a bare .grok/ is not evidence of TRW"

    config = grok_dir / "config.toml"
    config.write_text('[permission]\nmode = "ask"\n', encoding="utf-8")
    assert "grok" not in detect_ide(tmp_path), "someone else's grok config is not evidence either"

    config.write_text('[mcp_servers.trw]\ncommand = ".venv/bin/trw-mcp"\n', encoding="utf-8")
    assert "grok" in detect_ide(tmp_path), "the TRW server entry IS the evidence"

    config.write_text('[mcp_servers.trw]\ncommand = "oops\n', encoding="utf-8")
    assert "grok" not in detect_ide(tmp_path), "unparseable TOML is not evidence"


# --------------------------------------------------------------------------- #
# G1 (installer refinement 5.1.0): update-project --ide <new-client>
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_update_project_ide_grok_provisions_full_surface_in_one_run(tmp_path: Path) -> None:
    """G1: a brand-new ``--ide grok`` run must materialize the full client
    surface on the FIRST run, not require a second identical run.

    Root cause: ``_update_agents`` read ``resolve_client_write_targets(target_dir)``
    without ``ide_override``, so it only saw clients already RECORDED in
    ``.trw/config.yaml`` — target_platforms registration for the new client
    runs LATER in the same invocation (``_run_post_update_phases``). A first
    run wrote only the grok config file; agents needed a second run to appear
    (reproduced live: ``Changes: 0 updated, 2 created, 7 preserved`` on run 1,
    the full agent set only on run 2).
    """
    from trw_mcp.bootstrap import update_project

    (tmp_path / ".git").mkdir()
    _seed_project_venv(tmp_path)
    result = init_project(tmp_path, ide="claude-code")
    assert not result["errors"], result["errors"]

    update_result = update_project(tmp_path, ide="grok")
    assert not update_result["errors"], update_result["errors"]

    grok_agents = tmp_path / ".grok" / "agents"
    assert grok_agents.is_dir(), "grok's agent surface must exist after ONE update-project --ide grok run"
    agent_files = sorted(p.name for p in grok_agents.glob("trw-*.md"))
    assert agent_files, "grok's agents must be materialized on the first --ide grok run, not the second"

    config_yaml = (tmp_path / ".trw" / "config.yaml").read_text(encoding="utf-8")
    assert "grok" in config_yaml
