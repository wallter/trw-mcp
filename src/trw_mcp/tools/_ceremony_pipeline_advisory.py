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


def _primary_target_identity(trw_dir: Path) -> tuple[str | None, str | None]:
    """Return ``(primary target label, its last success timestamp)`` from sync state.

    PRD-FIX-125-FR02. Read through :class:`SyncCoordinator` rather than a second
    JSON parser so there is one reader of the sync-state schema. Both values are
    ``None`` on a pre-FR01 state file or an unreadable one — this surface is
    fail-open, so a missing identity degrades the warning's detail and never the
    warning itself.

    NFR03: ``primary_target_label`` is the hostname-style label produced by
    ``_label_for_url`` — never a URL with userinfo, never the api key.
    """
    from trw_mcp.sync.coordinator import SyncCoordinator

    coordinator = SyncCoordinator(trw_dir=trw_dir)
    return coordinator.get_primary_target_label(), coordinator.get_last_push_at()


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
    (``{"severity": ..., "reasons": [...], "enforced_by": ...}``) so the
    breakage is surfaced, not buried in the compact advisory string.

    This session-start surface is intentionally fail-OPEN (it never blocks the
    hot path); the fail-CLOSED enforcement lives in ``check_pipeline_health``.

    The warning therefore names ``make check`` and NOT delivery.
    ``check_pipeline_health`` has exactly two consumers — this function and its
    own ``__main__`` behind the ``pipeline-health`` Make target — so no deliver
    gate reads pipeline health, and the earlier ``"enforce": True`` / "fix before
    delivery" wording asserted an enforcement point that does not exist.
    ``enforced_by`` states where the fail-closed check actually lives instead of
    claiming one here. Since PRD-FIX-125-FR02 that target is a prerequisite of
    ``make check``, so the warning names the aggregate developers actually run.

    PRD-FIX-125-FR02 also enriches the warning with the PRIMARY sync target's
    label and its last observed success, read from ``.trw/sync-state.json``. A
    bare "push staleness" reason cannot distinguish "never worked" from "worked
    until <date>", and the target it describes was ambiguous while the counter
    quantified over every configured target.

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
        else:
            # DEF-05: ``degraded`` is False for BOTH a fully healthy pipeline
            # AND a pipeline where every probe crashed unmeasured (PRD-CORE-
            # 263-FR03 deliberately excludes an unmeasured probe from the
            # degraded verdict). This branch used to omit the advisory in
            # both cases, so an operator reading a clean session_start could
            # not tell "confirmed healthy" from "we could not check". Only
            # fires when something was genuinely unmeasured — a healthy
            # session pays zero tokens for it, per PRD-INFRA-068.
            unmeasured = health.get("unmeasured")
            if isinstance(unmeasured, list) and unmeasured:
                names = ", ".join(str(name) for name in unmeasured)
                advisory = f"pipeline health: {names} could not be measured — call trw_pipeline_health() for detail"
                results["pipeline_health_advisory"] = advisory
                logger.warning(
                    "session_start_pipeline_unmeasured",
                    signals=unmeasured,
                    count=len(unmeasured),
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
            primary_label, primary_last_success_at = _primary_target_identity(trw_dir)
            results["pipeline_health_warning"] = {
                "severity": "error",
                "reasons": reasons,
                "primary_target_label": primary_label,
                "primary_last_success_at": primary_last_success_at,
                "enforced_by": "make check (pipeline-health)",
                "advisory": (
                    "Compounding pipeline is broken — call trw_pipeline_health() for detail. "
                    "The fail-closed check runs inside `make check`; no TRW tool blocks on this."
                ),
            }
            logger.error(
                "session_start_pipeline_gate_tripped",
                reasons=reasons,
                count=len(reasons),
            )
    except Exception as exc:  # justified: fail-open, gate escalation must not block session start
        record_into(cast("MutableMapping[str, object]", results), "pipeline_health_gate", exc)
