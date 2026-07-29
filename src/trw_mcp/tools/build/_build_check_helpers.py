"""Pure result-assembly helpers for the ``trw_build_check`` reporter.

Belongs to the ``build/_registration.py`` facade. Re-exported there for
back-compat. Extracted so ``_registration.py`` stays under the 350 effective-LOC
gate. These are self-contained functions with no MCP/server dependencies:
input validation (``_require_tests_passed``) and coverage-threshold enforcement
(``_finalize_build_result``).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.models._evidence_plans import BuildCommandResult

_BUILD_CHECK_USAGE = (
    "trw_build_check(tests_passed=True, test_count=47, failure_count=0, coverage_pct=92.3, "
    "static_checks_clean=True, scope='pytest tests/')"
)


def reconcile_typed_results(
    typed_results: tuple[BuildCommandResult, ...],
    *,
    tests_passed: bool | None,
    static_checks_clean: bool | None,
) -> tuple[bool, bool]:
    """Derive ``(tests_passed, static_checks_clean)`` from typed command results.

    Every rejection here must name the fault that actually occurred. The prior
    version derived ``False`` for any required id that was simply ABSENT and
    then reported "tests_passed contradicts typed command result 'tests'" — an
    error that accuses the caller of misreporting a run they reported
    correctly, and, when the legacy boolean was omitted too, silently recorded
    a passing run as failed with no error at all. Both halves are HB-1/HB-4
    problems: a diagnosis must not misstate the fault, and evidence must never
    be invented in either direction.
    """
    from trw_mcp.tools._evidence_writers import COMMAND_RESULT_EXAMPLE, REQUIRED_BUILD_COMMAND_IDS

    by_id = {item.command_id: item for item in typed_results}
    missing = [command_id for command_id in REQUIRED_BUILD_COMMAND_IDS if command_id not in by_id]
    if missing:
        raise ValueError(
            f"command_results is missing required command_id(s) {missing}. "
            f"Supplied ids: {sorted(by_id)}. Every required command needs its own entry so the "
            f"outcome is reported rather than inferred. Example: {COMMAND_RESULT_EXAMPLE}"
        )

    derived = {command_id: by_id[command_id].passed for command_id in REQUIRED_BUILD_COMMAND_IDS}
    for command_id, reported in (("tests", tests_passed), ("static_checks", static_checks_clean)):
        if reported is not None and reported != derived[command_id]:
            raise ValueError(
                f"reported outcome for {command_id!r} disagrees with its command result: "
                f"you passed {reported!r}, but command_results[{command_id!r}] has "
                f"exit_code={by_id[command_id].exit_code} (a non-zero exit code means failed). "
                f"Fix whichever one is wrong, or omit the boolean and let the command result stand."
            )
    return derived["tests"], derived["static_checks"]


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


def derive_duration_secs(
    command_results: tuple[BuildCommandResult, ...] | None,
) -> float | None:
    """Return observed wall-clock across reported commands, or None if unknown.

    ``trw_build_check`` executes nothing — the caller runs the commands and
    reports outcomes — so the tool has no clock of its own. It CAN, however,
    derive a real duration when the caller supplies typed command results,
    which carry ``started_at`` / ``completed_at``: the span from the earliest
    start to the latest completion.

    Returns ``None`` — not ``0.0`` — when NO entry carries a usable pair of
    timestamps. The distinction is the point: ``0.0`` reads as "measured, and
    it was instant", which was never true.

    PARTIAL evidence is spanned, not rejected: entries missing or failing to
    parse a timestamp are skipped and the span is computed from whichever
    entries did parse. That is a deliberate trade — a report where only the
    lint command carries timestamps yields the lint's span, which UNDERSTATES
    the run. Callers needing to know how much of the run is covered should
    compare against ``len(command_results)``; discarding a real partial
    measurement would lose more than it protects.
    """
    if not command_results:
        return None
    starts: list[datetime] = []
    ends: list[datetime] = []
    for item in command_results:
        started = _parse_iso(item.started_at)
        completed = _parse_iso(item.completed_at)
        if started is None or completed is None:
            continue
        starts.append(started)
        ends.append(completed)
    if not starts:
        return None
    try:
        latest_end = max(ends)
        earliest_start = min(starts)
        span = (latest_end - earliest_start).total_seconds()
    except TypeError:
        # Mixed offset-aware and offset-naive timestamps in one report. Python
        # refuses to subtract them, and guessing a timezone would fabricate the
        # measurement this function exists to stop fabricating.
        return None
    # A negative span means clock skew or transposed fields in the report —
    # unusable as a measurement, so it degrades to unknown rather than to zero.
    return span if span >= 0.0 else None


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, returning None on anything unusable.

    The trailing ``Z`` is normalized to ``+00:00`` first. ``fromisoformat`` did
    not accept ``Z`` until Python 3.11 and this package declares ``>=3.10``, so
    without this the common CI timestamp form parses as garbage and a genuinely
    measured duration degrades to "unmeasured" — the fabrication guard failing
    in the direction that discards real evidence. Every other ISO parser in the
    package already normalizes this way.
    """
    if not value:
        return None
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _require_tests_passed(tests_passed: bool | None) -> bool:
    """Require explicit tests_passed reporting with a usage example."""
    if tests_passed is None:
        raise ValueError(
            f"tests_passed is required. Report the outcome after running tests via Bash. Example: {_BUILD_CHECK_USAGE}"
        )
    return tests_passed
