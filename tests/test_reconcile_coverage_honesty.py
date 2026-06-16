"""Honest-labeling tests for reconcile FR-coverage (F6 truthfulness fix).

Drives the REAL reconcile path. Asserts the reconcile verdict self-documents
that it does identifier-presence-in-diff matching (NOT behavioral
verification), surfaces FRs that have no extractable identifier instead of
silently counting them as covered, and qualifies the no-governing-PRD 'clean'
verdict so it can't be read as "FRs verified covered".

Defect (pre-fix): ``handle_reconcile_mode`` marked an FR clean whenever its
extracted identifier string appeared anywhere in the concatenated added-diff
text, and FRs with NO extractable identifier passed silently. The verdict
could then be cited as evidence of spec coverage it never measured.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.tools._review_manual import (
    RECONCILE_COVERAGE_METHOD,
    _extract_fr_not_checkable,
    handle_reconcile_mode,
)

from ._reconciliation_support import (
    SAMPLE_DIFF_WITH_MATCHES,
    make_config,
    run_dir,  # noqa: F401
)

# An FR whose body has NO backtick / --flag / PascalCase identifier. Under
# presence-matching it is fundamentally not checkable -- it must NOT pass
# silently as "clean".
PRD_WITH_UNCHECKABLE_FR = """\
---
id: PRD-TEST-009
status: draft
---
# PRD-TEST-009: Mixed FRs

## 3. Functional Requirements

### FR01: Implement `UserValidator` class

The `UserValidator` class MUST validate input.

### FR02: System must log every access attempt to the audit trail.

