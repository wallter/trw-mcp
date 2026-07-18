"""Pure result-assembly helpers for the ``trw_build_check`` reporter.

Belongs to the ``build/_registration.py`` facade. Re-exported there for
back-compat. Extracted so ``_registration.py`` stays under the 350 effective-LOC
gate. These are self-contained functions with no MCP/server dependencies:
input validation (``_require_tests_passed``) and coverage-threshold enforcement
(``_finalize_build_result``).
"""

from __future__ import annotations

_BUILD_CHECK_USAGE = (
    "trw_build_check(tests_passed=True, test_count=47, failure_count=0, coverage_pct=92.3, "
    "static_checks_clean=True, scope='pytest tests/')"
)


def _finalize_build_result(
    result: dict[str, object],
    min_coverage: float | None,
) -> None:
    """Apply coverage threshold enforcement and enrich result dict."""
    if min_coverage is None:
        return
    coverage_pct = float(str(result.get("coverage_pct", 0)))
    if coverage_pct < min_coverage:
        result["tests_passed"] = False
        result["coverage_threshold_failed"] = True
        result["coverage_threshold"] = min_coverage
        result["coverage_threshold_message"] = (
            f"Coverage {coverage_pct:.1f}% is below required threshold {min_coverage:.1f}%"
        )


def _require_tests_passed(tests_passed: bool | None) -> bool:
    """Require explicit tests_passed reporting with a usage example."""
    if tests_passed is None:
        raise ValueError(
            f"tests_passed is required. Report the outcome after running tests via Bash. Example: {_BUILD_CHECK_USAGE}"
        )
    return tests_passed
