"""Session-start pipeline-health advisory + fail-closed escalation step.

Belongs to the ``ceremony.py`` facade (via ``_ceremony_session_start_steps.py``).
Re-exported there for back-compat.

Extracted from ``_ceremony_session_start_steps.py`` to keep that module under
the 350 effective-LOC gate. Owns the single ``step_pipeline_health_advisory``
step: the fail-OPEN compact advisory (PRD-FIX-COMPOUNDING-6 FR03) plus the
fail-CLOSED gate escalation (PRD-FIX-107 FR06).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.tools._ceremony_degradations import record_into

if TYPE_CHECKING:
    from collections.abc import MutableMapping
    from pathlib import Path

    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)


def step_pipeline_health_advisory(
    trw_dir: Path,
    results: dict[str, object],
    config: TRWConfig | None = None,
) -> None:
    """PRD-FIX-COMPOUNDING-6 FR03 + PRD-FIX-107 FR06 — pipeline-health advisory + escalation.

    Calls step_pipeline_health() with all five probes. When degraded=True,
    injects ``pipeline_health_advisory`` (a single-line string) into results.
    When healthy, does NOT inject the key (PRD-INFRA-068 lesson: no
    focus-distraction on healthy sessions).

    FR06 ("enforce, don't suggest"): additionally runs the fail-closed
    ``check_pipeline_health`` gate over the three hard-breakage signatures
    (push staleness, dead graph, localhost-only target). When the gate trips,
    ESCALATES — injecting a prominent structured ``pipeline_health_warning``
    (``{"enforce": True, "severity": ..., "reasons": [...]}``) so the
    breakage is surfaced, not buried in the compact advisory string.

    This session-start surface is intentionally fail-OPEN (it never blocks the
    hot path); the fail-CLOSED enforcement lives in ``check_pipeline_health``
    for ``make check`` / CI / deliver-time use.

    Args:
        trw_dir: The resolved .trw directory path.
        results: The session_start result dict (mutated in-place when degraded).
        config: Optional TRWConfig; enables the FR06 gate thresholds + kill
            switch + localhost-only check. Omitted in legacy callers.
    """
    # Resolve ``step_pipeline_health`` via the parent facade so test
    # monkeypatches on ``_ceremony_session_start_steps.step_pipeline_health``
    # propagate (test-monkeypatch indirection pattern). Imported lazily to
    # avoid a load-time cycle with the parent, which re-exports this function.
    from trw_mcp.tools import _ceremony_session_start_steps as _parent

    try:
        health = _parent.step_pipeline_health(trw_dir)
        if bool(health.get("degraded")):
            advisory = str(health.get("advisory", ""))
            if advisory:
                results["pipeline_health_advisory"] = advisory
                logger.warning(
                    "session_start_pipeline_degraded",
                    advisory=advisory,
                )
    except Exception as exc:  # justified: fail-open, pipeline health must not block session start
        record_into(cast("MutableMapping[str, object]", results), "pipeline_health", exc)

    # FR06 escalation: when the fail-closed gate trips, surface a prominent
    # structured warning. Fail-open: gate-eval errors never block session start.
    try:
        from trw_mcp.tools._pipeline_health_gate import check_pipeline_health

        verdict = check_pipeline_health(trw_dir, config)
        if not bool(verdict.get("healthy")) and verdict.get("status") == "degraded":
            reasons = [str(r) for r in verdict.get("reasons", [])]
            results["pipeline_health_warning"] = {
                "enforce": True,
                "severity": "error",
                "reasons": reasons,
                "advisory": (
                    "ENFORCE: compounding pipeline is broken — "
                    "run check_pipeline_health / see trw_pipeline_health() and fix before delivery."
                ),
            }
            logger.error(
                "session_start_pipeline_gate_tripped",
                reasons=reasons,
                count=len(reasons),
            )
    except Exception as exc:  # justified: fail-open, gate escalation must not block session start
        record_into(cast("MutableMapping[str, object]", results), "pipeline_health_gate", exc)