When a user reads a record, the system records who, what, and when so that
operators can later reconstruct the access history. No identifier appears in
this requirement text at all.
"""


@pytest.mark.unit
def test_extract_fr_not_checkable_surfaces_identifierless_fr() -> None:
    """An FR with no extractable identifier is surfaced, not dropped."""
    not_checkable = _extract_fr_not_checkable(PRD_WITH_UNCHECKABLE_FR, "PRD-TEST-009")
    frs = {entry["fr"] for entry in not_checkable}
    assert "FR02" in frs  # the identifier-less FR is surfaced
    assert "FR01" not in frs  # FR01 has `UserValidator` -> checkable
    fr02 = next(e for e in not_checkable if e["fr"] == "FR02")
    assert fr02["prd_id"] == "PRD-TEST-009"
    assert fr02["reason"] == "no_extractable_identifier"
    assert fr02["fr_text"]  # carries the FR text for the operator


@pytest.mark.unit
def test_extract_fr_not_checkable_empty_when_all_checkable() -> None:
    """When every FR has an identifier, nothing is flagged not-checkable."""
    prd = "## 3. Functional Requirements\n\n### FR01: Add `Widget` class\n\nThe `Widget` does work.\n"
    assert _extract_fr_not_checkable(prd, "PRD-X") == []


def test_reconcile_surfaces_not_checkable_fr_not_silently_clean(
    run_dir: Path,
    tmp_path: Path,
) -> None:
    """REAL reconcile path: identifier-less FR appears in fr_not_checkable.

    Core honesty assertion: the FR with no extractable identifier is NOT
    silently swallowed into a clean verdict -- it is reported.
    """
    config = make_config(prds_relative_path="prds")
    project_root = tmp_path / "project"
    prds_dir = project_root / "prds"
    prds_dir.mkdir(parents=True)
    (prds_dir / "PRD-TEST-009.md").write_text(PRD_WITH_UNCHECKABLE_FR, encoding="utf-8")

    # Diff covers the one checkable identifier (`UserValidator`) so there are no
    # mismatches -- pre-fix this would read as a flat 'clean' with FR02 silent.
    diff = "diff --git a/v.py b/v.py\n+class UserValidator:\n+    pass\n"

    with (
        patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=diff),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=project_root),
    ):
        result = handle_reconcile_mode(
            config,
            run_dir,
            "review-uncheckable",
            "2026-06-04T00:00:00Z",
            ["PRD-TEST-009"],
        )

    not_checkable = result["fr_not_checkable"]
    assert isinstance(not_checkable, list)
    surfaced = {e["fr"] for e in not_checkable}
    assert "FR02" in surfaced, "identifier-less FR must be surfaced, not silently clean"
    assert result["not_checkable_count"] == 1
    # No mismatch on the checkable FR, but the result is NOT a bare clean signal:
    # the not-checkable surface is present alongside it.
    assert result["mismatch_count"] == 0


def test_reconcile_result_labels_coverage_method(
    run_dir: Path,
    tmp_path: Path,
) -> None:
    """REAL reconcile path: result self-documents the presence-matching method."""
    config = make_config(prds_relative_path="prds")
    project_root = tmp_path / "project"
    prds_dir = project_root / "prds"
    prds_dir.mkdir(parents=True)
    (prds_dir / "PRD-TEST-001.md").write_text(_SAMPLE_PRD, encoding="utf-8")

    with (
        patch(
            "trw_mcp.tools._review_helpers._get_git_diff",
            return_value=SAMPLE_DIFF_WITH_MATCHES,
        ),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=project_root),
    ):
        result = handle_reconcile_mode(
            config,
            run_dir,
            "review-label",
            "2026-06-04T00:00:00Z",
            ["PRD-TEST-001"],
        )

    assert result["coverage_method"] == "identifier_presence_in_diff"
    assert result["coverage_method"] == RECONCILE_COVERAGE_METHOD


def test_reconcile_no_governing_prd_is_qualified_clean(run_dir: Path) -> None:
    """No governing PRD -> verdict stays 'clean' but carries no_governing_prd=True.

    So a consumer cannot read the clean verdict as "FRs verified covered".
    """
    config = make_config()
    with (
        patch("trw_mcp.state.prd_utils.discover_governing_prds", return_value=[]),
        patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="some diff"),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=run_dir.parent),
    ):
        result = handle_reconcile_mode(
            config,
            run_dir,
            "review-noprd",
            "2026-06-04T00:00:00Z",
            None,
        )

    assert result["verdict"] == "clean"  # enum unchanged -> callers unaffected
    assert result["no_governing_prd"] is True
    assert result["reason"]  # honest reason string present
    assert result["coverage_method"] == RECONCILE_COVERAGE_METHOD


def test_reconcile_genuinely_covered_fr_still_reports_covered(
    run_dir: Path,
    tmp_path: Path,
) -> None:
    """A covered FR (identifier present in diff) yields no mismatch -- unchanged."""
    config = make_config(prds_relative_path="prds")
    project_root = tmp_path / "project"
    prds_dir = project_root / "prds"
    prds_dir.mkdir(parents=True)
    (prds_dir / "PRD-TEST-001.md").write_text(_SAMPLE_PRD, encoding="utf-8")

    with (
        patch(
            "trw_mcp.tools._review_helpers._get_git_diff",
            return_value=SAMPLE_DIFF_WITH_MATCHES,
        ),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=project_root),
    ):
        result = handle_reconcile_mode(
            config,
            run_dir,
            "review-covered",
            "2026-06-04T00:00:00Z",
            ["PRD-TEST-001"],
        )

    assert result["verdict"] == "clean"
    assert result["mismatch_count"] == 0
    # Sample PRD's FRs all carry identifiers -> none are flagged not-checkable.
    assert result["not_checkable_count"] == 0
    assert result["fr_not_checkable"] == []


# Local copy of the support module's SAMPLE_PRD (every FR has an identifier),
# inlined to keep this test self-describing about why not_checkable_count == 0.
_SAMPLE_PRD = """\
---
id: PRD-TEST-001
status: draft
---
# PRD-TEST-001: Test Feature

## 3. Functional Requirements

### FR01: Implement `UserValidator` class

The `UserValidator` class MUST validate input using the `--strict` flag.

Given a user input
When `validate()` is called with `--strict`
Then validation errors are returned as `ValidationResult` objects.

### FR02: Add `DataProcessor` pipeline

The `DataProcessor` MUST support `--dry-run` mode for testing.

## 4. Non-Functional Requirements

NFR content here.
"""
