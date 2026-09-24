"""Learning and knowledge deferred delivery steps.

Sub-module of ``_deferred_delivery`` — contains steps for learning
publishing, outcome correlation, recall tracking, trust increment,
index sync, and auto-progress.

Test patches should still target the parent facade:
``patch("trw_mcp.tools._deferred_delivery._step_publish_learnings")``.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import structlog

from trw_mcp.models.typed_dicts import (
    AutoProgressStepResult,
    IndexSyncResult,
    PublishLearningsResult,
    TrustIncrementResult,
)

# PRD-FIX-061-FR02: Canonical definition moved to state/_session_events.py.
# Re-exported here for backward compatibility with existing consumers and tests.
from trw_mcp.state._session_events import _merge_session_events as _merge_session_events
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._deferred_learning_rework import (
    _step_collect_rework_metrics as _step_collect_rework_metrics,
)

logger = structlog.get_logger(__name__)


def _step_publish_learnings() -> PublishLearningsResult:
    """Step 6: Publish high-impact learnings to platform backend."""
    from trw_mcp.telemetry.publisher import publish_learnings

    return cast("PublishLearningsResult", dict(publish_learnings()))


def _step_trust_increment(resolved_run: Path | None) -> TrustIncrementResult | None:
    """Advance trust only from an eligible, one-time-consumed outcome receipt.

    PRD-CORE-206 supersedes PRD-FIX-053-FR02: learning/checkpoint/edit/commit
    counts are activity, not verification, and a raw build *event* is replayable —
    none of them reach this step. The only positive route is a validated typed
    PRD-CORE-205 receipt consumed atomically at most once (``trust.py``).

    NFR01 interim compatibility: while the receipt substrate runs in ``observe``
    mode (enforcement deferred), trust stays frozen — the eligibility matrix and
    atomic consumption are live and tested but not yet driven from delivery. In
    ``enforce`` mode this collects the run's current typed receipts, applies the
    closed task-type/evidence matrix, and consumes one eligible aggregate outcome.
    """
    from trw_mcp.models._evidence_core import EvidenceMode
    from trw_mcp.models.config import get_config
    from trw_mcp.state import trust as _trust
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
    from trw_mcp.state._session_id import resolve_effective_session_id
    from trw_mcp.tools._delivery_helpers import _read_run_yaml
    from trw_mcp.tools._evidence_gates import read_evidence_mode

    config = get_config()
    mode = read_evidence_mode(config)
    if mode is not EvidenceMode.ENFORCE:
        # observe / unknown mode → frozen (NFR01): no raw event or activity route.
        return {"skipped": True, "reason": "trust_frozen_receipts_observe_mode"}

    trw_dir = resolve_trw_dir()
    project_root = resolve_project_root()
    reader = FileStateReader()
    run_data = _read_run_yaml(resolved_run, reader) if resolved_run is not None else {}
    task_type = str(run_data.get("task_type", "unknown"))
    session_id = resolve_effective_session_id(trw_dir)

    eligibility, consume = _trust.evaluate_and_consume_trust_outcome(
        trw_dir,
        resolved_run,
        project_root,
        task_type,
        session_id=session_id,
        config=config,
    )
    if consume is None or not consume.incremented:
        reason = consume.reason if consume is not None else eligibility.reason
        return {"skipped": True, "reason": reason}
    return {
        "session_count": consume.session_count,
        "previous_tier": consume.previous_tier,
        "new_tier": consume.new_tier,
        "transitioned": consume.transitioned,
        "reason": "outcome_receipt_consumed",
    }


def _do_index_sync() -> IndexSyncResult:
    """Execute INDEX.md and ROADMAP.md sync from PRD frontmatter."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.state.index_sync import sync_index_md, sync_roadmap_md
    from trw_mcp.state.persistence import FileStateWriter

    config = get_config()
    writer = FileStateWriter()
    project_root = resolve_project_root()
    prds_dir = project_root / Path(config.prds_relative_path)
    aare_dir = prds_dir.parent

    index_result = sync_index_md(aare_dir / "INDEX.md", prds_dir, writer=writer)
    roadmap_result = sync_roadmap_md(aare_dir / "ROADMAP.md", prds_dir, writer=writer)

    return IndexSyncResult(
        status="success",
        index=cast("dict[str, object]", index_result),
        roadmap=cast("dict[str, object]", roadmap_result),
    )


