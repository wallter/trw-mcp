"""TRW session ceremony tools — trw_session_start, trw_deliver.

PRD-CORE-019: Composite tools that reduce ceremony from 7 manual calls
to 2, with partial-failure resilience on each sub-operation.
PRD-CORE-049: Phase-contextual auto-recall in trw_session_start.

Review tool: trw_mcp.tools.review (PRD-QUAL-022)
Checkpoint tools: trw_mcp.tools.checkpoint (PRD-CORE-053)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.scoring import rank_by_utility
from trw_mcp.state._paths import find_active_run, resolve_project_root, resolve_trw_dir
from trw_mcp.state.analytics import (
    find_success_patterns,
    mark_promoted,
    update_analytics,
    update_analytics_sync,
)
from trw_mcp.state.claude_md import (
    collect_context_data,
    collect_patterns,
    collect_promotable_learnings,
    load_claude_md_template,
    merge_trw_section,
    render_adherence,
    render_architecture,
    render_behavioral_protocol,
    render_categorized_learnings,
    render_ceremony_flows,
    render_ceremony_table,
    render_conventions,
    render_closing_reminder,
    render_imperative_opener,
    render_patterns,
    render_phase_descriptions,
    render_template,
)
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
)
from trw_mcp.state.receipts import log_recall_receipt
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger()

_config = get_config()
_reader = FileStateReader()
_writer = FileStateWriter()
_events = FileEventLogger(_writer)

# --- Phase-contextual auto-recall (PRD-CORE-049) ---

_PHASE_TAG_MAP: dict[str, list[str]] = {
    "research": ["architecture", "gotcha", "codebase"],
    "plan": ["architecture", "pattern", "dependency"],
    "implement": ["gotcha", "testing", "pattern"],
    "validate": ["testing", "build", "coverage"],
    "review": ["security", "performance", "maintainability"],
    "deliver": ["ceremony", "deployment", "integration"],
}


def _phase_to_tags(phase: str) -> list[str]:
    """Map a framework phase to relevant learning tags (PRD-CORE-049 FR02)."""
    return _PHASE_TAG_MAP.get(phase.lower(), [])


# Re-export checkpoint helpers for backward compatibility with tests/hooks
from trw_mcp.tools.checkpoint import (  # noqa: E402
    _do_checkpoint,
    _maybe_auto_checkpoint,
    _reset_tool_call_counter,
)

# Suppress unused import warnings — these are re-exports
__all__ = ["_maybe_auto_checkpoint", "_reset_tool_call_counter", "_do_checkpoint"]


def _get_run_status(run_dir: Path) -> dict[str, object]:
    """Extract status summary from a run directory."""
    result: dict[str, object] = {"active_run": str(run_dir)}
    try:
        run_yaml = run_dir / "meta" / "run.yaml"
        if run_yaml.exists():
            data = _reader.read_yaml(run_yaml)
            result["phase"] = str(data.get("phase", "unknown"))
            result["status"] = str(data.get("status", "unknown"))
            result["task_name"] = str(data.get("task_name", ""))
    except (StateError, OSError, ValueError):
        result["status"] = "error_reading"
    return result


def _run_step(
    name: str,
    fn: object,  # Callable[[], dict[str, object] | None]
    results: dict[str, object],
    errors: list[str],
) -> None:
    """Execute a delivery step with fail-open error handling.

    If ``fn`` returns a dict, it is stored in ``results[name]``.
    If ``fn`` returns None, nothing is stored (used for conditional steps).
    Exceptions are appended to ``errors`` and a failure dict is stored.
    """
    from collections.abc import Callable
    assert isinstance(fn, Callable)  # type: ignore[arg-type]
    try:
        step_result = fn()  # type: ignore[operator]
        if step_result is not None:
            results[name] = step_result
    except Exception as exc:
        errors.append(f"{name}: {exc}")
        results[name] = {"status": "failed", "error": str(exc)}


# --- Deliver step helpers ---


def _step_checkpoint(resolved_run: Path) -> dict[str, object]:
    """Step 2: Delivery state snapshot."""
    _do_checkpoint(resolved_run, "delivery")
    return {"status": "success"}


def _step_auto_prune(trw_dir: Path) -> dict[str, object] | None:
    """Step 2.5: Auto-prune excess learnings."""
    if not _config.learning_auto_prune_on_deliver:
        return None
    from trw_mcp.state.analytics import auto_prune_excess_entries
    prune_result = auto_prune_excess_entries(
        trw_dir,
        max_entries=_config.learning_auto_prune_cap,
    )
    pruned = int(str(prune_result.get("actions_taken", 0)))
    return prune_result if pruned > 0 else None


def _step_consolidation(trw_dir: Path) -> dict[str, object]:
    """Step 2.6: Memory consolidation (PRD-CORE-044)."""
    if not _config.memory_consolidation_enabled:
        return {"status": "skipped", "reason": "disabled"}
    from trw_mcp.state.consolidation import consolidate_cycle
    return dict(consolidate_cycle(
        trw_dir,
        max_entries=_config.memory_consolidation_max_per_cycle,
    ))


def _step_tier_sweep(trw_dir: Path) -> dict[str, object]:
    """Step 2.7: Tier lifecycle sweep (PRD-CORE-043)."""
    from trw_mcp.state.tiers import TierManager
    tier_mgr = TierManager(trw_dir, _reader, _writer)
    sweep_result = tier_mgr.sweep()
    return {
        "status": "success",
        "promoted": sweep_result.promoted,
        "demoted": sweep_result.demoted,
        "purged": sweep_result.purged,
        "errors": sweep_result.errors,
    }


def _step_auto_progress(resolved_run: Path | None) -> dict[str, object]:
    """Step 5: Auto-progress PRD statuses."""
    return _do_auto_progress(resolved_run)


def _step_publish_learnings() -> dict[str, object]:
    """Step 6: Publish high-impact learnings to platform backend."""
    from trw_mcp.telemetry.publisher import publish_learnings
    return dict(publish_learnings())


def _step_outcome_correlation() -> dict[str, object]:
    """Step 6.5: Outcome correlation (G1)."""
    from trw_mcp.scoring import process_outcome_for_event
    outcome_ids = process_outcome_for_event("trw_deliver_complete")
    return {"status": "success", "updated": len(outcome_ids)}


def _step_recall_outcome(resolved_run: Path | None) -> dict[str, object]:
    """Step 6.6: Recall outcome tracking (G6)."""
    from trw_mcp.state.recall_tracking import get_recall_stats, record_outcome
    recall_stats = get_recall_stats()
    unique_ids = recall_stats.get("unique_learnings", 0)
    recalled_count = 0
    if unique_ids and resolved_run is not None:
        trw_dir_rt = resolve_trw_dir()
        tracking_path = trw_dir_rt / "logs" / "recall_tracking.jsonl"
        if tracking_path.exists():
            from trw_mcp.state.persistence import FileStateReader as _FSR
            rt_reader = _FSR()
            records_rt = rt_reader.read_jsonl(tracking_path)
            seen: set[str] = set()
            for rec in records_rt:
                lid = str(rec.get("learning_id", ""))
                if lid and rec.get("outcome") is None and lid not in seen:
                    record_outcome(lid, "positive")
                    seen.add(lid)
                    recalled_count += 1
    return {"status": "success", "recorded": recalled_count}


def _step_telemetry(resolved_run: Path | None) -> dict[str, object]:
    """Step 7: Telemetry events (G3 + G4)."""
    from trw_mcp.models.config import get_config as _get_cfg
    from trw_mcp.telemetry.client import TelemetryClient
    from trw_mcp.telemetry.models import CeremonyComplianceEvent, SessionEndEvent
    cfg = _get_cfg()
    tel_client = TelemetryClient.from_config()
    tools_invoked = 0
    if resolved_run is not None:
        ev_path = resolved_run / "meta" / "events.jsonl"
        if ev_path.exists():
            from trw_mcp.state.persistence import FileStateReader as _FSR2
            ev_reader = _FSR2()
            tools_invoked = len(ev_reader.read_jsonl(ev_path))
    tel_client.record_event(SessionEndEvent(
        installation_id=cfg.installation_id or "local",
        framework_version=cfg.framework_version,
        tools_invoked=tools_invoked,
    ))
    run_id_str = str(resolved_run.name) if resolved_run else "unknown"
    tel_client.record_event(CeremonyComplianceEvent(
        installation_id=cfg.installation_id or "local",
        framework_version=cfg.framework_version,
        run_id=run_id_str,
        score=0,
    ))
    tel_client.flush()
    return {"status": "success", "events": 2}


def _step_batch_send() -> dict[str, object]:
    """Step 8: Batch send (G2)."""
    from trw_mcp.telemetry.sender import BatchSender
    return dict(BatchSender.from_config().send())


def register_ceremony_tools(server: FastMCP) -> None:
    """Register session ceremony composite tools on the MCP server."""

    @server.tool()
    @log_tool_call
    def trw_session_start() -> dict[str, object]:
        """Load your prior learnings and any active run — gives you full context before writing code.

        Recalls high-impact learnings (patterns, gotchas, architecture decisions) and
        checks for an active run (phase, progress, last checkpoint). Without this context,
        you risk re-implementing solved problems or repeating mistakes from prior sessions.

        Partial-failure resilient: if recall fails, run status is still returned and vice versa.
        """
        results: dict[str, object] = {"timestamp": datetime.now(timezone.utc).isoformat()}
        errors: list[str] = []

        # Step 1: Recall high-impact learnings via SQLite adapter (compact mode)
        try:
            from trw_mcp.state.memory_adapter import recall_learnings as adapter_recall
            from trw_mcp.state.memory_adapter import update_access_tracking as adapter_update_access
            trw_dir = resolve_trw_dir()

            # adapter_recall returns list[dict] directly
            learnings = adapter_recall(
                trw_dir, query="*", min_impact=0.7,
                max_results=_config.recall_max_results, compact=True,
            )

            # Update access tracking for recalled IDs
            matched_ids = [str(e.get("id", "")) for e in learnings if e.get("id")]
            adapter_update_access(trw_dir, matched_ids)
            log_recall_receipt(trw_dir, "*", matched_ids)

            results["learnings"] = learnings
            results["learnings_count"] = len(learnings)
            results["total_available"] = len(learnings)
        except Exception as exc:
            errors.append(f"recall: {exc}")
            results["learnings"] = []
            results["learnings_count"] = 0

        # Step 2: Check active run status
        run_dir: Path | None = None
        try:
            run_dir = find_active_run()
            if run_dir is not None:
                results["run"] = _get_run_status(run_dir)
            else:
                results["run"] = {"active_run": None, "status": "no_active_run"}
        except Exception as exc:
            errors.append(f"status: {exc}")
            results["run"] = {"active_run": None, "status": "error"}

        # Step 3: Log session_start event (FR01, PRD-CORE-031)
        try:
            event_data: dict[str, object] = {
                "learnings_recalled": int(str(results.get("learnings_count", 0))),
                "run_detected": run_dir is not None,
            }
            if run_dir is not None:
                events_path = run_dir / "meta" / "events.jsonl"
                if events_path.parent.exists():
                    _events.log_event(events_path, "session_start", event_data)
            else:
                trw_dir_path = resolve_trw_dir()
                context_path = trw_dir_path / _config.context_dir
                _writer.ensure_dir(context_path)
                fallback_path = context_path / "session-events.jsonl"
                _events.log_event(fallback_path, "session_start", event_data)
        except Exception:
            pass  # Fail-open: event write failure must not affect tool result

        # Step 4: Check for available updates (PRD-INFRA-014)
        try:
            from trw_mcp.state.auto_upgrade import check_for_update
            update_info = check_for_update()
            if update_info.get("available"):
                results["update_advisory"] = update_info.get("advisory")

            # Auto-apply if configured
            cfg = get_config()
            if update_info.get("available") and cfg.auto_upgrade:
                from trw_mcp.state.auto_upgrade import perform_upgrade
                upgrade_result = perform_upgrade(update_info)
                if upgrade_result.get("applied"):
                    parts: list[str] = []
                    parts.append(f"Auto-upgraded to v{upgrade_result.get('version', '?')}: {upgrade_result.get('details', '')}")
                    results["auto_upgrade"] = upgrade_result
        except Exception:
            pass  # Fail-open: update check failure must not affect tool result

        # Step 5: Auto-close stale runs (orphaned run prevention)
        try:
            if _config.run_auto_close_enabled:
                from trw_mcp.state.analytics_report import auto_close_stale_runs
                close_result = auto_close_stale_runs()
                closed_count = int(str(close_result.get("count", 0)))
                if closed_count > 0:
                    results["stale_runs_closed"] = close_result
        except Exception:
            pass  # Fail-open: maintenance must not break session start

        # Step 6: Phase-contextual auto-recall (PRD-CORE-049)
        try:
            if _config.auto_recall_enabled:
                from trw_mcp.state.memory_adapter import recall_learnings as adapter_recall_step6

                trw_dir_ar = resolve_trw_dir()

                # Build context query from active run
                query_tokens: list[str] = []
                phase_tags: list[str] | None = None
                if run_dir is not None:
                    run_status = results.get("run", {})
                    if isinstance(run_status, dict):
                        task_name = str(run_status.get("task_name", ""))
                        phase = str(run_status.get("phase", ""))
                        if task_name:
                            query_tokens.append(task_name)
                        if phase:
                            query_tokens.append(phase)
                            phase_tag_list = _phase_to_tags(phase)
                            if phase_tag_list:
                                phase_tags = phase_tag_list

                ar_query = " ".join(query_tokens) if query_tokens else "*"
                ar_entries = adapter_recall_step6(
                    trw_dir_ar, query=ar_query,
                    tags=phase_tags, min_impact=0.5,
                    max_results=0, compact=False,
                )
                if ar_entries:
                    ranked = rank_by_utility(
                        ar_entries, query_tokens,
                        lambda_weight=_config.recall_utility_lambda,
                    )
                    capped = ranked[:_config.auto_recall_max_results]
                    results["auto_recalled"] = [
                        {"id": e.get("id"), "summary": e.get("summary"), "impact": e.get("impact")}
                        for e in capped
                    ]
                    results["auto_recall_count"] = len(capped)
        except Exception:
            pass  # Fail-open: auto-recall must not break session start

        # Step 7: Embeddings status check + backfill
        try:
            from trw_mcp.state.memory_adapter import check_embeddings_status
            emb_status = check_embeddings_status()
            if emb_status.get("advisory"):
                results["embeddings_advisory"] = emb_status["advisory"]
            elif emb_status.get("enabled") and emb_status.get("available"):
                # First-time backfill: generate embeddings for existing entries
                from trw_mcp.state.memory_adapter import backfill_embeddings
                backfill = backfill_embeddings(resolve_trw_dir())
                if backfill.get("embedded", 0) > 0:
                    results["embeddings_backfill"] = backfill
        except Exception:
            pass  # Fail-open: embedding setup must not break session start

        results["errors"] = errors
        results["success"] = len(errors) == 0

        logger.info(
            "trw_session_start_complete",
            learnings=results.get("learnings_count", 0),
            errors=len(errors),
        )
        return results

    @server.tool()
    @log_tool_call
    def trw_deliver(
        run_path: str | None = None,
        skip_reflect: bool = False,
        skip_index_sync: bool = False,
    ) -> dict[str, object]:
        """Persist your learnings and progress for future sessions — without this, your work is invisible to the next agent.

        Runs reflect (extract learnings from events), checkpoint (save final state),
        CLAUDE.md sync (promote high-impact learnings), and INDEX/ROADMAP sync.
        Each sub-operation runs independently — a failure in one step does not
        block the others. Your learnings become available to every future session.

        Args:
            run_path: Path to run directory (auto-detected if None).
            skip_reflect: Skip reflection step (e.g., if already reflected).
            skip_index_sync: Skip INDEX/ROADMAP sync step.
        """
        results: dict[str, object] = {"timestamp": datetime.now(timezone.utc).isoformat()}
        errors: list[str] = []
        trw_dir = resolve_trw_dir()

        # Resolve run path
        resolved_run: Path | None = None
        if run_path:
            resolved_run = Path(run_path).resolve()
        else:
            resolved_run = find_active_run()

        results["run_path"] = str(resolved_run) if resolved_run else None

        # Step 0: Review soft gate (PRD-QUAL-022)
        if resolved_run is not None:
            review_path = resolved_run / "meta" / "review.yaml"
            if review_path.exists():
                try:
                    review_data = _reader.read_yaml(review_path)
                    rv_verdict = str(review_data.get("verdict", ""))
                    rv_critical = int(str(review_data.get("critical_count", 0)))
                    if rv_verdict == "block" and rv_critical > 0:
                        results["review_warning"] = (
                            f"Review has {rv_critical} critical findings. "
                            f"Delivery proceeding but review issues should be addressed."
                        )
                except Exception:
                    pass  # Fail-open: review gate should not block delivery
            else:
                results["review_advisory"] = (
                    "No trw_review was run before delivery. "
                    "Consider running trw_review for quality assurance."
                )

        # Premature delivery guard: warn if run has only init/ceremony events
        if resolved_run is not None:
            events_path = resolved_run / "meta" / "events.jsonl"
            if _reader.exists(events_path):
                all_events = _reader.read_jsonl(events_path)
                ceremony_only = {"run_init", "checkpoint", "reflection_complete",
                                 "trw_reflect_complete", "trw_deliver_complete",
                                 "trw_session_start_complete"}
                work_events = [
                    e for e in all_events
                    if str(e.get("event", "")) not in ceremony_only
                ]
                if len(work_events) == 0 and len(all_events) > 0:
                    results["warning"] = (
                        "Premature delivery — no work events found beyond ceremony. "
                        "This run has only init/checkpoint events. Proceeding anyway, "
                        "but consider whether work was actually completed."
                    )
                    logger.warning(
                        "premature_delivery",
                        total_events=len(all_events),
                        work_events=0,
                    )

        # Step 1: Reflect (extract learnings from events)
        if not skip_reflect:
            _run_step("reflect", lambda: _do_reflect(trw_dir, resolved_run), results, errors)
        else:
            results["reflect"] = {"status": "skipped"}

        # Step 2: Checkpoint (delivery state snapshot)
        if resolved_run is not None:
            _run_step("checkpoint", lambda: _step_checkpoint(resolved_run), results, errors)
        else:
            results["checkpoint"] = {"status": "skipped", "reason": "no_active_run"}

        # Step 2.5: Auto-prune excess learnings (prevents saturation)
        _run_step("auto_prune", lambda: _step_auto_prune(trw_dir), results, errors)

        # Step 2.6: Memory consolidation (PRD-CORE-044)
        _run_step("consolidation", lambda: _step_consolidation(trw_dir), results, errors)

        # Step 2.7: Tier lifecycle sweep (PRD-CORE-043)
        _run_step("tier_sweep", lambda: _step_tier_sweep(trw_dir), results, errors)

        # Step 3: CLAUDE.md sync
        _run_step("claude_md_sync", lambda: _do_claude_md_sync(trw_dir), results, errors)

        # Step 4: INDEX.md / ROADMAP.md sync
        if not skip_index_sync:
            _run_step("index_sync", lambda: _do_index_sync(), results, errors)
        else:
            results["index_sync"] = {"status": "skipped"}

        # Step 5: Auto-progress PRD statuses (PRD-CORE-025 via GAP-PROC-001)
        _run_step("auto_progress", lambda: _step_auto_progress(resolved_run), results, errors)

        # Step 6: Publish high-impact learnings to platform backend (PRD-CORE-033)
        _run_step("publish_learnings", lambda: _step_publish_learnings(), results, errors)

        # Step 6.5: Outcome correlation (G1)
        _run_step("outcome_correlation", lambda: _step_outcome_correlation(), results, errors)

        # Step 6.6: Recall outcome tracking (G6)
        _run_step("recall_outcome", lambda: _step_recall_outcome(resolved_run), results, errors)

        # Step 7: Telemetry events (G3 + G4)
        _run_step("telemetry", lambda: _step_telemetry(resolved_run), results, errors)

        # Step 8: Batch send (G2)
        _run_step("batch_send", lambda: _step_batch_send(), results, errors)

        results["errors"] = errors
        results["success"] = len(errors) == 0
        results["steps_completed"] = 10 - len(errors)

        logger.info(
            "trw_deliver_complete",
            steps_completed=results.get("steps_completed"),
            errors=len(errors),
        )
        return results

def _do_reflect(
    trw_dir: Path,
    run_dir: Path | None,
) -> dict[str, object]:
    """Execute reflection logic — extract learnings from events.

    Simplified version of the full trw_reflect tool, focused on
    mechanical extraction for delivery ceremony.
    """
    from trw_mcp.state.analytics import (
        extract_learnings_mechanical,
        find_repeated_operations,
        is_error_event,
    )

    _writer.ensure_dir(trw_dir / _config.learnings_dir / _config.entries_dir)
    _writer.ensure_dir(trw_dir / _config.reflections_dir)

    events: list[dict[str, object]] = []

    if run_dir:
        events_path = run_dir / "meta" / "events.jsonl"
        if _reader.exists(events_path):
            events = _reader.read_jsonl(events_path)

    error_events = [e for e in events if is_error_event(e)]
    repeated_ops = find_repeated_operations(events)
    success_patterns = find_success_patterns(events)

    new_learnings = extract_learnings_mechanical(
        error_events, repeated_ops, trw_dir,
        max_errors=5, max_repeated=3,
    )

    # Success patterns are analytics data only — do NOT create learning entries
    # (PRD-FIX-021: suppress telemetry noise from "Success: X (Nx)" entries).

    if run_dir:
        if (run_dir / "meta").exists():
            _events.log_event(run_dir / "meta" / "events.jsonl", "reflection_complete", {
                "reflection_id": "delivery",
                "scope": "delivery",
                "learnings_produced": len(new_learnings),
            })

    update_analytics(trw_dir, len(new_learnings))

    return {
        "status": "success",
        "events_analyzed": len(events),
        "learnings_produced": len(new_learnings),
        "success_patterns": len(success_patterns),
    }


def _do_claude_md_sync(trw_dir: Path) -> dict[str, object]:
    """Execute CLAUDE.md sync — promote high-impact learnings."""
    project_root = resolve_project_root()
    high_impact = collect_promotable_learnings(trw_dir, _config, _reader)
    patterns = collect_patterns(trw_dir, _config, _reader)
    arch_data, conv_data = collect_context_data(trw_dir, _config, _reader)

    template = load_claude_md_template(trw_dir)
    behavioral_protocol = render_behavioral_protocol()

    tpl_context: dict[str, str] = {
        "imperative_opener": render_imperative_opener(),
        "behavioral_protocol": behavioral_protocol,
        "ceremony_phases": render_phase_descriptions(),
        "ceremony_table": render_ceremony_table(),
        "ceremony_flows": render_ceremony_flows(),
        "architecture_section": render_architecture(arch_data),
        "conventions_section": render_conventions(conv_data),
        "categorized_learnings": render_categorized_learnings(high_impact),
        "patterns_section": render_patterns(patterns),
        "adherence_section": render_adherence(high_impact),
        "closing_reminder": render_closing_reminder(),
    }
    trw_section = render_template(template, tpl_context)

    target = project_root / "CLAUDE.md"
    total_lines = merge_trw_section(target, trw_section, _config.claude_md_max_lines)
    update_analytics_sync(trw_dir)

    for learning in high_impact:
        lid = learning.get("id", "")
        if isinstance(lid, str) and lid:
            mark_promoted(trw_dir, lid)

    return {
        "status": "success",
        "path": str(target),
        "learnings_promoted": len(high_impact),
        "total_lines": total_lines,
    }


def _do_index_sync() -> dict[str, object]:
    """Execute INDEX.md and ROADMAP.md sync from PRD frontmatter."""
    from trw_mcp.state.index_sync import sync_index_md, sync_roadmap_md

    project_root = resolve_project_root()
    prds_dir = project_root / Path(_config.prds_relative_path)
    aare_dir = prds_dir.parent

    index_result = sync_index_md(aare_dir / "INDEX.md", prds_dir, writer=_writer)
    roadmap_result = sync_roadmap_md(aare_dir / "ROADMAP.md", prds_dir, writer=_writer)

    return {
        "status": "success",
        "index": index_result,
        "roadmap": roadmap_result,
    }


def _do_auto_progress(run_dir: Path | None) -> dict[str, object]:
    """Auto-progress PRD statuses for the deliver phase.

    Calls ``auto_progress_prds`` with phase="deliver" for all PRDs
    in the run's ``prd_scope``. Skipped if no active run.
    """
    if run_dir is None:
        return {"status": "skipped", "reason": "no_active_run"}

    from trw_mcp.state.validation import auto_progress_prds

    project_root = resolve_project_root()
    prds_dir = project_root / Path(_config.prds_relative_path)
    if not prds_dir.is_dir():
        return {"status": "skipped", "reason": "prds_dir_not_found"}

    progressions = auto_progress_prds(run_dir, "deliver", prds_dir, _config)

    return {
        "status": "success",
        "total_evaluated": len(progressions),
        "applied": sum(1 for p in progressions if p.get("applied")),
        "progressions": progressions,
    }
