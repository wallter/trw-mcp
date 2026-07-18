"""PRD-CORE-205 FR05 wiring — content-bound build staleness in the delivery gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.tools._delivery_build_gates import build_receipt_content_stale_warning
from trw_mcp.tools._evidence_writers import record_build_receipt


def _run_with_build_receipt(project: Path, files: dict[str, str]) -> Path:
    run = project / ".trw" / "runs" / "task" / "run1"
    meta = run / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    lines = []
    for rel, content in files.items():
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        lines.append(json.dumps({"event": "file_modified", "file": str(p)}))
    (meta / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = record_build_receipt(
        run,
        project,
        tests_passed=True,
        static_checks_clean=True,
        scope_label="full",
        coverage_pct=90.0,
        policy_mode="observe",
    )
    assert out is not None and out.ok
    return run


class TestBuildReceiptContentStaleWarning:
    def test_no_warning_when_content_current(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        run = _run_with_build_receipt(project, {"src/a.py": "code"})
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        assert build_receipt_content_stale_warning(run) is None

    def test_warning_when_bound_byte_changes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        run = _run_with_build_receipt(project, {"src/a.py": "code"})
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        # Edit a bound file AFTER the build receipt was recorded.
        (project / "src" / "a.py").write_text("EDITED AFTER BUILD", encoding="utf-8")
        warning = build_receipt_content_stale_warning(run)
        assert warning is not None
        assert "Content-stale build evidence" in warning

    def test_no_warning_for_unrelated_out_of_scope_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        run = _run_with_build_receipt(project, {"src/a.py": "code"})
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        # Another agent's unrelated file — NOT in this run's bound scope.
        (project / "src" / "unrelated.py").write_text("other work", encoding="utf-8")
        assert build_receipt_content_stale_warning(run) is None

    def test_no_run_is_not_applicable_but_empty_run_lacks_evidence(self, tmp_path: Path) -> None:
        assert build_receipt_content_stale_warning(None) is None
        empty_run = tmp_path / "empty"
        (empty_run / "meta").mkdir(parents=True)
        warning = build_receipt_content_stale_warning(empty_run)
        assert warning is not None and "No valid content-bound BuildReceipt" in warning
