"""Focused anti-Goodhart tests for PRD quality scoring.

These tests lock in the behavior added by PRD-QUAL-059:
- proof-rich implementation plans score above filler-heavy prose
- implementation-readiness guidance outranks density nudges
- density remains a hygiene signal rather than the primary flywheel
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import DimensionScore
from trw_mcp.state.analytics.report import scan_all_runs
from trw_mcp.state.consolidation import consolidate_cycle
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.report import assemble_report
from trw_mcp.state.validation import (
    generate_improvement_suggestions,
    score_implementation_readiness,
    validate_prd_quality_v2,
)
from trw_mcp.state.validation._prd_scoring import (
    _extract_fr_sections,
    _score_assertion_coverage,
    _score_file_path_coverage,
    score_traceability_v2,
)
from trw_mcp.tools._deferred_delivery import _run_deferred_steps
from trw_mcp.tools._review_helpers import _persist_review_artifact

_FRONTMATTER = {"category": "CORE"}

_MINIMAL_PRD_FRONTMATTER = """\
---
id: PRD-QUAL-056
title: Traceability coverage fixture
version: 1.0
status: draft
priority: P2
category: QUAL
confidence:
  implementation_feasibility: 3
  requirement_clarity: 3
  estimate_confidence: 3
traceability:
  implements:
    - US-001
  depends_on:
    - PRD-QUAL-001
  enables:
    - PRD-QUAL-002
---
"""

_PROOF_RICH_CONTENT = """\
## 4. Functional Requirements

### PRD-TEST-001-FR01: Toggle
The system shall update the toggle state and persist the new value.

## 6. Technical Approach

### Primary Control Points
| Surface | Change | Proof |
|---------|--------|-------|
| `src/service.py` | Persist the new toggle state | `test_toggle.py::test_toggle_persists` |

### Behavior Switch Matrix
| Requirement | Old | New | Proof Test |
|-------------|-----|-----|------------|
| FR01 | Toggle updates memory only | Toggle updates memory and storage | `test_toggle.py::test_toggle_persists` |

### Key Files
| File | Changes |
|------|---------|
| `src/service.py` | Persist toggle state |

## 7. Test Strategy

### Unit Tests
- `test_toggle.py::test_toggle_persists`

### Integration Tests
- `test_api.py::test_toggle_endpoint`

### Acceptance Tests
- `platform/src/toggle.test.tsx`

### Regression Tests
- `test_toggle.py::test_toggle_regression`

### Negative / Fallback Tests
- `test_toggle.py::test_toggle_invalid_state`

### Completion Evidence (Definition of Done)
- `pytest tests/test_toggle.py -q`

### Migration / Backward Compatibility
- No migration required.
"""

_FILLER_HEAVY_CONTENT = """\
## 4. Functional Requirements

### PRD-TEST-001-FR01: Toggle
The system shall improve the toggle experience in a comprehensive and
well-structured way that provides meaningful improvements for users.

## 6. Technical Approach

This section describes the overall approach in broad terms. The implementation
should be thoughtful, consistent, and aligned with the broader system goals.
The final solution should be maintainable and reliable.

## 7. Test Strategy

The solution should be tested thoroughly with appropriate unit, integration,
and regression testing as needed for confidence in the outcome.
"""

_TRACEABILITY_ONLY_PATHS = """\
## 4. Functional Requirements

### FR01: Example Requirement
This FR intentionally omits file paths in the prose.

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | US-001 | src/foo.py | test_foo.py::test_bar | Pending |
"""

_ASSERTION_BLOCK_CONTENT = """\
## 4. Functional Requirements

### FR01: Covered by assertions block
Implementation: src/foo.py
Test: test_foo.py::test_bar
```assertions
grep_present: "src/foo.py"
```

### FR02: Only prose mention
Implementation: src/bar.py
Test: test_bar.py::test_baz
This section mentions grep_present as documentation, not as an assertion block.
"""

_ASSERTION_JSON_BULLET_CONTENT = """\
## 4. Functional Requirements

### FR01: Covered by markdown assertions list
Implementation: src/foo.py
Test: test_foo.py::test_bar
**Assertions**:
- {"type": "grep_present", "pattern": "file_path_coverage", "target": "trw-mcp/src/trw_mcp/state/validation/_prd_scoring.py"}
- {"type": "grep_absent", "pattern": "assertion_coverage = 0.0", "target": "trw-mcp/src/trw_mcp/state/validation/_prd_scoring.py"}

