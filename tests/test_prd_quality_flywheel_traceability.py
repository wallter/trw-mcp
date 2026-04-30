"""Traceability coverage tests for PRD quality scoring."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.validation import validate_prd_quality_v2
from trw_mcp.state.validation._prd_scoring import (
    _extract_fr_sections,
    _score_assertion_coverage,
    _score_file_path_coverage,
    score_traceability_v2,
)

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
    content = (Path(__file__).resolve().parents[2] / "docs/requirements-aare-f/prds/PRD-QUAL-056.md").read_text(
        encoding="utf-8"
    )

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
