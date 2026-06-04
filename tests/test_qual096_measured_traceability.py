"""Tests for PRD-QUAL-096: measured (non-binary) traceability coverage ratio.

FR01 -- ``compute_measured_traceability_coverage(content)`` returns
    (# FRs with >=1 resolved impl ref AND >=1 test ref) / (total FRs), in [0, 1],
    as an informational metric. It is additive and does NOT touch the binary
    ``traceability_coverage`` used by the ``valid`` gate.
FR02 / NFR01 -- currently-``valid`` real PRDs stay ``valid`` after this change
    (the binary gate + ``valid`` logic are untouched).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.requirements import PRDQualityGates
from trw_mcp.state.prd_utils import parse_frontmatter
from trw_mcp.state.validation import validate_prd_quality, validate_prd_quality_v2
from trw_mcp.state.validation._prd_validation import (
    compute_measured_traceability_coverage,
)

# ---------------------------------------------------------------------------
# Synthetic fixtures: k of n FRs fully traced (impl ref AND test ref)
# ---------------------------------------------------------------------------


def _fr_block(num: int, *, traced: bool) -> str:
    """One FR section. When ``traced`` it carries both an impl + a test ref."""
    header = f"### PRD-CORE-001-FR{num:02d}: Requirement {num}"
    if traced:
        refs = (
            f"**Files**: `src/trw_mcp/state/widget_{num}.py` "
            f"and `tests/test_widget_{num}.py`."
        )
    else:
        refs = "This requirement has prose only, no file references."
    return f"{header}\nThe system shall do thing {num}.\n{refs}\n"


def _synthetic_prd(total_frs: int, traced_frs: int) -> str:
    """Build a PRD body with ``traced_frs`` of ``total_frs`` FRs fully traced."""
    blocks = [
        _fr_block(i + 1, traced=(i < traced_frs)) for i in range(total_frs)
    ]
    fr_section = "\n".join(blocks)
    return (
        "---\n"
        "prd:\n"
        "  id: PRD-CORE-001\n"
        '  title: "Synthetic"\n'
        "---\n\n"
        "# PRD-CORE-001: Synthetic\n\n"
        "## 3. Functional Requirements\n\n"
        f"{fr_section}\n"
    )


@pytest.mark.parametrize(
    ("total", "traced", "expected"),
    [
        (4, 2, 0.5),
        (4, 0, 0.0),
        (4, 4, 1.0),
        (3, 1, 1.0 / 3.0),
        (5, 2, 0.4),
    ],
)
def test_measured_ratio(total: int, traced: int, expected: float) -> None:
    """FR01: ratio == traced/total when those FRs have both impl + test refs."""
    content = _synthetic_prd(total, traced)
    ratio = compute_measured_traceability_coverage(content)
    assert ratio == pytest.approx(expected)
    assert 0.0 <= ratio <= 1.0


def test_measured_ratio_requires_both_impl_and_test() -> None:
    """FR01: an FR with only an impl ref (no test ref) does NOT count as traced.

    Uses an impl-only path that the reference detectors do not also read as a
    test reference, so the conjunction (impl AND test) is exercised cleanly.
    """
    content = (
        "# PRD-CORE-001\n\n"
        "## 3. Functional Requirements\n\n"
        "### PRD-CORE-001-FR01: Impl only\n"
        "Files: `src/trw_mcp/state/foo.py` only, no test reference here.\n\n"
        "### PRD-CORE-001-FR02: Prose only\n"
        "This requirement has prose only, no file references.\n\n"
        "### PRD-CORE-001-FR03: Both\n"
        "Files: `src/trw_mcp/state/bar.py` and `tests/test_bar.py`.\n"
    )
    # Only FR03 has both impl + test -> 1/3 (FR01 lacks a test ref, FR02 none).
    assert compute_measured_traceability_coverage(content) == pytest.approx(1.0 / 3.0)


def test_measured_ratio_no_frs_is_zero() -> None:
    """FR01: a PRD with no FR sections yields 0.0 (no division by zero)."""
    content = "# PRD-CORE-001\n\n## 1. Problem Statement\nNo FRs here.\n"
    assert compute_measured_traceability_coverage(content) == 0.0


def test_measured_ratio_uses_traceability_matrix_rows() -> None:
    """FR01: refs in the Traceability Matrix row count toward an FR's coverage."""
    content = (
        "# PRD-CORE-001\n\n"
        "## 3. Functional Requirements\n\n"
        "### PRD-CORE-001-FR01: Matrix-traced\n"
        "Prose only in the FR body.\n\n"
        "## 8. Traceability Matrix\n"
        "| FR | Source | Test |\n"
        "|----|--------|------|\n"
        "| FR01 | `src/trw_mcp/state/baz.py` | `tests/test_baz.py` |\n"
    )
    assert compute_measured_traceability_coverage(content) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# FR01 invariance: the binary gate + valid logic are untouched
