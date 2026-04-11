"""Tests for PRD-QUAL-022: trw_review ceremony tool and trw_deliver review soft gate.

Covers:
- trw_review: artifact creation, verdict logic (pass/warn/block), event logging
- trw_deliver: soft gate warning for critical review, advisory when no review.yaml
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from trw_mcp.models.run import ReviewFinding

# --- Fixtures ---


@pytest.fixture()
def trw_project(tmp_path: Path) -> Path:
    """Create a minimal .trw/ project structure for ceremony tests."""
    trw_dir = tmp_path / ".trw"
    learnings_dir = trw_dir / "learnings" / "entries"
    learnings_dir.mkdir(parents=True)
    (trw_dir / "reflections").mkdir()
    (trw_dir / "context").mkdir()
    (trw_dir / "learnings" / "index.yaml").write_text(
        "total_entries: 0\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory structure."""
    d = tmp_path / "docs" / "task" / "runs" / "20260226T120000Z-review-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: review-test\nstatus: active\nphase: review\ntask_name: review-task\n",
        encoding="utf-8",
    )
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server


def _make_deliver_with_stubs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a ceremony server and patch heavy sub-steps for deliver tests."""
    tools = _make_ceremony_server(monkeypatch, tmp_path)
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "reflections").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools.ceremony.find_active_run", lambda: run_dir)
    monkeypatch.setattr(
        "trw_mcp.tools.ceremony._do_reflect",
        lambda *_a, **_kw: {"status": "success", "events_analyzed": 0, "learnings_produced": 0},
    )
    monkeypatch.setattr(
        "trw_mcp.tools.ceremony._do_instruction_sync",
        lambda *_a, **_kw: {"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
    )
    monkeypatch.setattr(
        "trw_mcp.tools._deferred_delivery._do_index_sync",
        lambda *_a, **_kw: {"status": "success", "index": {}, "roadmap": {}},
    )
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)
    return tools


# --- ReviewFinding model ---


class TestReviewFindingModel:
    """ReviewFinding Pydantic model tests."""

    def test_creates_with_required_fields(self) -> None:
        f = ReviewFinding(
            category="correctness",
            severity="critical",
            description="Missing null check",
        )
        assert f.category == "correctness"
        assert f.severity == "critical"
        assert f.description == "Missing null check"
        assert f.file_path == ""
        assert f.suggestion == ""

    def test_creates_with_all_fields(self) -> None:
        f = ReviewFinding(
            category="security",
            severity="warning",
            description="SQL injection risk",
            file_path="src/app.py",
            suggestion="Use parameterized queries",
        )
        assert f.file_path == "src/app.py"
        assert f.suggestion == "Use parameterized queries"

    def test_is_frozen(self) -> None:
        f = ReviewFinding(
            category="testing",
            severity="info",
            description="Low coverage",
        )
        with pytest.raises(ValidationError):
            f.category = "other"  # type: ignore[misc]


# --- trw_review tool ---


class TestReviewCreatesArtifact:
    """trw_review creates review.yaml artifact in run directory."""

    def test_review_creates_artifact(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Call trw_review with findings, verify review.yaml is created."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
        ):
            result = tools["trw_review"].fn(
                findings=[
                    {"category": "correctness", "severity": "warning", "description": "Test issue"},
                ],
            )

        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()
        assert result["review_yaml"] == str(review_path)
        assert result["total_findings"] == 1

        # Read back the artifact and verify structure
        from trw_mcp.state.persistence import FileStateReader

        data = FileStateReader().read_yaml(review_path)
        assert "review_id" in data
        assert data["verdict"] == "warn"
        assert data["critical_count"] == 0
        assert data["warning_count"] == 1


class TestReviewVerdictPass:
    """trw_review produces 'pass' verdict when no findings."""

    def test_review_verdict_pass(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn()

        assert result["verdict"] == "pass"
        assert result["critical_count"] == 0
        assert result["warning_count"] == 0
        assert result["total_findings"] == 0

    def test_review_verdict_pass_with_info_only(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Info-only findings should produce 'pass' verdict."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn(
                findings=[
                    {"category": "maintainability", "severity": "info", "description": "Consider refactor"},
                ],
            )

        assert result["verdict"] == "pass"
        assert result["info_count"] == 1


class TestReviewVerdictWarn:
    """trw_review produces 'warn' verdict for warning findings."""

    def test_review_verdict_warn(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn(
                findings=[
                    {"category": "performance", "severity": "warning", "description": "O(n^2) loop"},
                    {"category": "maintainability", "severity": "info", "description": "Magic number"},
                ],
            )

        assert result["verdict"] == "warn"
        assert result["warning_count"] == 1
        assert result["info_count"] == 1


class TestReviewVerdictBlock:
    """trw_review produces 'block' verdict for critical findings."""

    def test_review_verdict_block(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn(
                findings=[
                    {"category": "security", "severity": "critical", "description": "SQL injection"},
                    {"category": "correctness", "severity": "warning", "description": "Off-by-one"},
                ],
            )

        assert result["verdict"] == "block"
        assert result["critical_count"] == 1
        assert result["warning_count"] == 1


class TestReviewLogsEvent:
    """trw_review logs review_complete event to events.jsonl."""

    def test_review_logs_event(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn(
                findings=[
                    {"category": "testing", "severity": "info", "description": "Missing edge case test"},
                ],
            )

        events_path = run_dir / "meta" / "events.jsonl"
        lines = [line for line in events_path.read_text(encoding="utf-8").strip().split("\n") if line]
        assert len(lines) >= 1
        event = json.loads(lines[-1])
        assert event["event"] == "review_complete"
        assert event["verdict"] == "pass"
        assert event["review_id"] == result["review_id"]


class TestPreflightLogging:
    """trw_preflight_log records explicit checklist and self-review events."""

    def test_preflight_log_persists_checklist_and_self_review_events(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_preflight_log"].fn(
                prd_id="PRD-QUAL-056",
                checklist_complete=True,
                self_review={
                    "passed": 6,
                    "failed": 1,
                    "skipped": 0,
                    "wiring_issues": ["src/new_module.py"],
                    "nfr_issues": ["missing structured logging"],
                    "test_issues": ["FR05 traceability mismatch"],
                },
            )

        assert result["status"] == "logged"
        assert result["logged_events"] == [
            "pre_implementation_checklist_complete",
            "pre_audit_self_review",
        ]

        events_path = run_dir / "meta" / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]
        assert events[-2]["event"] == "pre_implementation_checklist_complete"
        assert events[-2]["prd_id"] == "PRD-QUAL-056"
        assert events[-1]["event"] == "pre_audit_self_review"
        assert events[-1]["passed"] == 6
        assert events[-1]["wiring_issues"] == ["src/new_module.py"]

    def test_preflight_log_fail_open_on_malformed_self_review(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Malformed self-review payloads normalize instead of crashing."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_preflight_log"].fn(
                prd_id="PRD-QUAL-056",
                self_review={
                    "passed": "not-a-number",
                    "failed": None,
                    "skipped": "2.5",
                    "wiring_issues": "src/runtime.py",
                    "nfr_issues": ("missing structured logging",),
                    "test_issues": 42,
                },
            )

        assert result["status"] == "logged"
        events = [
            json.loads(line)
            for line in (run_dir / "meta" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        assert events[-1]["event"] == "pre_audit_self_review"
        assert events[-1]["passed"] == 0
        assert events[-1]["failed"] == 0
        assert events[-1]["skipped"] == 0
        assert events[-1]["wiring_issues"] == ["src/runtime.py"]
        assert events[-1]["nfr_issues"] == ["missing structured logging"]
        assert events[-1]["test_issues"] == []

    def test_review_artifact_includes_latest_preflight_checks(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            tools["trw_preflight_log"].fn(
                prd_id="PRD-QUAL-056",
                checklist_complete=True,
                self_review={
                    "passed": 4,
                    "failed": 0,
                    "skipped": 1,
                    "wiring_issues": [],
                    "nfr_issues": [],
                    "test_issues": [],
                },
            )
            tools["trw_review"].fn(
                findings=[
                    {"category": "testing", "severity": "info", "description": "Ready for audit"},
                ],
                prd_ids=["PRD-QUAL-056"],
            )

        review_path = run_dir / "meta" / "review.yaml"
        from trw_mcp.state.persistence import FileStateReader

        review_data = FileStateReader().read_yaml(review_path)
        preflight = review_data["preflight_checks"]["PRD-QUAL-056"]
        assert preflight["pre_implementation_checklist_complete"]["completed"] is True
        assert preflight["pre_audit_self_review"]["passed"] == 4
        assert preflight["pre_audit_self_review"]["skipped"] == 1


class TestReviewAutoDetectRun:
    """trw_review auto-detects run directory when run_path is None."""

    def test_review_auto_detect_run(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.review.find_active_run", return_value=run_dir):
            result = tools["trw_review"].fn()

        assert result["run_path"] == str(run_dir)
        assert result["verdict"] == "pass"

    def test_review_explicit_run_path(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        result = tools["trw_review"].fn(run_path=str(run_dir))

        assert result["run_path"] == str(run_dir)
        assert (run_dir / "meta" / "review.yaml").exists()

    def test_review_no_run_available(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When no run is available, review still returns a result but no artifact."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)

        with patch("trw_mcp.tools.review.find_active_run", return_value=None):
            result = tools["trw_review"].fn(
                findings=[
                    {"category": "correctness", "severity": "critical", "description": "Bug"},
                ],
            )

        assert result["run_path"] is None
        assert result["verdict"] == "block"
        assert result["review_yaml"] == ""


# --- trw_deliver review soft gate ---


@pytest.mark.integration
class TestDeliverReviewSoftGate:
    """trw_deliver soft gate checks for review.yaml before delivery."""

    def test_deliver_review_soft_gate_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Deliver with critical review verdict produces review_warning."""
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260226T120000Z-gate-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: gate-test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        # Write a review.yaml with block verdict
        from trw_mcp.state.persistence import FileStateWriter

        writer = FileStateWriter()
        writer.write_yaml(
            run_dir / "meta" / "review.yaml",
            {
                "review_id": "review-test123",
                "verdict": "block",
                "critical_count": 2,
                "warning_count": 0,
                "findings": [],
            },
        )

        tools = _make_deliver_with_stubs(monkeypatch, tmp_path, run_dir=run_dir)

        result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        assert "review_warning" in result
        assert "critical findings" in str(result["review_warning"])

    def test_deliver_review_advisory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Deliver without review.yaml produces review_advisory."""
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260226T120000Z-advisory-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: advisory-test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")
        # No review.yaml created

        tools = _make_deliver_with_stubs(monkeypatch, tmp_path, run_dir=run_dir)

        result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        assert "review_advisory" in result
        assert "trw_review" in str(result["review_advisory"])

    def test_deliver_no_review_gate_when_no_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Deliver without active run skips review gate entirely."""
        tools = _make_deliver_with_stubs(monkeypatch, tmp_path, run_dir=None)

        result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        # Neither warning nor advisory should be present
        assert "review_warning" not in result
        assert "review_advisory" not in result

    def test_deliver_review_gate_pass_no_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Deliver with passing review produces no warning or advisory."""
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260226T120000Z-pass-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: pass-test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        from trw_mcp.state.persistence import FileStateWriter

        writer = FileStateWriter()
        writer.write_yaml(
            run_dir / "meta" / "review.yaml",
            {
                "review_id": "review-pass",
                "verdict": "pass",
                "critical_count": 0,
                "warning_count": 0,
                "findings": [],
            },
        )

        tools = _make_deliver_with_stubs(monkeypatch, tmp_path, run_dir=run_dir)

        result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        assert "review_warning" not in result
        assert "review_advisory" not in result
