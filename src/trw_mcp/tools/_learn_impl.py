"""Core learn logic — extracted from learning.py for module-size compliance.

Dependencies that test suites patch at ``trw_mcp.tools.learning.*`` are
injected as parameters by the closure in ``learning.py`` so that patches
remain effective without needing to know about this module.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import LearnResultDict
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

# Side-effect helpers extracted to _learn_side_effects (PRD-DIST-243 batch 9).
# Re-exported so existing test imports continue to work.
from trw_mcp.tools._learn_anchors import resolve_learn_anchors
from trw_mcp.tools._learn_journal_wiring import (
    capture_journal_payload,
    consume_journal,
    journal_accepted,
)
from trw_mcp.tools._learn_preflight import resolve_learn_deps, run_accept_gates
from trw_mcp.tools._learn_side_effects import (
    _LEARN_INJECTION_PATTERNS as _LEARN_INJECTION_PATTERNS,
)
from trw_mcp.tools._learn_side_effects import (
    _MAX_DETAIL_CHARS as _MAX_DETAIL_CHARS,
)
from trw_mcp.tools._learn_side_effects import (
    _MAX_SUMMARY_CHARS as _MAX_SUMMARY_CHARS,
)
from trw_mcp.tools._learn_side_effects import (
    _append_provenance_signed as _append_provenance_signed,
)
from trw_mcp.tools._learn_side_effects import (
    _auxiliary_content_reject as _auxiliary_content_reject,
)
from trw_mcp.tools._learn_side_effects import (
    _content_policy_reject as _content_policy_reject,
)
from trw_mcp.tools._learn_side_effects import (
    _default_is_solution as _default_is_solution,
)
from trw_mcp.tools._learn_side_effects import (
    _handle_consolidation as _handle_consolidation,
)
from trw_mcp.tools._learn_side_effects import (
    _save_yaml_backup as _save_yaml_backup,
)
from trw_mcp.tools._learn_side_effects import (
    _store_accepts_positional_trw_dir as _store_accepts_positional_trw_dir,
)
from trw_mcp.tools._learning_helpers import (
    LearningParams,
    calibrate_impact,
    check_soft_cap,
    enforce_distribution,
)
from trw_mcp.tools._state_assertion_hint import (
    propose_validity_window,
    validity_window_nudge,
)

logger = structlog.get_logger(__name__)


def execute_learn(
    summary: str,
    detail: str,
    trw_dir: Path,
    config: TRWConfig,
    *,
    tags: list[str] | None = None,
    evidence: list[str] | None = None,
    impact: float = 0.5,
    shard_id: str | None = None,
    source_type: str = "agent",
    source_identity: str = "",
    client_profile: str = "",
    model_id: str = "",
    consolidated_from: list[str] | None = None,
    assertions: list[dict[str, str]] | None = None,
    is_solution_fn: Callable[[str], bool] | None = None,
    # PRD-CORE-110: Typed learning fields
    type: str = "pattern",
    nudge_line: str = "",
    expires: str = "",
    confidence: str = "unverified",
    task_type: str = "",
    domain: list[str] | None = None,
    phase_origin: str = "",
    phase_affinity: list[str] | None = None,
    team_origin: str = "",
    protection_tier: str = "normal",
    session_id: str | None = None,
    scope: str = "auto",  # PRD-CORE-185 FR07: write-tier override
    # Injected deps (patched at trw_mcp.tools.learning.* in tests).
    # justified Any: these are optional test-seam overrides for heterogeneous
    # functions (store_learning, generate_learning_id, save_learning_entry,
    # update_analytics, list_active_learnings, check_and_handle_dedup) whose
    # return values feed directly into typed downstream calls (LearningParams,
    # _append_provenance_signed, enforce_distribution, ...). Narrowing to
    # ``Callable[..., object] | None`` does not type the *results*, so it forces
    # ~10 explicit ``cast`` calls at every use site for zero added safety; the
    # real type contract is enforced by the concrete default each ``or``-falls
    # back to below. Keep ``Any`` here deliberately.
    #
    # PRD-FIX-130-FR03: two of these are ALSO production seams, not test-only.
    # The journal drain injects ``_list_active_learnings`` (the sweep's shared
    # active set, resolved once instead of once per record) and
    # ``_save_learning_entry`` (bound to the sweep's index sink, so the learnings
    # index is rewritten once per sweep instead of once per stored record). Both
    # are per-sweep costs that were being paid per record; see
    # ``tools/_learn_journal_wiring.replay_journaled_learn``.
    _adapter_store: Any = None,
    _generate_learning_id: Any = None,
    _save_learning_entry: Any = None,
    _update_analytics: Any = None,
    _list_active_learnings: Any = None,
    _check_and_handle_dedup: Any = None,
    # Write-ahead-journal replay seam (durability fix). ``_replay_learning_id``
    # forces the original id so a crash-recovery replay is idempotent;
    # ``_from_journal`` suppresses re-journaling on that replay path.
    _replay_learning_id: str | None = None,
    _from_journal: bool = False,
) -> LearnResultDict:
    """Execute the core learn workflow: validate, dedup, store, distribute.

    Args:
        summary: One-line summary.
        detail: Full context.
        trw_dir: Resolved .trw directory path.
        config: TRW configuration.
        tags: Categorization tags.
        evidence: Supporting evidence.
        impact: Impact score 0.0-1.0.
        shard_id: Optional shard identifier.
        source_type: Learning provenance.
        source_identity: Name of source.
        consolidated_from: IDs of superseded entries.
        assertions: Machine-verifiable assertions.
        is_solution_fn: Callable to detect solution patterns.
        _adapter_store: Injected store_learning function.
        _generate_learning_id: Injected ID generator.
        _save_learning_entry: Injected YAML backup writer.
        _update_analytics: Injected analytics updater.
        _list_active_learnings: Injected active learnings lister.
        _check_and_handle_dedup: Injected dedup checker.
    """
    # Snapshot the replayable original args BEFORE any local mutation so the
    # write-ahead journal persists raw caller inputs (calibrate_impact etc. are
    # not idempotent). Captured now; written only once the entry is ACCEPTED.
    _journal_payload = capture_journal_payload(dict(locals()))

    # Resolve injected deps with fallbacks (see _learn_preflight.LearnDeps).
    deps = resolve_learn_deps(
        _adapter_store,
        _generate_learning_id,
        _save_learning_entry,
        _update_analytics,
        _list_active_learnings,
        _check_and_handle_dedup,
    )

    # Input validation (PRD-QUAL-042-FR06): impact bounds
    impact = max(0.0, min(1.0, impact))

    # Acceptance gates — noise filter, then content policy, then the opt-in LLM
    # utility filter. These MUST stay ahead of the journal write below: a
    # rejected learning must never become a durable, replayable record.
    rejection = run_accept_gates(summary, detail, config, logger)
    if rejection is not None:
        return rejection

    # Same gate, the fields the long-form scan never saw. Runs here rather than
    # inside run_accept_gates so the pre-journal boundary that module owns stays
    # a single cohesive slice; the ordering guarantee that matters is unchanged —
    # this is still strictly BEFORE journal_accepted, so a rejected learning is
    # never durably recorded or replayed.
    aux_rejection = _auxiliary_content_reject(tags, evidence, nudge_line)
    if aux_rejection is not None:
        logger.warning(
            "learn_content_policy_rejected",
            reason=aux_rejection["reason"],
            field_group="auxiliary",
            summary_preview=summary[:60],
        )
        return cast("LearnResultDict", aux_rejection)

    # Durability fix: the learning has now passed every acceptance gate (noise,
    # content policy, LLM utility). Assign its stable id and DURABLY JOURNAL it
    # BEFORE the slow pre-store pipeline (active-set load + semantic dedup +
    # embedding cold-start) that can push this call past the client's 120s tool
    # timeout. If the session then exits mid-pipeline, the next session_start
    # sweep replays the journaled record so the accepted learning is never
    # silently lost. Consumed on every terminal-handled path below (and retained
    # only on a store error, which the replay retries).
    learning_id = _replay_learning_id or deps.generate_id()
    journal_accepted(trw_dir, config, learning_id, _journal_payload, from_journal=_from_journal)

    reader = FileStateReader()
    writer = FileStateWriter()
    entries_dir = trw_dir / config.learnings_dir / config.entries_dir
    writer.ensure_dir(entries_dir)

    # One-time batch dedup migration (PRD-CORE-042 FR05).
    #
    # PRD-FIX-130-FR05: NEVER on the journal-replay path. This branch runs the
    # full O(N^2) embed-and-compare scan of every active entry synchronously
    # inside whichever learn happens to be first — and during a drain that is the
    # FIRST REPLAY. Measured 2026-09-04: 307.7 s of a 321,050 ms session_start,
    # against a 33,552 ms control on the same corpus with the marker present.
    # FR01's guarantee ("the sweep overruns by at most one record's replay") is
    # vacuous if one replay can take 307 s, so the skip is a correctness
    # dependency of the budget, not a nicety. The migration is not LOST by being
    # skipped: the marker is still unwritten, the drain reschedules it onto the
    # FR02 background thread, and the interactive path below is unchanged.
    if config.dedup_enabled and not _from_journal:
        try:
            from trw_mcp.state.dedup import batch_dedup, is_migration_needed

            if is_migration_needed(trw_dir):
                batch_dedup(trw_dir, reader, writer, config=config)
        except (ImportError, OSError, ValueError, TypeError):
            logger.debug("learning_migration_failed", exc_info=True)

    # PRD-CORE-110 / PRD-FIX-052-FR05: typed metadata defaults.
    from trw_mcp.tools._learn_metadata import prepare_nudge_line, prepare_tags, resolve_phase_origin

    phase_origin = resolve_phase_origin(phase_origin, logger)
    nudge_line = prepare_nudge_line(nudge_line, summary)
    safe_tags = prepare_tags(
        tags,
        summary=summary,
        is_solution_fn=is_solution_fn,
        default_is_solution=_default_is_solution,
        log=logger,
    )

    # PRD-QUAL-056-FR06: audit-originated learnings must carry typed metadata
    # even when the caller only supplied the audit-finding tags.
    #
    # Resolve via getattr() rather than ``from ... import`` so a long-running
    # MCP server that cached an older analytics.core module (pre-FR06) does
    # not crash trw_learn with ImportError — it falls back to pass-through
    # values and logs a clear restart hint instead.
    from trw_mcp.state.analytics import core as _analytics_core

    _normalize = getattr(_analytics_core, "normalize_audit_learning_metadata", None)
    if _normalize is not None:
        audit_metadata = _normalize(
            safe_tags,
            type=type,
            confidence=confidence,
            domain=domain,
            phase_affinity=phase_affinity,
        )
        type = str(audit_metadata["type"])
        confidence = str(audit_metadata["confidence"])
        domain = cast("list[str]", audit_metadata["domain"])
        phase_affinity = cast("list[str]", audit_metadata["phase_affinity"])
    else:
        logger.warning(
            "audit_metadata_normalizer_unavailable",
            hint="restart MCP server to pick up PRD-QUAL-056-FR06 normalization",
            module_path=getattr(_analytics_core, "__file__", "<unknown>"),
        )

    # Bayesian calibration of impact score (PRD-CORE-034)
    calibrated_impact = calibrate_impact(impact, config)

    # Fetch active learnings once -- reused by soft-cap and distribution
    all_active: list[dict[str, object]] = []
    with contextlib.suppress(OSError, StateError, ValueError, TypeError):
        all_active = deps.list_active(trw_dir)
    calibrated_impact, distribution_soft_cap_warning = check_soft_cap(
        calibrated_impact,
        all_active,
        config,
    )

    # Semantic dedup check (PRD-CORE-042) -- must run BEFORE storing
    safe_evidence = evidence or []
    dedup_result = deps.dedup(
        LearningParams(
            summary=summary,
            detail=detail,
            learning_id=learning_id,
            tags=safe_tags,
            evidence=safe_evidence,
            impact=calibrated_impact,
            shard_id=shard_id,
            source_type=source_type,
            source_identity=source_identity,
            client_profile=client_profile,
            model_id=model_id,
            assertions=assertions,
            type=type,
            nudge_line=nudge_line,
            expires=expires,
            confidence=confidence,
            task_type=task_type,
            domain=domain,
            phase_origin=phase_origin,
            phase_affinity=phase_affinity,
            team_origin=team_origin,
            protection_tier=protection_tier,
        ),
        entries_dir,
        reader,
        writer,
        config,
    )
    if dedup_result is not None:
        # Deduped (skip/merge) is a terminal-handled outcome — the content is
        # accounted for in the survivor, so retire the pending record.
        consume_journal(trw_dir, config, learning_id)
        return cast("LearnResultDict", dedup_result)

    # PRD-CORE-111 FR04 + PRD-CORE-267 FR01/FR02: code-grounded anchors drawn
    # from THIS session's own pinned run, gated on demonstrated overlap with
    # the learning's own text. Delegated to _learn_anchors so this module stays
    # under the size gate.
    project_root = trw_dir.parent if trw_dir.name == ".trw" else trw_dir
    anchors, anchor_validity = resolve_learn_anchors(
        project_root,
        learning_id,
        session_id=session_id,
        summary=summary,
        detail=detail,
        evidence=safe_evidence,
    )

    # Store via SQLite adapter (primary path).  Preserve compatibility with
    # older injected test doubles that either take ``trw_dir`` positionally or
    # accept only ``**kwargs``.
    store_kwargs: dict[str, object] = {
        "learning_id": learning_id,
        "summary": summary,
        "detail": detail,
        "tags": safe_tags,
        "evidence": safe_evidence,
        "impact": calibrated_impact,
        "shard_id": shard_id,
        "source_type": source_type,
        "source_identity": source_identity,
        "client_profile": client_profile,
        "model_id": model_id,
        "assertions": assertions,
        "type": type,
        "nudge_line": nudge_line,
        "expires": expires,
        "confidence": confidence,
        "task_type": task_type,
        "domain": domain,
        "phase_origin": phase_origin,
        "phase_affinity": phase_affinity,
        "team_origin": team_origin,
        "protection_tier": protection_tier,
        "anchors": anchors,
        "anchor_validity": anchor_validity,
        "session_id": session_id,
        "scope": scope,  # PRD-CORE-185 FR07: write-tier override
    }
    if _store_accepts_positional_trw_dir(deps.store):
        store_result = deps.store(trw_dir, **store_kwargs)
    else:
        store_result = deps.store(trw_dir=trw_dir, **store_kwargs)
    store_result_dict = store_result if isinstance(store_result, dict) else {}
    if store_result_dict.get("status") == "quarantined":
        # Quarantine is a deliberate anomaly-detector decision, not a loss — the
        # entry landed in the quarantine store. Retire the pending record so it
        # is not replayed (and re-quarantined) on the next sweep.
        consume_journal(trw_dir, config, learning_id)
        return {
            "learning_id": learning_id,
            "path": str(store_result_dict.get("path", f"sqlite://{learning_id}")),
            "status": "quarantined",
            "distribution_warning": "",
        }
    # D8 (dual-write atomicity): the SQLite row is the source of truth. When the
    # store fails, ``store_learning`` returns ``status="error"`` from
    # ``_store_error_result`` instead of raising (JSON-RPC boundary contract), so
    # a naive fall-through would still write the YAML sidecar below — producing an
    # UNRECALLABLE YAML-with-no-DB-row. Worse, because the entry never lands in the
    # DB, the semantic dedup check (which reads the DB) can never suppress a retry,
    # so the same summary accumulates one orphan sidecar per attempt (the observed
    # "one summary 92x" pathology, and the 8-hex ``L-{token_hex(4)}`` ids that
    # appear precisely when trw_memory is unavailable — the same condition that
    # makes the store fail). Returning here keeps the sidecar strictly downstream
    # of a confirmed DB write, so YAML never survives a store the DB rejected.
    if store_result_dict.get("status") == "error":
        logger.warning(
            "learn_store_failed_no_sidecar",
            learning_id=learning_id,
            error=str(store_result_dict.get("error", "")),
        )
        return {
            "learning_id": learning_id,
            "path": str(store_result_dict.get("path", f"sqlite://{learning_id}")),
            "status": "error",
            "distribution_warning": "",
        }
    # The SQLite row is now durable (source of truth per D8). The write-ahead
    # journal has done its job — retire the pending record so it is not replayed.
    consume_journal(trw_dir, config, learning_id)
    _append_provenance_signed(
        trw_dir=trw_dir,
        learning_id=learning_id,
        summary=summary,
        detail=detail,
        source_identity=source_identity or source_type,
    )

    # PRD-FIX-052-FR04: Auto-obsolete superseded entries
    _handle_consolidation(learning_id, consolidated_from, entries_dir, reader, writer, trw_dir)

    # Save YAML backup via analytics (dual-write for rollback safety)
    params = LearningParams(
        summary=summary,
        detail=detail,
        learning_id=learning_id,
        tags=safe_tags,
        evidence=safe_evidence,
        impact=calibrated_impact,
        shard_id=shard_id,
        source_type=source_type,
        source_identity=source_identity,
        client_profile=client_profile,
        model_id=model_id,
        assertions=assertions,
        type=type,
        nudge_line=nudge_line,
        expires=expires,
        confidence=confidence,
        task_type=task_type,
        domain=domain,
        phase_origin=phase_origin,
        phase_affinity=phase_affinity,
        team_origin=team_origin,
        protection_tier=protection_tier,
        anchors=anchors,
        anchor_validity=anchor_validity,
    )
    entry_path = _save_yaml_backup(
        params,
        consolidated_from=consolidated_from,
        trw_dir=trw_dir,
        entries_dir=entries_dir,
        save_entry_fn=deps.save_entry,
        update_analytics_fn=deps.update_analytics,
    )

    # Forced distribution enforcement (PRD-CORE-034)
    distribution_warning, _demoted_ids = enforce_distribution(
        impact,
        calibrated_impact,
        learning_id,
        all_active,
        trw_dir,
        config,
    )

    logger.info(
        "learn_ok",
        summary_len=len(summary),
        tags=safe_tags,
        impact=calibrated_impact,
        id=learning_id,
    )
    result_dict: LearnResultDict = {
        "learning_id": learning_id,
        "path": str(entry_path),
        "status": str(store_result_dict.get("status", "recorded")),
    }
    # Advisory only when there is something to advise — an empty
    # distribution_warning on every call is response noise.
    if distribution_warning:
        result_dict["distribution_warning"] = distribution_warning
    if distribution_soft_cap_warning:
        result_dict["distribution_warning"] = distribution_soft_cap_warning

    # PRD-CORE-244-FR05: offer a validity window for a state-asserting learning.
    # This runs AFTER a successful store and does NOT touch the ``expires`` value
    # forwarded above: silent stamping is prohibited, because a classifier that
    # puts a TTL on an invariant makes the store less true than one that puts
    # none anywhere. Fail-open — an advisory string must never fail a learn.
    try:
        window = propose_validity_window(summary, detail, type, config.state_learning_default_ttl_days)
        if window is not None:
            result_dict["validity_window_nudge"] = validity_window_nudge(learning_id, window)
    except Exception:  # justified: fail-open, advisory text only
        logger.debug("validity_window_nudge_skipped", exc_info=True)

    # Increment ceremony progress state (PRD-CORE-074 FR04)
    try:
        from trw_mcp.state.ceremony_progress import increment_learnings

        increment_learnings(trw_dir)
    except Exception:  # justified: fail-open
        logger.debug("learn_ceremony_state_update_skipped", exc_info=True)

    # Inject ceremony progress summary into response.
    try:
        from trw_mcp.tools._ceremony_status_context import append_ceremony_status_for_tool

        append_ceremony_status_for_tool(cast("dict[str, object]", result_dict), trw_dir, tool_name="learn")
    except Exception:  # justified: fail-open
        logger.debug("learn_ceremony_status_skipped", exc_info=True)

    return result_dict
