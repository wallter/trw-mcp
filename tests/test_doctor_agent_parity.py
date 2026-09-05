"""PRD-CORE-252-FR05: ``trw-mcp doctor`` reports bundled-agent parity per client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.agents.agent_formats import agent_format_for
from trw_mcp.bootstrap._init_project_skills import _install_agents
from trw_mcp.models.config import TRWConfig
from trw_mcp.server._doctor_agent_parity import agent_parity_report
from trw_mcp.server._subcommands_doctor import _check_agent_parity, _doctor_core

BUNDLED_AGENTS_DIR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "agents"


def _project(tmp_path: Path, *clients: str) -> Path:
    """A project tree whose recorded clients are *clients*, with agents installed."""
    (tmp_path / ".trw").mkdir(parents=True, exist_ok=True)
    platforms = "\n".join(f"  - {client}" for client in clients)
    (tmp_path / ".trw" / "config.yaml").write_text(f"target_platforms:\n{platforms}\n", encoding="utf-8")
    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}
    _install_agents(tmp_path, force=False, result=result, clients=list(clients))
    return tmp_path


@pytest.mark.unit
def test_pass_warn_and_skip_paths(tmp_path: Path) -> None:
    """The three verdicts, produced by three real trees.

    Fails against HEAD: no ``agent_parity`` check existed, so a user missing
    seven specialists saw a clean run.
    """
    complete = _project(tmp_path / "complete", "cursor-ide", "codex")
    status, message, rows = agent_parity_report(complete)
    assert status == "PASS", message
    assert all(row["installed"] == row["expected"] for row in rows)

    # WARN: delete one installed agent and the message must name it AND its client.
    short = _project(tmp_path / "short", "codex")
    fmt = agent_format_for("codex")
    (short / fmt.destination_for("trw-auditor")).unlink()
    status, message, rows = agent_parity_report(short)
    assert status == "WARN", message
    assert "trw-auditor" in message and "codex" in message
    codex_row = next(row for row in rows if row["client"] == "codex")
    assert codex_row["missing"] == ["trw-auditor"]
    assert codex_row["installed"] == int(str(codex_row["expected"])) - 1

    # SKIP: the only selected client has no agent surface.
    cli_only = _project(tmp_path / "cli", "cursor-cli")
    status, message, rows = agent_parity_report(cli_only)
    assert status == "SKIP", message
    assert "cursor-cli" in message and "no agent surface" in message
    assert rows and rows[0]["supported"] is False


@pytest.mark.unit
def test_json_row_carries_installed_and_expected_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """FR05: the result is machine-readable, not only a prose message."""
    import argparse

    from trw_mcp.server._subcommands_doctor import _run_doctor

    project = _project(tmp_path, "copilot")
    args = argparse.Namespace(target_dir=str(project), format="json", fix=False)
    with pytest.raises(SystemExit):
        _run_doctor(args)

    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    row = next(check for check in payload["checks"] if check["name"] == "agent_parity")
    assert row["status"] == "PASS"
    per_client = row["data"]
    assert per_client and per_client[0]["client"] == "copilot"
    assert per_client[0]["installed"] == per_client[0]["expected"]
    assert per_client[0]["expected"] == len(list(BUNDLED_AGENTS_DIR.glob("*.md")))


@pytest.mark.unit
def test_the_check_is_registered_in_the_catalogue(tmp_path: Path) -> None:
    """A check nobody runs reports nothing; assert it is actually dispatched."""
    project = _project(tmp_path, "cursor-ide")
    names = [check.name for check in _doctor_core(project, TRWConfig())]
    assert "agent_parity" in names


@pytest.mark.unit
def test_the_check_never_fails_the_run(tmp_path: Path) -> None:
    """Boundary: WARN, never FAIL — a missing agent does not break the install."""
    project = _project(tmp_path, "cursor-ide")
    for path in (project / str(agent_format_for("cursor-ide").destination_dir)).iterdir():
        path.unlink()
    assert _check_agent_parity(project, TRWConfig()).status == "WARN"


@pytest.mark.unit
def test_a_user_authored_agent_is_not_reported_as_surplus(tmp_path: Path) -> None:
    """Boundary: the check asks what is missing, not what is unfamiliar."""
    project = _project(tmp_path, "cursor-ide")
    dest = project / str(agent_format_for("cursor-ide").destination_dir)
    (dest / "my-own-agent.md").write_text("---\nname: my-own-agent\n---\n", encoding="utf-8")

    status, message, _ = agent_parity_report(project)
    assert status == "PASS", message
    assert "my-own-agent" not in message


@pytest.mark.unit
def test_an_unregistered_recorded_client_is_reported_not_dropped(tmp_path: Path) -> None:
    """A stale ``target_platforms`` entry must be visible rather than invisible."""
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("target_platforms:\n  - aider\n", encoding="utf-8")

    status, _, rows = agent_parity_report(tmp_path)
    assert status == "SKIP"
    assert rows[0]["client"] == "aider"
    assert rows[0]["supported"] is False
