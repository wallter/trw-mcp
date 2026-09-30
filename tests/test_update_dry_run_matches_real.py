"""E2E-INC-051 (b) and (c): the update-project preview reports what the real run reports, and user bytes after the block survive a sync."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path: Path) -> Path:
    from trw_mcp.bootstrap import init_project

    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True, timeout=30)
    assert not init_project(root, ide="claude-code").get("errors")
    return root


@pytest.mark.usefixtures("no_memory_daemon")
def test_the_dry_run_and_the_real_run_report_the_same_files(project: Path) -> None:
    from trw_mcp.bootstrap import update_project

    dry = update_project(project, dry_run=True)
    real = update_project(project)

    for key in ("created", "updated", "preserved", "cleaned"):
        assert sorted(dry.get(key, [])) == sorted(real.get(key, [])), key
    assert any(path.endswith(".trw/learnings/index.yaml") for path in dry["preserved"])


@pytest.mark.usefixtures("no_memory_daemon")
def test_instructions_sync_keeps_the_users_text_and_final_newline_after_the_block(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.state.claude_md import execute_claude_md_sync
    from trw_mcp.state.persistence import FileStateReader

    # The sync resolves its target from the project root, so point it at THIS project (not the checkout running the test).
    monkeypatch.chdir(project)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    agents = project / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + "\n<!-- user note -->\n", encoding="utf-8")
    before = agents.read_bytes()

    execute_claude_md_sync("root", None, get_config(), FileStateReader(), None, "claude-code")

    assert agents.read_bytes().endswith(b"<!-- user note -->\n")
    assert agents.read_bytes() == before
