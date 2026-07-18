"""PRD-CORE-205 FPI-1 — real .trw run-directory receipt integration.

Drives the observe-mode BuildReceipt dual-writer against a real run directory
(real journal, real files, real atomic persistence, real validation) and exercises
the live ``trw_build_check`` tool path so the wire is proven, not just unit-mocked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import ReceiptState
from trw_mcp.tools._evidence_writers import _validation_plan, latest_build_receipt, record_build_receipt


def _make_run_with_journal(project: Path, changed_files: dict[str, str]) -> Path:
    run = project / ".trw" / "runs" / "task" / "run1"
    meta = run / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    lines = []
    for rel, content in changed_files.items():
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        lines.append(json.dumps({"ts": "2026-07-10T00:00:00Z", "event": "file_modified", "file": str(p)}))
    (meta / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run


class TestRealRunDirectoryReceipt:
    def test_dual_write_persists_content_bound_receipt(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        run = _make_run_with_journal(project, {"src/a.py": "real code", "src/b.py": "more"})

        outcome = record_build_receipt(
            run,
            project,
            tests_passed=True,
            static_checks_clean=True,
            scope_label="full",
            coverage_pct=91.0,
            policy_mode="observe",
        )
        assert outcome is not None and outcome.ok

        receipt = latest_build_receipt(run)
        assert receipt is not None
        # Content binding actually covers the journaled run-owned files.
        bound = {e.path for e in receipt.content_binding.entries}
        assert bound == {"src/a.py", "src/b.py"}
        assert receipt.legacy_tests_passed is True

        # A subsequent byte change to a bound file invalidates the receipt.
        from trw_mcp.tools._evidence_binding import content_binding_is_current

        assert content_binding_is_current(receipt.content_binding, project).state is ReceiptState.VALID
        (project / "src" / "a.py").write_text("MUTATED", encoding="utf-8")
        assert content_binding_is_current(receipt.content_binding, project).state is ReceiptState.STALE_CONTENT

    def test_scope_unverifiable_run_writes_no_receipt(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        (project / ".trw" / "runs" / "task" / "run1" / "meta").mkdir(parents=True)
        run = project / ".trw" / "runs" / "task" / "run1"
        # No file_modified journal -> scope_unverifiable -> no positive receipt.
        outcome = record_build_receipt(
            run,
            project,
            tests_passed=True,
            static_checks_clean=True,
            scope_label="full",
            coverage_pct=None,
            policy_mode="observe",
        )
        assert outcome is None
        assert latest_build_receipt(run) is None


class TestLiveBuildCheckToolPath:
    def test_trw_build_check_dual_writes_receipt(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests.conftest import extract_tool_fn, make_test_server

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".trw" / "context").mkdir(parents=True)
        run = _make_run_with_journal(project, {"src/a.py": "code"})

        monkeypatch.setattr("trw_mcp.tools.build._registration.resolve_trw_dir", lambda: project / ".trw")
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)

        build_check = extract_tool_fn(make_test_server("build"), "trw_build_check")
        result = build_check(
            tests_passed=True,
            static_checks_clean=True,
            test_count=5,
            scope="full",
            run_path=str(run),
            command_results=[
                {"command_id": "tests", "label": "pytest", "command_class": "test", "exit_code": 0},
                {
                    "command_id": "static_checks",
                    "label": "ruff+mypy",
                    "command_class": "static",
                    "exit_code": 0,
                },
            ],
        )
        assert result["tests_passed"] is True
        # The live tool call actually persisted a per-run receipt.
        receipt = latest_build_receipt(run)
        assert receipt is not None
        assert {e.path for e in receipt.content_binding.entries} == {"src/a.py"}


def test_validation_plan_normalizes_integral_coverage_before_digest() -> None:
    """CORE-205: JSON integer thresholds survive strict digest validation."""
    plan = _validation_plan(
        scope_id="scope",
        scope_digest="digest",
        governing_prd_ids=(),
        governing_content_digest="governing",
        coverage_threshold=90,
        policy_mode="enforce",
    )
    assert plan.coverage_threshold == 90.0
    assert plan.plan_digest == plan.expected_digest()