def _do_auto_progress(run_dir: Path | None) -> AutoProgressStepResult:
    """Auto-progress PRD statuses for the deliver phase.

    Calls ``auto_progress_prds`` with phase="deliver" for all PRDs
    in the run's ``prd_scope``. Skipped if no active run.

    Note: ``resolve_project_root`` and ``get_config`` are resolved from
    the ceremony module at call time so that test patches on
    ``trw_mcp.tools.ceremony.*`` propagate correctly.
    """
    if run_dir is None:
        return {"status": "skipped", "reason": "no_active_run"}

    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.state.validation import auto_progress_prds

    config = get_config()
    project_root = resolve_project_root()
    prds_dir = project_root / Path(config.prds_relative_path)
    if not prds_dir.is_dir():
        return {"status": "skipped", "reason": "prds_dir_not_found"}

    def _completion_guard(prd_id: str) -> str | None:
        """PRD-QUAL-119-FR06: only a current COMPLETE effective-completion
        decision permits an implemented-family promotion. Any evaluation error
        fails CLOSED — automated delivery must never mint a lifecycle claim
        without evidence (incident L-EQwV: deferred auto-progress walked seven
        planned PRDs to done)."""
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.state.prd_utils import parse_frontmatter
        from trw_mcp.tools._prd_transition_gate import (
            derive_transition_decision,
            evaluate_prd_coherence,
        )

        try:
            content = (prds_dir / f"{prd_id}.md").read_text(encoding="utf-8")
            frontmatter = parse_frontmatter(content)
            # Re-audit F1: evaluate AS IF the PRD held the implemented status —
            # the promotion IS the implemented claim being certified.
            report = evaluate_prd_coherence(
                prd_id, run_dir, FileStateReader(), gate_mode="block", target_status="implemented"
            )
            decision = derive_transition_decision(prd_id, report, frontmatter, content)
        except Exception:  # justified: fail-closed — no evidence, no promotion (NFR01)
            logger.warning("auto_progress_completion_guard_degraded", prd_id=prd_id, exc_info=True)
            return "completion_evaluation_failed"
        if decision.permits_transition:
            return None
        return f"{decision.outcome.value}: {'; '.join(decision.reasons[:3])}"

    progressions = auto_progress_prds(run_dir, "deliver", prds_dir, config, completion_guard=_completion_guard)

    # PRD-QUAL-120-FR03 (P09 activation): delivery is the production writer of
    # the out-of-band AcceptanceManifest for every scoped PRD. Audit F6/F7
    # (2026-07-11): the outcome is INDEPENDENTLY RE-DERIVED from the resulting
    # file state via the universal decision — never parsed from progression
    # reason strings and never trusted from the in-process promotion result
    # (the stale-server incident class). Fail-open per PRD: a derivation error
    # skips that manifest; absence is a visible coverage gap, never a pass.
    manifests_written = 0
    try:
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.acceptance_manifest import derive_manifest, persist_manifest
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.state.prd_utils import discover_governing_prds, parse_frontmatter
        from trw_mcp.tools._prd_transition_gate import (
            derive_transition_decision,
            evaluate_prd_coherence,
        )

        trw_dir = resolve_trw_dir()
        reader = FileStateReader()
        for prd_id in discover_governing_prds(run_dir):
            prd_file = prds_dir / f"{prd_id}.md"
            if not prd_file.exists():
                continue
            try:
                content = prd_file.read_text(encoding="utf-8")
                frontmatter = parse_frontmatter(content)
                # Re-derive against the implemented claim: idempotent for a PRD
                # that actually reached implemented; a planned/partial PRD
                # derives its true non-complete outcome regardless of what the
                # in-process promotion reported.
                report = evaluate_prd_coherence(prd_id, run_dir, reader, gate_mode="block", target_status="implemented")
                decision = derive_transition_decision(prd_id, report, frontmatter, content)
                outcome = decision.outcome.value
                # Audit F7 (P0, empirically reproduced): the target_status
                # override evaluates the HYPOTHETICAL implemented claim.
                # COMPLETE additionally requires the PRD's ACTUAL on-disk
                # status to be in the implemented family — a transition that
                # stalled short (partial BFS, guard stop, stale server) can
                # never project complete (NFR01: partial evidence never passes).
                actual_status = str(frontmatter.get("status", "")).strip().lower()
                if outcome == "complete" and actual_status not in (
                    "implemented",
                    "done",
                    "delivered",
                    "complete",
                ):
                    outcome = "incomplete"
                manifest = derive_manifest(prd_file, {}, completion_outcome=outcome)
                persist_manifest(manifest, trw_dir)
                manifests_written += 1
            except Exception:  # justified: per-PRD fail-open, absence is visible downstream
                logger.warning("acceptance_manifest_persist_failed", prd_id=prd_id, exc_info=True)
    except Exception:  # justified: manifest step is additive; delivery result stands
        logger.warning("acceptance_manifest_step_degraded", exc_info=True)

    return {
        "status": "success",
        "total_evaluated": len(progressions),
        "applied": sum(1 for p in progressions if p.get("applied")),
        "progressions": progressions,
    }


