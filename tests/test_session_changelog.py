"""Tests for PRD-LOCAL-049 — session changelog + package-changelog advisory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from trw_mcp.server._subcommands import _run_session_changelog
from trw_mcp.state._session_changelog import (
    build_session_changelog,
    detect_package_changelog_advisory,
    write_session_changelog,
)


def _make_run(tmp_path: Path, *, with_review: bool = True) -> tuple[Path, Path]:
    """Create a minimal TRW run dir + .trw dir. Returns (run_path, trw_dir)."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    run_path = tmp_path / ".trw" / "runs" / "task" / "20260609T000000Z-run"
    meta = run_path / "meta"
    meta.mkdir(parents=True)
    (meta / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"event": "trw_init", "ts": "2026-06-09T00:00:00Z"}),
                json.dumps({"event": "trw_learn", "ts": "2026-06-09T00:01:00Z"}),
                json.dumps({"event": "followup", "detail": "wire CLI test", "ts": "2026-06-09T00:02:00Z"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if with_review:
        (meta / "review.yaml").write_text(
            "verdict: pass\nfindings: []\n",
            encoding="utf-8",
        )
    (trw_dir / "context" / "build-status.yaml").write_text(
        "scope: full\ntests_passed: true\ncoverage_pct: 91.0\nfailure_count: 0\n",
        encoding="utf-8",
    )
    return run_path, trw_dir


def test_session_changelog_written_on_deliver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR01: write_session_changelog persists the markdown artifact + path."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    run_path, trw_dir = _make_run(tmp_path)
    report_path, result = write_session_changelog(run_path, trw_dir)
    assert report_path == run_path / "reports" / "session-changelog.md"
    assert report_path.is_file()
    body = report_path.read_text(encoding="utf-8")
    for section in (
        "## Summary",
        "## Commits Made",
        "## Files Changed",
        "## Validation Evidence",
        "## Review Evidence",
        "## Learnings Recorded",
        "## Residual Risks",
        "## Follow-ups",
    ):
        assert section in body
    assert result.learnings_recorded == 1
    # Follow-up event surfaced from durable events.
    assert "wire CLI test" in body


def test_session_changelog_handles_no_commits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR02: a session with no derivable commits states so explicitly."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    run_path, trw_dir = _make_run(tmp_path)
    result = build_session_changelog(run_path, trw_dir)
    assert result.has_commits is False
    assert "No commits were recorded for this session." in result.markdown


def test_session_changelog_degrades_when_review_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR02: missing review.yaml degrades to an explicit unknown section."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    run_path, trw_dir = _make_run(tmp_path, with_review=False)
    result = build_session_changelog(run_path, trw_dir)
    assert result.review_present is False
    assert "Review: unknown" in result.markdown


def test_prd_local_049_fr02_session_changelog_handles_missing_optional_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR02: missing build-status AND review both degrade without raising."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    run_path, trw_dir = _make_run(tmp_path, with_review=False)
    (trw_dir / "context" / "build-status.yaml").unlink()
    result = build_session_changelog(run_path, trw_dir)
    assert result.build_present is False
    assert result.review_present is False
    assert "Build status: unknown" in result.markdown
    assert "Review: unknown" in result.markdown


def test_package_changelog_advisory_ignores_projects_without_changelog(tmp_path: Path) -> None:
    """FR03: a project with no CHANGELOG.md produces coverage with no failure."""
    git_root = tmp_path
    by_package = {"pkg-a": ["pkg-a/src/mod.py"], ".": ["README.md"]}
    coverage = detect_package_changelog_advisory(by_package, git_root)
    assert {c.package_root for c in coverage} == {"pkg-a", "."}
    # No CHANGELOG.md exists anywhere — all entries have changelog_path=None,
    # changelog_updated=False, and (critically) the call does not raise.
    assert all(c.changelog_path is None for c in coverage)
    assert all(c.changelog_updated is False for c in coverage)


def test_package_changelog_advisory_detects_nearest_changelog_when_present(tmp_path: Path) -> None:
    """FR03: nearest CHANGELOG.md is found; updated-coverage is detected."""
    git_root = tmp_path
    (git_root / "pkg-a").mkdir()
    (git_root / "pkg-a" / "CHANGELOG.md").write_text("# changelog\n", encoding="utf-8")
    (git_root / "CHANGELOG.md").write_text("# root\n", encoding="utf-8")
    by_package = {
        "pkg-a": ["pkg-a/src/mod.py", "pkg-a/CHANGELOG.md"],  # changelog updated
        "pkg-b": ["pkg-b/src/other.py"],  # falls back to root CHANGELOG, NOT updated
    }
    coverage = {c.package_root: c for c in detect_package_changelog_advisory(by_package, git_root)}
    assert coverage["pkg-a"].changelog_path == "pkg-a/CHANGELOG.md"
    assert coverage["pkg-a"].changelog_updated is True
    assert coverage["pkg-b"].changelog_path == "CHANGELOG.md"  # nearest is repo root
    assert coverage["pkg-b"].changelog_updated is False


def test_prd_local_049_fr03_package_changelog_advisory_is_non_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR03: advisory is opt-in and never raises / never blocks the build."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    run_path, trw_dir = _make_run(tmp_path)
    # Default (advisory disabled): no advisory section is rendered.
    disabled = build_session_changelog(run_path, trw_dir, changelog_advisory_enabled=False)
    assert "## Package Changelog Advisory" not in disabled.markdown
    assert disabled.package_changelog_advisory == []
    # Enabled: section rendered, still no exception.
    enabled = build_session_changelog(run_path, trw_dir, changelog_advisory_enabled=True)
    assert "## Package Changelog Advisory" in enabled.markdown


def test_session_changelog_build_never_raises_on_bad_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR02 fail-open: a nonexistent run dir still returns a minimal result."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    bogus = tmp_path / "does-not-exist"
    result = build_session_changelog(bogus, tmp_path / ".trw")
    assert isinstance(result.markdown, str)
    assert "# Session Changelog" in result.markdown


def test_prd_local_049_fr04_cli_prints_changelog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR04: the session-changelog subcommand prints the report (read-only)."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    run_path, _ = _make_run(tmp_path)
    args = argparse.Namespace(run_path=str(run_path), write=False, advisory=False)
    with pytest.raises(SystemExit) as exc:
        _run_session_changelog(args)
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "# Session Changelog" in out
    # Read-only: no artifact written when --write is absent.
    assert not (run_path / "reports" / "session-changelog.md").exists()


def test_prd_local_049_fr04_cli_write_persists_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR04: --write persists the artifact and prints its path."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    run_path, _ = _make_run(tmp_path)
    args = argparse.Namespace(run_path=str(run_path), write=True, advisory=False)
    with pytest.raises(SystemExit) as exc:
        _run_session_changelog(args)
    assert exc.value.code == 0
    out = capsys.readouterr().out.strip()
    report_path = run_path / "reports" / "session-changelog.md"
    assert report_path.is_file()
    # The handler prints the path as its final line (structlog may emit log
    # lines to stdout in the test env, so assert on the last non-empty line).
    last_line = [line for line in out.splitlines() if line.strip()][-1]
    assert last_line == str(report_path)


def test_prd_local_049_fr04_cli_rejects_non_run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR04: a path without meta/ exits non-zero (read-only safety)."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    args = argparse.Namespace(run_path=str(tmp_path), write=False, advisory=False)
    with pytest.raises(SystemExit) as exc:
        _run_session_changelog(args)
    assert exc.value.code == 1
