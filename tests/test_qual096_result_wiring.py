"""Lead-integration wiring test (PRD-QUAL-096): validate_prd_quality_v2 surfaces the
measured traceability coverage ratio on the result object (not just as a standalone
function). Verifies behavior — the result field equals the computed ratio.
"""

from __future__ import annotations

from trw_mcp.state.validation._prd_validation import compute_measured_traceability_coverage
from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

_PRD = "\n".join(
    [
        "---",
        "prd:",
        "  id: PRD-CORE-900",
        "  title: t",
        "  status: draft",
        "  priority: P2",
        "---",
        "## Functional Requirements",
        "### PRD-CORE-900-FR01: traced",
        "Implemented in `src/a.py`. Verified by `tests/test_a.py`.",
        "### PRD-CORE-900-FR02: untraced",
        "(no references)",
    ]
)


def test_result_surfaces_measured_traceability_coverage(config) -> None:
    result = validate_prd_quality_v2(_PRD, config)
    # The result field must equal the standalone computation (proves wiring/population).
    assert result.measured_traceability_coverage == compute_measured_traceability_coverage(_PRD)
    assert 0.0 <= result.measured_traceability_coverage <= 1.0


# Two PRDs that are STRUCTURALLY identical (same sections, same FR count, same binary
# traceability — each has >=1 trace link so binary coverage is 1.0) but differ ONLY in
# the MEASURED ratio: in `_PRD_FULL` both FRs are fully traced (ratio 1.0); in
# `_PRD_HALF` only FR01 is (ratio 0.5). If `valid` were a function of the measured ratio,
# these would diverge; SF3 asserts they do NOT.
_PRD_FULL = "\n".join(
    [
        "---",
        "prd:",
        "  id: PRD-CORE-901",
        "  title: t",
        "  status: draft",
        "  priority: P2",
        "---",
        "## Functional Requirements",
        "### PRD-CORE-901-FR01: traced",
        "Implemented in `src/a.py`. Verified by `tests/test_a.py`.",
        "### PRD-CORE-901-FR02: traced",
        "Implemented in `src/b.py`. Verified by `tests/test_b.py`.",
    ]
)

_PRD_HALF = "\n".join(
    [
        "---",
        "prd:",
        "  id: PRD-CORE-901",
        "  title: t",
        "  status: draft",
        "  priority: P2",
        "---",
        "## Functional Requirements",
        "### PRD-CORE-901-FR01: traced",
        "Implemented in `src/a.py`. Verified by `tests/test_a.py`.",
        "### PRD-CORE-901-FR02: untraced",
        "(no references)",
    ]
)


def test_measured_coverage_is_informational_not_a_gate(config) -> None:
    """The measured ratio must not affect valid (additive only, NFR01/FR02).

    SF3: prove INDEPENDENCE — two PRDs with the SAME binary-traceability + completeness
    gate inputs but DIFFERENT measured ratios must produce an IDENTICAL ``valid``.
    """
    full = validate_prd_quality_v2(_PRD_FULL, config)
    half = validate_prd_quality_v2(_PRD_HALF, config)

    # The measured ratios genuinely differ (this is what makes the test meaningful).
    assert full.measured_traceability_coverage == 1.0
    assert half.measured_traceability_coverage == 0.5
    assert full.measured_traceability_coverage != half.measured_traceability_coverage

    # ... yet the binary gate inputs are identical (both have >=1 trace link).
    assert full.traceability_coverage == half.traceability_coverage

    # ... and therefore ``valid`` is identical: the measured ratio does not gate it.
    assert full.valid == half.valid
