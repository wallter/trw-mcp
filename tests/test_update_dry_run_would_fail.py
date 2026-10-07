"""A failing dry run says the update WOULD fail; it never claims a rollback (feedback #112, sub_fZC9TA8D9tWYRadX)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import _update_project as up
from trw_mcp.bootstrap import init_project, update_project

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]

_ROLLBACK = "update-project rolled back managed directories after write failure"


def _fail(*_args: object, **_kwargs: object) -> None:
    raise OSError("simulated write failure")


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    assert not init_project(root, ide="claude-code")["errors"]
    return root


def test_a_failing_dry_run_reports_would_fail_not_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    monkeypatch.setattr(up, "_verify_installation", _fail)

    result = update_project(root, ide="claude-code", dry_run=True)

    assert any("simulated write failure" in e for e in result["errors"])
    assert _ROLLBACK not in result["warnings"]
    assert any("dry run" in w and "would fail" in w for w in result["warnings"])


def test_a_failing_real_run_still_reports_the_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _project(tmp_path)
    monkeypatch.setattr(up, "_verify_installation", _fail)

    result = update_project(root, ide="claude-code")

    assert _ROLLBACK in result["warnings"]
    assert not any("would fail" in w for w in result["warnings"])


def test_a_failed_dry_run_does_not_claim_a_restored_file_was_removed(tmp_path: Path) -> None:
    """Review P2: the early return skipped clearing ``retired``, so 'Removed retired TRW file' sat beside 'nothing changed'."""
    from trw_mcp.bootstrap._update_phases import _forget_rolled_back_changes

    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "back.md").write_text("x", encoding="utf-8")
    result: dict[str, list[str]] = {"retired": [".claude/back.md", ".claude/gone.md"], "warnings": []}

    _forget_rolled_back_changes(tmp_path, result, dry_run=True)

    assert result["retired"] == [".claude/gone.md"]
    assert any("would fail" in w for w in result["warnings"])
    assert not any("put back" in w for w in result["warnings"])


def test_a_dry_run_removal_is_worded_as_proposed(capsys: pytest.CaptureFixture[str]) -> None:
    from trw_mcp.server._update_report import report_removed

    result: dict[str, list[str]] = {"retired": [".claude/old.md"], "would_run": ["pip_install"]}
    report_removed(result, detailed=False, quiet=False)

    out = capsys.readouterr().out
    assert "Would remove retired TRW file: .claude/old.md" in out
    assert "Removed retired TRW file" not in out


def test_a_real_run_removal_stays_past_tense(capsys: pytest.CaptureFixture[str]) -> None:
    from trw_mcp.server._update_report import report_removed

    report_removed({"retired": [".claude/old.md"]}, detailed=False, quiet=False)
    assert "Removed retired TRW file: .claude/old.md" in capsys.readouterr().out


def _rolled_back_result(root: Path, *, dry_run: bool) -> dict[str, list[str]]:
    """What a failed update carries before the rollback is reconciled: every change description it produced."""
    from trw_mcp.bootstrap._update_phases import _forget_rolled_back_changes

    (root / ".claude" / "agents").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "agents" / "trw-tester.md").write_text("x", encoding="utf-8")  # the rollback put it back
    result: dict[str, list[str]] = {
        "updated": [],
        "created": [],
        "preserved": [],
        "errors": ["update-project failed: OSError: simulated"],
        "warnings": ["an unrelated notice"],
        "retired": [".claude/agents/trw-tester.md"],
        "notes": [".claude/settings.json: set 2 legacy hook timeout(s) to TRW's bundled value"],
    }
    if dry_run:
        result["would_run"] = []
        result["dry_run"] = []
    from trw_mcp.bootstrap._retire import Retirement, record_retirement

    record_retirement(result, Retirement([], [".claude/agents/trw-tester.md"], []))
    _forget_rolled_back_changes(root, result, dry_run=dry_run)
    return result


@pytest.mark.parametrize("dry_run", [True, False])
def test_the_final_output_of_a_rolled_back_run_describes_no_change(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dry_run: bool
) -> None:
    """Review round 3 P2: git-removal warnings and hook-timeout notes outlived the rollback that undid them."""
    import argparse

    from trw_mcp import bootstrap
    from trw_mcp.server._subcommands import _run_update_project

    result = _rolled_back_result(tmp_path, dry_run=dry_run)
    monkeypatch.setattr(bootstrap, "update_project", lambda *a, **k: result)
    args = argparse.Namespace(
        target_dir=str(tmp_path), pip_install=False, dry_run=dry_run, ide=None, log_json=False, debug=False,
        verbose=0, quiet=False, reprovision=None,
    )  # fmt: skip
    with pytest.raises(SystemExit):
        _run_update_project(args)

    out = capsys.readouterr().out
    assert "trw-tester" not in out
    assert "hook timeout" not in out
    assert "Removed retired TRW file" not in out
    assert "an unrelated notice" in out


def test_a_successful_dry_run_proposes_its_notes_and_the_agents_md_write(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from trw_mcp.server._update_report import report_kept

    result: dict[str, list[str]] = {
        "updated": ["AGENTS.md"],
        "created": [],
        "preserved": [],
        "notes": [".claude/settings.json: set 1 legacy hook timeout(s) to TRW's bundled value"],
        "would_run": [],
    }
    report_kept(result, tmp_path, detailed=False, quiet=False)

    out = capsys.readouterr().out
    assert "AGENTS.md: would refresh the TRW block" in out
    assert "Would apply: .claude/settings.json: set 1 legacy hook timeout(s)" in out
    assert "refreshed the TRW block" not in out
    assert "Note:" not in out


# ── Final review: recovery warnings survive; a dry run proposes retirement warnings ─


def test_a_recovery_warning_for_the_same_path_survives_the_rollback(tmp_path: Path) -> None:
    """Review P1: filtering by ``<path>: `` prefix deleted the genuine trash-location warning with the removal claim."""
    from trw_mcp.bootstrap._retire import describe_removal
    from trw_mcp.bootstrap._update_phases import _forget_rolled_back_changes

    rel = ".claude/hooks/lib-trw.sh"
    (tmp_path / ".claude" / "hooks").mkdir(parents=True)
    (tmp_path / rel).write_text("x", encoding="utf-8")  # the rollback put it back
    recovery = (
        f"{rel}: a concurrent writer's file was left here; your edit's backup in .trw/trash could not be confirmed"
    )
    result: dict[str, list[str]] = {"retired": [rel], "warnings": []}
    describe_removal(result, rel, f"{rel}: your edited copy was moved to .trw/trash/x and the bundled lib installed")
    result["warnings"].append(recovery)

    _forget_rolled_back_changes(tmp_path, result)

    assert recovery in result["warnings"]
    assert not [w for w in result["warnings"] if "was moved to" in w]
    assert result["retired"] == [] and result["retired_described"] == []


def test_a_description_for_a_file_the_rollback_could_not_restore_is_kept(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._retire import describe_removal
    from trw_mcp.bootstrap._update_phases import _forget_rolled_back_changes

    rel = ".claude/hooks/gone.sh"
    result: dict[str, list[str]] = {"retired": [rel], "warnings": []}
    describe_removal(result, rel, f"{rel}: removed; committed in git")

    _forget_rolled_back_changes(tmp_path, result)

    assert f"{rel}: removed; committed in git" in result["warnings"]


def test_a_dry_run_retirement_warning_is_a_proposal(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import argparse

    from trw_mcp import bootstrap
    from trw_mcp.bootstrap._retire import Retirement, record_retirement
    from trw_mcp.server._subcommands import _run_update_project

    result: dict[str, list[str]] = {
        "updated": [], "created": [], "preserved": [], "errors": [], "warnings": [], "would_run": [], "dry_run": [],
    }  # fmt: skip
    record_retirement(result, Retirement([], [".claude/agents/trw-tester.md"], []))
    monkeypatch.setattr(bootstrap, "update_project", lambda *a, **k: result)
    args = argparse.Namespace(
        target_dir=str(tmp_path), pip_install=False, dry_run=True, ide=None, log_json=False, debug=False,
        verbose=0, quiet=False, reprovision=None,
    )  # fmt: skip
    with pytest.raises(SystemExit):
        _run_update_project(args)

    out = capsys.readouterr().out
    assert "trw-tester.md: would remove" in out
    assert "removed;" not in out and "Removed retired" not in out


def test_a_real_run_retirement_warning_stays_past_tense() -> None:
    from trw_mcp.bootstrap._retire import Retirement, record_retirement

    result: dict[str, list[str]] = {"warnings": []}
    record_retirement(result, Retirement([], ["a.md"], []))
    assert any("a.md: removed;" in w for w in result["warnings"])