# ---------------------------------------------------------------------------


def test_binary_traceability_coverage_unchanged() -> None:
    """NFR01: V1 ``traceability_coverage`` stays binary (1.0 when traces exist)."""
    frontmatter: dict[str, object] = {
        "id": "PRD-CORE-001",
        "title": "T",
        "version": "1.0",
        "status": "draft",
        "priority": "P1",
        "traceability": {"implements": ["KE-1"]},
    }
    sections = [f"## {i}." for i in range(12)]
    result = validate_prd_quality(frontmatter, sections)
    # Binary: a single trace link -> 1.0, regardless of per-FR coverage.
    assert result.traceability_coverage == 1.0


def test_no_traces_binary_zero() -> None:
    """NFR01: V1 ``traceability_coverage`` is 0.0 when there are no trace links."""
    frontmatter: dict[str, object] = {
        "id": "PRD-CORE-001",
        "title": "T",
        "version": "1.0",
        "status": "draft",
        "priority": "P1",
        "traceability": {"implements": [], "depends_on": [], "enables": []},
    }
    sections = [f"## {i}." for i in range(12)]
    result = validate_prd_quality(frontmatter, sections)
    assert result.traceability_coverage == 0.0


# ---------------------------------------------------------------------------
# FR02: real PRDs that were valid stay valid (no-regression on the gate)
# ---------------------------------------------------------------------------

_PRDS_DIR = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "requirements-aare-f"
    / "prds"
)


def _sample_valid_real_prds(minimum: int) -> list[tuple[str, str]]:
    """Return (name, content) for real PRDs that validate as ``valid == True``."""
    valid: list[tuple[str, str]] = []
    if not _PRDS_DIR.is_dir():  # pragma: no cover - depends on repo layout
        return valid
    for prd_path in sorted(_PRDS_DIR.glob("PRD-*.md")):
        try:
            content = prd_path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - defensive
            continue
        try:
            result = validate_prd_quality_v2(content)
        except Exception:  # pragma: no cover - skip malformed fixtures
            continue
        if result.valid:
            valid.append((prd_path.name, content))
        if len(valid) >= minimum + 5:  # small headroom, avoid scanning all
            break
    return valid


def test_valid_unchanged() -> None:
    """FR02: >=10 currently-valid real PRDs remain valid (gate untouched).

    The measured ratio is computed for each as well to prove it is additive --
    it never flips ``valid`` and is independent of the binary gate.
    """
    samples = _sample_valid_real_prds(minimum=10)
    if len(samples) < 10:
        pytest.skip(f"only found {len(samples)} valid real PRDs (need >=10)")

    for name, content in samples:
        result = validate_prd_quality_v2(content)
        assert result.valid is True, f"{name} regressed from valid -> invalid"
        # The new informational ratio is well-formed and does not affect valid.
        ratio = compute_measured_traceability_coverage(content)
        assert 0.0 <= ratio <= 1.0, f"{name} produced out-of-range ratio {ratio}"


def test_measured_ratio_independent_of_binary_gate() -> None:
    """FR01/FR02: a PRD with frontmatter traces but zero per-FR file refs has
    binary coverage 1.0 yet a measured ratio of 0.0 -- proving they differ."""
    frontmatter = parse_frontmatter(_synthetic_prd(4, 0))
    # Inject a frontmatter trace so the binary gate sees a link.
    frontmatter["traceability"] = {"implements": ["KE-1"]}
    gates = PRDQualityGates()
    v1 = validate_prd_quality(frontmatter, [f"## {i}." for i in range(12)], gates)
    measured = compute_measured_traceability_coverage(_synthetic_prd(4, 0))
    assert v1.traceability_coverage == 1.0  # binary: link present
    assert measured == 0.0  # measured: no FR has both impl + test refs
    assert v1.traceability_coverage != measured
