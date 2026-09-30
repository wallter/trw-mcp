"""INC-080 (swarm-e2e s13): uninstall after the user COMMITS the init output leaves nothing of TRW's behind.

Once ``init-project`` output was committed and ``update-project`` ran, uninstall kept TRW's own ``REVIEW.md`` as
"not recorded by TRW; left in place -- run update-project first to record it" (a remedy that did not work), and
left empty ``.codex/`` and ``docs/`` directories. Without a commit, nothing was left. A REVIEW.md the user edited
must still be kept.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]

_GIT = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def _committed_codex_project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert not init_project(tmp_path, ide="codex")["errors"]
    subprocess.run(["git", "-C", str(tmp_path), *_GIT, "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), *_GIT, "commit", "-q", "--no-verify", "-m", "init"], check=True)
    assert not update_project(tmp_path, ide="codex")["errors"]
    return tmp_path


def _uninstall(project: Path) -> None:
    _run_uninstall(
        argparse.Namespace(
            target_dir=str(project), dry_run=False, yes=True, user_tier=False, keep_memory=False, ide=None
        )
    )


def test_uninstall_after_a_committed_init_removes_review_md_and_leaves_no_empty_dirs(tmp_path: Path) -> None:
    project = _committed_codex_project(tmp_path)
    assert (project / "REVIEW.md").is_file(), "precondition: init wrote REVIEW.md"

    _uninstall(project)

    assert not (project / "REVIEW.md").exists()
    for name in (".codex", "docs"):
        path = project / name
        assert not (path.is_dir() and not any(path.iterdir())), f"empty {name}/ left behind"


def test_a_review_md_the_user_edited_is_kept(tmp_path: Path) -> None:
    project = _committed_codex_project(tmp_path)
    review = project / "REVIEW.md"
    review.write_text(review.read_text(encoding="utf-8") + "\nMy own review rule.\n", encoding="utf-8")

    _uninstall(project)  # the edited file is the user's now: keeping it is the complete outcome

    assert "My own review rule." in review.read_text(encoding="utf-8")


def test_a_user_flag_line_inside_the_learnings_section_is_kept(tmp_path: Path) -> None:
    """Codex r1 P0 repro: a user rule written in TRW's own line format still makes the file the user's."""
    project = _committed_codex_project(tmp_path)
    review = project / "REVIEW.md"
    text = review.read_text(encoding="utf-8")
    anchor = next(
        line
        for line in text.splitlines()
        if line.startswith(("- Flag: ", "- No qualifying", "_Learnings", "<!-- No qualifying"))
    )
    review.write_text(text.replace(anchor, anchor + "\n- Flag: Never merge on Fridays (MY-RULE)", 1), encoding="utf-8")

    _uninstall(project)

    assert "Never merge on Fridays (MY-RULE)" in review.read_text(encoding="utf-8")


def test_an_edit_saved_while_uninstall_waits_for_confirmation_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r2 P0 (ownership race): ownership judged at plan time is re-proven on the bytes at delete time."""
    project = _committed_codex_project(tmp_path)
    review = project / "REVIEW.md"

    def user_edits_then_confirms(_prompt: str) -> str:
        review.write_text(
            review.read_text(encoding="utf-8") + "\nMy rule, saved during the prompt.\n", encoding="utf-8"
        )
        return "y"

    monkeypatch.setattr("builtins.input", user_edits_then_confirms)
    _run_uninstall(
        argparse.Namespace(
            target_dir=str(project), dry_run=False, yes=False, user_tier=False, keep_memory=False, ide=None
        )
    )

    assert "My rule, saved during the prompt." in review.read_text(encoding="utf-8")


def test_the_recognizer_accepts_only_bytes_trw_recorded(tmp_path: Path) -> None:
    """Ownership is proven by bytes, never by shape: the install template, or the hash of the last generated write."""
    from trw_mcp.bootstrap._config_templates import _minimal_review_md
    from trw_mcp.state.claude_md._review_md import _REVIEW_TEMPLATE, is_generated_review_md, record_review_md

    trw_dir = tmp_path / ".trw"
    written = _REVIEW_TEMPLATE.replace("{learning_entries}", "- Flag: never swallow errors (L-abc1)")
    assert is_generated_review_md(_minimal_review_md(), trw_dir)
    assert not is_generated_review_md(written, trw_dir), "no record: a generated shape alone proves nothing"

    record_review_md(trw_dir, written)

    assert is_generated_review_md(written, trw_dir)
    assert not is_generated_review_md(written.replace("(L-abc1)", "(L-abc1)\n- Flag: mine (MY-RULE)"), trw_dir)
