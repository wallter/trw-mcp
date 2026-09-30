"""E2E-UPDATE-IDEMPOTENT: a second no-op `update-project` leaves AGENTS.md byte-stable and writes no backup."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.usefixtures("no_memory_daemon")
def test_a_second_update_project_writes_no_instruction_backup(tmp_path: Path) -> None:
    from trw_mcp.bootstrap import init_project, update_project

    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("# Team rules\n\nAlways use tabs.\n", encoding="utf-8")
    assert not init_project(tmp_path, ide="claude-code").get("errors")
    update_project(tmp_path)  # settle: the first update may legitimately normalise generated files

    def state() -> tuple[str, list[str]]:
        backups = sorted(p.name for p in (tmp_path / ".trw" / "backups" / "instructions").glob("*"))
        return (tmp_path / "AGENTS.md").read_text(encoding="utf-8"), backups

    settled = state()
    update_project(tmp_path)
    update_project(tmp_path)

    assert state() == settled
