"""Build-status age evaluation for :mod:`phase_gates_build`.

Belongs to the ``phase_gates_build.py`` facade. Extracted so the staleness
decision — including its unparseable-timestamp branch — lives in one place with
its own tests, and so the parent stays under the 350 effective-LOC gate.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import structlog

from trw_mcp.models.requirements import ValidationFailure

logger = structlog.get_logger(__name__)


def evaluate_build_staleness(
    raw_timestamp: object,
    staleness_secs: int,
) -> tuple[bool, list[ValidationFailure]]:
    """``(age_known_stale, failures)`` for a cached build status's timestamp.

    Three outcomes, and the third is the one this function exists for (WD-02):

    - fresh              -> ``(False, [])``
    - measurably old     -> ``(True, [build_staleness])``
    - UNPARSEABLE stamp  -> ``(False, [build_timestamp_unparseable])``

    An unparseable timestamp previously logged at debug and fell through as
    FRESH, which is the fail-open direction: the cached result was accepted as
    covering the current tree on the strength of a field nobody could read. It
    now reports a named failure so the age is surfaced as unknown.

    It deliberately does NOT set the stale flag. The caller uses that flag to
    RELAX its severity (``is_strict_gate = ... and not is_stale ...``), on the
    reasoning that genuinely old test results should not hard-error. An unknown
    age has not earned that relaxation, so claiming staleness here would trade
    one fail-open for another. The content-hash binding in
    ``_delivery_build_gates`` remains the primary staleness detector; this is
    defense in depth.
    """
    failures: list[ValidationFailure] = []
    if not raw_timestamp:
        return False, failures

    text = str(raw_timestamp)
    try:
        cached_dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        age_secs = time.time() - cached_dt.replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError, OSError):
        logger.warning("build_timestamp_unparseable", timestamp=text, outcome="age_unknown")
        failures.append(
            ValidationFailure(
                field="build_status",
                rule="build_timestamp_unparseable",
                message=(
                    f"Build status timestamp {text!r} could not be parsed, so its age is unknown — "
                    "the cached result cannot be shown to cover the current tree. "
                    "Re-run trw_build_check()"
                ),
                severity="warning",
            )
        )
        return False, failures

    if age_secs > staleness_secs:
        failures.append(
            ValidationFailure(
                field="build_status",
                rule="build_staleness",
                message=(
                    f"Build status is {int(age_secs / 60)}m old "
                    f"(threshold: {staleness_secs // 60}m) — "
                    "re-run trw_build_check()"
                ),
                severity="warning",
            )
        )
        return True, failures

    return False, failures