### FR02: Only prose mention
Implementation: src/bar.py
Test: test_bar.py::test_baz
This section mentions grep_present as documentation, not as an assertion block.
"""

_ZERO_COVERAGE_CONTENT = (
    _MINIMAL_PRD_FRONTMATTER
    + """\
## 4. Functional Requirements

### FR01: Legacy requirement
The system shall keep legacy wording but cites no file paths or tests.

### FR02: Another legacy requirement
The system shall keep legacy wording but cites no file paths or tests.

## 6. Technical Approach

### Behavior Switch Matrix
| Requirement | Old | New |
|-------------|-----|-----|
| FR01 | Legacy label | Root category |
| FR02 | Legacy prompt | Backward-compatible prompt |

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | US-001 | planned follow-up | manual audit | Planned |
| FR02 | US-002 | legacy mapping | manual audit | Planned |
"""
)

_PARTIAL_COVERAGE_CONTENT = (
    _MINIMAL_PRD_FRONTMATTER
    + """\
## 4. Functional Requirements

### FR01: Fully traced requirement
Implementation: src/audit/prompts.py
Test: tests/test_prompts.py::test_legacy_mapping
```assertions
grep_present: "legacy_category"
```

### FR02: Partially traced requirement
Implementation: src/audit/schema.py
This FR intentionally omits a test reference and assertion block.
"""
)


def _traceability_dimension_details(content: str) -> dict[str, object]:
    result = validate_prd_quality_v2(content)
    return next(dim.details for dim in result.dimensions if dim.name == "traceability")


def _traceability_dimension_score(content: str) -> float:
    result = validate_prd_quality_v2(content)
    return next(dim.score for dim in result.dimensions if dim.name == "traceability")


def test_implementation_readiness_prefers_proof_rich_content() -> None:
    proof_rich = score_implementation_readiness(_FRONTMATTER, _PROOF_RICH_CONTENT)
    filler_heavy = score_implementation_readiness(_FRONTMATTER, _FILLER_HEAVY_CONTENT)

    assert proof_rich.name == "implementation_readiness"
    assert proof_rich.score > filler_heavy.score
    assert proof_rich.details["test_refs"] > filler_heavy.details["test_refs"]
    assert proof_rich.details["implementation_refs"] > filler_heavy.details["implementation_refs"]


def test_density_guidance_is_hygiene_not_primary_driver() -> None:
    dims = [
        DimensionScore(name="content_density", score=11.0, max_score=20.0),
        DimensionScore(name="implementation_readiness", score=8.0, max_score=25.0),
        DimensionScore(name="traceability", score=12.0, max_score=35.0),
    ]

    suggestions = generate_improvement_suggestions(dims)
    suggestion_dimensions = [suggestion.dimension for suggestion in suggestions]

    assert "content_density" not in suggestion_dimensions
    assert suggestion_dimensions[0] == "implementation_readiness"


def test_implementation_readiness_message_mentions_executable_proof() -> None:
    dims = [DimensionScore(name="implementation_readiness", score=5.0, max_score=25.0)]

    suggestions = generate_improvement_suggestions(dims)

    assert len(suggestions) == 1
    assert "control points" in suggestions[0].message
    assert "proof tests" in suggestions[0].message


def test_file_path_coverage_scoring() -> None:
    fr_sections = _extract_fr_sections(_TRACEABILITY_ONLY_PATHS)

    coverage = _score_file_path_coverage(_TRACEABILITY_ONLY_PATHS, fr_sections)
    traceability = score_traceability_v2(_FRONTMATTER, _TRACEABILITY_ONLY_PATHS)

    assert coverage == 1.0
    assert traceability.details["file_path_coverage"] == 1.0


def test_assertion_coverage_scoring() -> None:
    fr_sections = _extract_fr_sections(_ASSERTION_BLOCK_CONTENT)

    coverage = _score_assertion_coverage(_ASSERTION_BLOCK_CONTENT, fr_sections)
    traceability = score_traceability_v2(_FRONTMATTER, _ASSERTION_BLOCK_CONTENT)

    assert coverage == 0.5
    assert traceability.details["assertion_coverage"] == 0.5
    assert "suggestions" not in traceability.details


def test_assertion_coverage_scoring_recognizes_markdown_json_bullets() -> None:
    fr_sections = _extract_fr_sections(_ASSERTION_JSON_BULLET_CONTENT)

    coverage = _score_assertion_coverage(_ASSERTION_JSON_BULLET_CONTENT, fr_sections)
    traceability = score_traceability_v2(_FRONTMATTER, _ASSERTION_JSON_BULLET_CONTENT)

    assert coverage == 0.5
    assert traceability.details["assertion_coverage"] == 0.5
    assert "suggestions" not in traceability.details


def test_validate_prd_quality_v2_scores_repo_prd_assertions_non_zero() -> None:
    content = (
        Path(__file__).resolve().parents[2]
        / "docs/requirements-aare-f/prds/PRD-QUAL-056.md"
    ).read_text(encoding="utf-8")

    result = validate_prd_quality_v2(content)
    traceability = next(dim for dim in result.dimensions if dim.name == "traceability")

    assert traceability.details["assertion_coverage"] > 0.0


def test_validate_prd_quality_v2_zeroes_new_coverage_metrics_without_paths_or_assertions() -> None:
    details = _traceability_dimension_details(_ZERO_COVERAGE_CONTENT)

    assert details["file_path_coverage"] == 0.0
    assert details["assertion_coverage"] == 0.0


def test_validate_prd_quality_v2_scores_partial_traceability_coverage_proportionally() -> None:
    details = _traceability_dimension_details(_PARTIAL_COVERAGE_CONTENT)

    assert details["file_path_coverage"] == 0.75
    assert details["assertion_coverage"] == 0.5
    assert "suggestions" not in details


def test_validate_prd_quality_v2_surfaces_expected_low_coverage_suggestions() -> None:
    details = _traceability_dimension_details(_ZERO_COVERAGE_CONTENT)

    assert details["suggestions"] == [
        "Add implementation and test file paths to FR acceptance criteria for first-pass audit compliance",
        "Add machine-verifiable assertions (grep_present/grep_absent) to FRs for automated audit pre-flight",
    ]


def test_validate_prd_quality_v2_treats_new_coverage_metrics_as_fail_open_bonus() -> None:
    zero_coverage_score = _traceability_dimension_score(_ZERO_COVERAGE_CONTENT)
    partial_coverage_score = _traceability_dimension_score(_PARTIAL_COVERAGE_CONTENT)

    assert zero_coverage_score > 0.0
    assert partial_coverage_score > zero_coverage_score


def _write_learning_entry(
    writer: FileStateWriter,
    entries_dir: Path,
    entry_id: str,
    summary: str,
    tags: list[str],
) -> None:
    writer.write_yaml(
        entries_dir / f"{entry_id}.yaml",
        {
            "id": entry_id,
            "summary": summary,
            "detail": summary,
            "status": "active",
            "tags": tags,
            "impact": 0.7,
            "created": "2026-04-01",
            "updated": "2026-04-01",
        },
    )


def test_delivery_report_rework_metrics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writer = FileStateWriter()
    reader = FileStateReader()
    trw_dir = tmp_path / ".trw"
    run_dir = trw_dir / "runs" / "task-a" / "20260408T120000Z-wave0001"
    meta_dir = run_dir / "meta"
    meta_dir.mkdir(parents=True)
    (trw_dir / "logs").mkdir(parents=True)

    writer.write_yaml(
        meta_dir / "run.yaml",
        {
            "run_id": run_dir.name,
            "task": "task-a",
            "status": "active",
            "phase": "deliver",
            "prd_scope": ["PRD-QUAL-056"],
        },
    )
    _persist_review_artifact(
        run_dir,
        {
            "review_id": "rev-001",
            "timestamp": "2026-04-08T12:00:00Z",
            "verdict": "block",
            "findings": [
                {"category": "impl_gap", "severity": "critical", "description": "Missing wire-up"},
                {"category": "test_gap", "severity": "warning", "description": "Missing regression test"},
            ],
        },
        {
            "review_id": "rev-001",
            "verdict": "block",
        },
    )
    _persist_review_artifact(
        run_dir,
        {
            "review_id": "rev-002",
            "timestamp": "2026-04-08T12:05:00Z",
            "verdict": "pass",
            "findings": [
                {"category": "impl_gap", "severity": "info", "description": "Wire-up verified"},
            ],
        },
        {
            "review_id": "rev-002",
            "verdict": "pass",
        },
    )
    _persist_review_artifact(
        run_dir,
        {
            "review_id": "rev-003",
            "timestamp": "2026-04-08T12:10:00Z",
            "verdict": "pass",
            "findings": [
                {"category": "spec_gap", "severity": "info", "description": "Spec clarified"},
            ],
        },
        {
            "review_id": "rev-003",
            "verdict": "pass",
            "prd_ids": ["PRD-CORE-104"],
        },
    )
    events = reader.read_jsonl(meta_dir / "events.jsonl")
    assert [event["event"] for event in events if event["event"] == "audit_cycle_complete"] == [
        "audit_cycle_complete",
        "audit_cycle_complete",
        "audit_cycle_complete",
    ]
    assert [event["prd_id"] for event in events if event["event"] == "audit_cycle_complete"] == [
        "PRD-QUAL-056",
        "PRD-QUAL-056",
        "PRD-CORE-104",
    ]

    noop = {"status": "skipped"}
    with (
        patch("trw_mcp.tools._deferred_delivery._step_auto_prune", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_consolidation", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_tier_sweep", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._do_index_sync", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_auto_progress", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_publish_learnings", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_outcome_correlation", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_recall_outcome", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_telemetry", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_batch_send", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_trust_increment", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_ceremony_feedback", return_value=noop),
        patch(
            "trw_mcp.tools._deferred_delivery._step_delivery_metrics",
            return_value={"status": "success", "normalized_reward": 0.5},
        ),
    ):
        _run_deferred_steps(trw_dir, run_dir, {})

    run_data = reader.read_yaml(meta_dir / "run.yaml")
    session_metrics = run_data["session_metrics"]
    assert session_metrics["audit_cycles"] == {"PRD-QUAL-056": 2, "PRD-CORE-104": 1}
    assert session_metrics["first_pass_compliance"] == {
        "PRD-QUAL-056": False,
        "PRD-CORE-104": True,
    }
    assert session_metrics["finding_categories"] == {
        "impl_gap": 2,
        "test_gap": 1,
        "spec_gap": 1,
    }
    assert session_metrics["sprint_avg_audit_cycles"] == pytest.approx(1.5)
    assert session_metrics["sprint_first_pass_compliance_rate"] == pytest.approx(0.5)

    report = assemble_report(run_dir, reader, trw_dir)
    assert report.session_metrics["audit_cycles"]["PRD-QUAL-056"] == 2
    assert report.session_metrics["finding_categories"]["impl_gap"] == 2

    monkeypatch.setattr("trw_mcp.state.analytics.report.resolve_project_root", lambda: tmp_path)
    monkeypatch.setattr("trw_mcp.state.analytics.report.resolve_trw_dir", lambda: trw_dir)
    analytics = scan_all_runs()
    assert analytics["aggregate"]["sprint_avg_audit_cycles"] == pytest.approx(1.5)
    assert analytics["aggregate"]["sprint_first_pass_compliance_rate"] == pytest.approx(0.5)


def test_deliver_persists_audit_pattern_promotion_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = make_ceremony_server(monkeypatch, tmp_path)
    writer = FileStateWriter()
    reader = FileStateReader()
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "reflections").mkdir(parents=True)
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "logs").mkdir(parents=True)

    run_dir = tmp_path / "docs" / "task" / "runs" / "20260410T120000Z-deliver-promotions"
    meta_dir = run_dir / "meta"
    meta_dir.mkdir(parents=True)
    writer.write_yaml(
        meta_dir / "run.yaml",
        {
            "run_id": run_dir.name,
            "task": "task-a",
            "status": "active",
            "phase": "deliver",
            "prd_scope": ["PRD-QUAL-056"],
        },
    )
    (meta_dir / "events.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools.ceremony.find_active_run", lambda: run_dir)
    monkeypatch.setattr(
        "trw_mcp.tools.ceremony._do_reflect",
        lambda *_a, **_kw: {"status": "success", "events_analyzed": 0, "learnings_produced": 0},
    )
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)

    noop = {"status": "skipped"}
    promotion_candidates = [
        {
            "category": "impl_gap",
            "normalized_pattern": "integration remediation wiring",
            "pattern_summary": "Integration wiring missing in remediation 2",
            "prd_count": 3,
            "prd_ids": ["PRD-CORE-104", "PRD-CORE-125", "PRD-QUAL-056"],
            "sample_summaries": [
                "Integration wiring missing in remediation 2",
                "Integration wiring missing in remediation 3",
                "Integration wiring missing in remediation 1",
            ],
            "synthesized_summary": "Recurring impl gap pattern: Integration wiring missing in remediation 2.",
            "prevention_strategy": "Verify the production call path and integration wiring before closing remediation.",
            "nudge_line": "Recurring impl gap: Integration wiring missing in remediation 2",
        }
    ]

    import trw_mcp.tools._deferred_state as _ds

    _ds._deferred_thread = None
    with (
        patch("trw_mcp.tools._deferred_delivery._step_auto_prune", return_value=noop),
        patch(
            "trw_mcp.tools._deferred_delivery._step_consolidation",
            return_value={
                "status": "no_clusters",
                "clusters_found": 0,
                "consolidated_count": 0,
                "audit_pattern_promotions": promotion_candidates,
                "audit_pattern_promotion_threshold": 3,
            },
        ),
        patch("trw_mcp.tools._deferred_delivery._step_tier_sweep", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_auto_progress", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_publish_learnings", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_outcome_correlation", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_recall_outcome", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_telemetry", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_batch_send", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_trust_increment", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_ceremony_feedback", return_value=noop),
        patch(
            "trw_mcp.tools._deferred_delivery._step_delivery_metrics",
            return_value={"status": "success", "normalized_reward": 0.5},
        ),
    ):
        result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)
        assert result["deferred"] == "launched"
        assert _ds._deferred_thread is not None
        _ds._deferred_thread.join(timeout=5)
        assert not _ds._deferred_thread.is_alive()
        _ds._deferred_thread = None

    run_data = reader.read_yaml(meta_dir / "run.yaml")
    assert run_data["deferred_results"]["consolidation"]["audit_pattern_promotions"] == promotion_candidates
    assert run_data["promotion_candidates"]["audit_pattern_promotions"] == promotion_candidates
    assert run_data["promotion_candidates"]["source"] == "consolidation"
    assert run_data["promotion_candidates"]["promotion_path"] == "metadata_only"
    assert run_data["promotion_candidates"]["delivery_surface"] == "run.yaml"
    assert run_data["promotion_candidates"]["claude_md_sync_integration"] == "not_applicable_prd_core_093"
    assert run_data["promotion_candidates"]["meta_tune_integration"] == "tool_unavailable"


def test_audit_pattern_promotion(tmp_path: Path) -> None:
    writer = FileStateWriter()
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)

    _write_learning_entry(
        writer,
        entries_dir,
        "L-001",
        "Integration wiring missing in remediation 1",
        ["audit-finding", "impl_gap", "PRD-QUAL-056"],
    )
    _write_learning_entry(
        writer,
        entries_dir,
        "L-002",
        "Integration wiring missing in remediation 2",
        ["audit-finding", "impl_gap", "PRD-CORE-104"],
    )
    _write_learning_entry(
        writer,
        entries_dir,
        "L-003",
        "Integration wiring missing in remediation 3",
        ["audit-finding", "impl_gap", "PRD-CORE-125"],
    )

    cfg = TRWConfig(audit_pattern_promotion_threshold=3)
    with patch("trw_mcp.state.consolidation._cycle.find_clusters", return_value=[]):
        result = consolidate_cycle(trw_dir, config=cfg)

    assert result["status"] == "no_clusters"
    assert result["audit_pattern_promotion_threshold"] == 3
    assert result["audit_pattern_promotions"] == [
        {
            "category": "impl_gap",
            "normalized_pattern": "integration remediation wiring",
            "pattern_summary": "Integration wiring missing in remediation 2",
            "prd_count": 3,
            "prd_ids": ["PRD-CORE-104", "PRD-CORE-125", "PRD-QUAL-056"],
            "sample_summaries": [
                "Integration wiring missing in remediation 2",
                "Integration wiring missing in remediation 3",
                "Integration wiring missing in remediation 1",
            ],
            "synthesized_summary": (
                "Recurring impl gap pattern: Integration wiring missing in remediation 2. "
                "Observed across 3 PRDs. Prevention: Verify the production call path and "
                "integration wiring before closing remediation."
            ),
            "prevention_strategy": "Verify the production call path and integration wiring before closing remediation.",
            "nudge_line": "Recurring impl gap: Integration wiring missing in remediation 2",
        }
    ]


def test_audit_pattern_promotion_respects_config_threshold(tmp_path: Path) -> None:
    writer = FileStateWriter()
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)

    for idx, prd_id in enumerate(("PRD-QUAL-056", "PRD-CORE-104", "PRD-CORE-125"), start=1):
        _write_learning_entry(
            writer,
            entries_dir,
            f"L-10{idx}",
            f"Recurring audit finding {idx}",
            ["audit-finding", "test_gap", prd_id],
        )

    cfg = TRWConfig(audit_pattern_promotion_threshold=4)
    with patch("trw_mcp.state.consolidation._cycle.find_clusters", return_value=[]):
        result = consolidate_cycle(trw_dir, config=cfg)

    assert result["status"] == "no_clusters"
    assert result["audit_pattern_promotion_threshold"] == 4
    assert result["audit_pattern_promotions"] == []


def test_audit_pattern_promotion_supports_integration_and_traceability_categories(tmp_path: Path) -> None:
    writer = FileStateWriter()
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)

    for idx, prd_id in enumerate(("PRD-QUAL-056", "PRD-CORE-104", "PRD-CORE-125"), start=1):
        _write_learning_entry(
            writer,
            entries_dir,
            f"L-20{idx}",
            f"Service callback wiring missing from runtime path {idx}",
            ["audit-finding", "integration_gap", prd_id],
        )
        _write_learning_entry(
            writer,
            entries_dir,
            f"L-30{idx}",
            f"Traceability matrix stale after remediation update {idx}",
            ["audit-finding", "traceability_gap", prd_id],
        )

    cfg = TRWConfig(audit_pattern_promotion_threshold=3)
    with patch("trw_mcp.state.consolidation._cycle.find_clusters", return_value=[]):
        result = consolidate_cycle(trw_dir, config=cfg)

    promotions = {(entry["category"], entry["normalized_pattern"]): entry for entry in result["audit_pattern_promotions"]}
    integration = promotions[("integration_gap", "callback path runtime service wiring")]
    traceability = promotions[("traceability_gap", "after matrix remediation stale traceability update")]
    assert integration["prevention_strategy"] == (
        "Exercise end-to-end integration points, not just isolated units, before delivery."
    )
    assert "Observed across 3 PRDs" in integration["synthesized_summary"]
    assert traceability["prevention_strategy"] == (
        "Update traceability artifacts alongside code changes before delivery sign-off."
    )
    assert traceability["pattern_summary"] == "Traceability matrix stale after remediation update 2"


def test_audit_pattern_promotion_does_not_promote_same_category_count_without_shared_pattern(tmp_path: Path) -> None:
    writer = FileStateWriter()
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)

    summaries = [
        ("PRD-QUAL-056", "Missing null guard in parser"),
        ("PRD-CORE-104", "Retry budget never resets"),
        ("PRD-CORE-125", "Feature flag default is inverted"),
    ]
    for idx, (prd_id, summary) in enumerate(summaries, start=1):
        _write_learning_entry(
            writer,
            entries_dir,
            f"L-40{idx}",
            summary,
            ["audit-finding", "impl_gap", prd_id],
        )

    cfg = TRWConfig(audit_pattern_promotion_threshold=3)
    with patch("trw_mcp.state.consolidation._cycle.find_clusters", return_value=[]):
        result = consolidate_cycle(trw_dir, config=cfg)

    assert result["audit_pattern_promotions"] == []



def test_exec_plan_includes_verification_commands() -> None:
    skill_path = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "trw_mcp"
        / "data"
        / "skills"
        / "trw-exec-plan"
        / "SKILL.md"
    )
    content = skill_path.read_text(encoding="utf-8")

    assert "Pre-Implementation Checklist (PRD-QUAL-056-FR03)" in content
    assert "Pre-Implementation Checklist" in content
    assert "FR{N} PASS" in content


