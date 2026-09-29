"""8.0: TRW agents no longer declare Claude Code ``memory:``; a leftover ``.claude/agent-memory`` is reported, never deleted.

trw_learn/trw_recall is the one durable agent memory. The directory holds agent-written notes, so neither update
nor doctor removes it: both name it, say to record what is worth keeping with trw_learn, and print a removal
command rooted at the project.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]


def _plant_agent_memory(repo: Path) -> Path:
    """TRW's implementer notes plus a project's own agent's notes; returns TRW's subdirectory."""
    for agent in ("trw-implementer", "my-own-agent"):
        notes = repo / ".claude" / "agent-memory" / agent
        notes.mkdir(parents=True)
        (notes / "MEMORY.md").write_text("- [a finding](finding.md)\n", encoding="utf-8")
        (notes / "finding.md").write_text("an agent-written note\n", encoding="utf-8")
    return repo / ".claude" / "agent-memory" / "trw-implementer"


def test_update_reports_but_never_deletes_agent_memory(tmp_path: Path) -> None:
    from trw_mcp.bootstrap import init_project, update_project

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert not init_project(repo, ide="claude-code")["errors"]
    assert not [w for w in update_project(repo)["warnings"] if "agent-memory" in w], "absent: no notice"

    trw_notes = _plant_agent_memory(repo)
    result = update_project(repo)

    assert (trw_notes / "finding.md").is_file(), "agent-written notes must survive update"
    (notice,) = [w for w in result["warnings"] if ".claude/agent-memory" in w]
    assert "my-own-agent" not in notice, "a project's own agent memory is not TRW's to advise removing"
    assert "trw_learn" in notice
    assert shlex.split(notice.split("remove it manually: ", 1)[1]) == ["rm", "-r", str(trw_notes.resolve())]
    installed = (repo / ".claude" / "agents" / "trw-implementer.md").read_text(encoding="utf-8")
    assert "\nmemory:" not in installed, "installed agents no longer opt into Claude Code agent memory"


def test_doctor_row_warns_with_the_migration_hint(tmp_path: Path) -> None:
    from trw_mcp.server._doctor_retired_artifacts import retired_artifact_row

    assert retired_artifact_row(tmp_path) == ("PASS", "no retired dead files present")
    trw_notes = _plant_agent_memory(tmp_path)

    status, message = retired_artifact_row(tmp_path)

    assert status == "WARN"
    assert ".claude/agent-memory/trw-implementer is retired" in message and "trw_learn" in message
    assert f"rm -r {shlex.quote(str(trw_notes.resolve()))}" in message
    assert "my-own-agent" not in message
    assert f"rm -r {shlex.quote(str(trw_notes.parent.resolve()))} " not in f"{message} ", "never the shared parent"


def test_a_project_whose_agent_memory_holds_only_its_own_agents_gets_no_row(tmp_path: Path) -> None:
    from trw_mcp.server._doctor_retired_artifacts import retired_artifact_row

    (tmp_path / ".claude" / "agent-memory" / "my-own-agent").mkdir(parents=True)

    assert retired_artifact_row(tmp_path)[0] == "PASS"