def _step_auto_progress(resolved_run: Path | None) -> AutoProgressStepResult:
    """Step 5: Auto-progress PRD statuses."""
    return _do_auto_progress(resolved_run)


# ---------------------------------------------------------------------------
# Sprint 84: Delivery metrics (PRD-CORE-104)
# ---------------------------------------------------------------------------


def _step_delivery_metrics(trw_dir: Path, resolved_run: Path | None) -> dict[str, object]:
    """Step 12: Compute delivery metrics — rework rate and learning exposure.

    Both feed backend attribution through the synced session metrics.

    Fail-open: returns partial results if individual metrics fail.
    """
    result: dict[str, object] = {"status": "success"}

    # Rework rate (PRD-CORE-103-FR04) — based on git modified files
    try:
        import subprocess

        from trw_mcp.scoring.rework_rate import compute_rework_rate

        project_root = trw_dir.parent if trw_dir.name == ".trw" else trw_dir
        git_result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(project_root),
        )
        changed = [f.strip() for f in git_result.stdout.strip().split("\n") if f.strip()]
        rework = compute_rework_rate(changed, project_root=str(project_root))
        result["rework_rate"] = rework
    except Exception:  # justified: fail-open, individual metric failure
        logger.debug("delivery_metric_rework_rate_failed", exc_info=True)
        result["rework_rate"] = {"error": "computation_failed"}

    # Learning exposure (recall pull rate from surface tracking).
    # PRD-CORE-144 FR02/FR04: scope to the current session and capture ids.
    try:
        from trw_mcp.state._session_id import resolve_effective_session_id
        from trw_mcp.state.surface_tracking import compute_recall_pull_rate

        sid = resolve_effective_session_id(trw_dir)
        pull_rate, nudge_count, learning_ids = compute_recall_pull_rate(
            trw_dir,
            session_id=sid,
        )
        result["learning_exposure"] = {
            "recall_pull_rate": round(pull_rate, 4),
            "nudge_count": nudge_count,
            "ids": learning_ids,
        }
    except Exception:  # justified: fail-open
        logger.debug("delivery_metric_learning_exposure_failed", exc_info=True)
        result["learning_exposure"] = {"error": "computation_failed"}

    # PRD-CORE-104 P0: Add client_profile and model_family to session metrics
    try:
        from trw_mcp.models.config import get_config

        cfg = get_config()
        result["client_profile"] = cfg.client_profile.client_id if hasattr(cfg.client_profile, "client_id") else ""
        result["model_family"] = getattr(cfg, "model_family", "") or ""
    except Exception:  # justified: fail-open, metadata enrichment is best-effort
        logger.debug("delivery_metric_client_metadata_failed", exc_info=True)

    logger.info(
        "delivery_metrics_computed",
        metrics=[k for k in result if k != "status"],
    )

    return result
