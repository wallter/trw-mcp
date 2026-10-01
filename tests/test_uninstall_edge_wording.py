"""INC-122 (swarm-e2e S13-B2): uninstall's kept/refused/removed lines say what is true and what to do.

Safety held in every case (user bytes kept, no write through a symlink); the text and counts did not:

* a user-edited ``REVIEW.md`` was kept with "run update-project first to record it", a remedy that cannot work
  (``update-project`` keeps an edited file as the user's and never records it);
* the refusal for a symlinked FILE told the user to "replace each symlink with a real directory";
* a skill directory the plan itself listed as "(already gone)" was then printed "Removed:" and counted.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]


def _ns(project: Path) -> argparse.Namespace:
    return argparse.Namespace(
        target_dir=str(project), dry_run=False, yes=True, user_tier=False, keep_memory=False, ide=None
    )


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    assert not init_project(project, ide="claude-code")["errors"]
    return project


def test_a_kept_user_edited_review_md_names_no_remedy_that_cannot_work(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    review = project / "REVIEW.md"
    review.write_text(review.read_text(encoding="utf-8") + "\nMy own review rule.\n", encoding="utf-8")

    _run_uninstall(_ns(project))

    kept = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("  Kept: REVIEW.md"))
    assert "update-project" not in kept
    assert "delete it yourself" in kept
    assert "My own review rule." in review.read_text(encoding="utf-8")


def test_refusing_a_symlinked_file_says_to_replace_it_with_a_real_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("the user's own bytes\n", encoding="utf-8")
    (project / "REVIEW.md").unlink()
    (project / "REVIEW.md").symlink_to(outside)

    with pytest.raises(SystemExit):
        _run_uninstall(_ns(project))

    fix = next(line for line in capsys.readouterr().err.splitlines() if line.strip().startswith("Fix:"))
    assert "real file" in fix
    assert "real directory" not in fix
    assert outside.read_text(encoding="utf-8") == "the user's own bytes\n"


def test_refusing_a_symlinked_directory_still_says_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside-skills"
    shutil.move(str(project / ".claude" / "skills"), str(outside))
    (project / ".claude" / "skills").symlink_to(outside)

    with pytest.raises(SystemExit):
        _run_uninstall(_ns(project))

    fix = next(line for line in capsys.readouterr().err.splitlines() if line.strip().startswith("Fix:"))
    assert "real directory" in fix


def test_a_skill_dir_that_was_already_gone_is_not_reported_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    shutil.rmtree(project / ".claude" / "skills" / "trw-deliver")

    _run_uninstall(_ns(project))

    out = capsys.readouterr().out
    assert "trw-deliver (already gone)" in out, "precondition: the plan lists it as already gone"
    assert "Removed: .claude/skills/trw-deliver" not in out
