"""TRW session ceremony tools — trw_session_start, trw_deliver.

PRD-CORE-019: Composite tools that reduce ceremony from 7 manual calls
to 2, with partial-failure resilience on each sub-operation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.models.learning import LearningEntry
from trw_mcp.scoring import rank_by_utility
from trw_mcp.state._paths import find_active_run, resolve_project_root, resolve_trw_dir
from trw_mcp.state.analytics import (
    find_success_patterns,
    generate_learning_id,
    has_existing_success_learning,
    mark_promoted,
    save_learning_entry,
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
from trw_mcp.state.recall_search import (
    search_entries,
    update_access_tracking,
)
from trw_mcp.state.receipts import log_recall_receipt
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger()

_config = get_config()
_reader = FileStateReader()
_writer = FileStateWriter()
_events = FileEventLogger(_writer)


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


def register_ceremony_tools(server: FastMCP) -> None:
    """Register session ceremony composite tools on the MCP server."""

    @server.tool()
    @log_tool_call
    def trw_session_start() -> dict[str, object]:
        """Combined session start: recall high-impact learnings + check run status.

        Replaces the manual sequence of trw_recall('*', min_impact=0.7)
        followed by trw_status(). Runs both operations with partial-failure
        resilience — if recall fails, status is still returned and vice versa.
        """
        results: dict[str, object] = {"timestamp": datetime.now(timezone.utc).isoformat()}
        errors: list[str] = []

        # Step 1: Recall high-impact learnings (compact mode)
        try:
            trw_dir = resolve_trw_dir()
            entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
            matching, matched_files = search_entries(
                entries_dir, [], _reader, min_impact=0.7,
            )
            matched_ids = update_access_tracking(matched_files, _reader, _writer)
            log_recall_receipt(trw_dir, "*", matched_ids)

            ranked = rank_by_utility(
                matching, [], _config.recall_utility_lambda,
            )

            compact_fields = _config.recall_compact_fields
            learnings = [
                {k: v for k, v in e.items() if k in compact_fields}
                for e in ranked[:_config.recall_max_results]
            ]

            results["learnings"] = learnings
            results["learnings_count"] = len(learnings)
            results["total_available"] = len(ranked)
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
        """Combined delivery ceremony: reflect + checkpoint + claude_md_sync + index_sync.

        Replaces the manual sequence of trw_reflect → trw_checkpoint →
        trw_claude_md_sync → trw_index_sync. Each sub-operation runs
        independently — failures in one step do not block subsequent steps.

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
            try:
                reflect_result = _do_reflect(trw_dir, resolved_run)
                results["reflect"] = reflect_result
            except Exception as exc:
                errors.append(f"reflect: {exc}")
                results["reflect"] = {"status": "failed", "error": str(exc)}
        else:
            results["reflect"] = {"status": "skipped"}

        # Step 2: Checkpoint (delivery state snapshot)
        if resolved_run is not None:
            try:
                _do_checkpoint(resolved_run, "delivery")
                results["checkpoint"] = {"status": "success"}
            except Exception as exc:
                errors.append(f"checkpoint: {exc}")
                results["checkpoint"] = {"status": "failed", "error": str(exc)}
        else:
            results["checkpoint"] = {"status": "skipped", "reason": "no_active_run"}

        # Step 3: CLAUDE.md sync
        try:
            sync_result = _do_claude_md_sync(trw_dir)
            results["claude_md_sync"] = sync_result
        except Exception as exc:
            errors.append(f"claude_md_sync: {exc}")
            results["claude_md_sync"] = {"status": "failed", "error": str(exc)}

        # Step 4: INDEX.md / ROADMAP.md sync
        if not skip_index_sync:
            try:
                index_result = _do_index_sync()
                results["index_sync"] = index_result
            except Exception as exc:
                errors.append(f"index_sync: {exc}")
                results["index_sync"] = {"status": "failed", "error": str(exc)}
        else:
            results["index_sync"] = {"status": "skipped"}

        # Step 5: Auto-progress PRD statuses (PRD-CORE-025 via GAP-PROC-001)
        try:
            progress_result = _do_auto_progress(resolved_run)
            results["auto_progress"] = progress_result
        except Exception as exc:
            errors.append(f"auto_progress: {exc}")
            results["auto_progress"] = {"status": "failed", "error": str(exc)}

        results["errors"] = errors
        results["success"] = len(errors) == 0
        results["steps_completed"] = 5 - len(errors)

        logger.info(
            "trw_deliver_complete",
            steps_completed=results["steps_completed"],
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

    # Generate positive learnings from success patterns
    positive_count = 0
    for sp in success_patterns:
        if positive_count >= _config.reflect_max_positive_learnings:
            break
        summary = sp["summary"]
        if has_existing_success_learning(trw_dir, summary):
            continue
        sp_id = generate_learning_id()
        sp_entry = LearningEntry(
            id=sp_id, summary=summary, detail=sp.get("detail", ""),
            tags=["success", "pattern", "auto-discovered"],
            impact=0.5, recurrence=int(sp.get("count", 1)),
            source_type="agent", source_identity="trw_deliver",
        )
        save_learning_entry(trw_dir, sp_entry)
        new_learnings.append({"id": sp_id, "summary": sp_entry.summary})
        positive_count += 1

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


def _do_checkpoint(run_dir: Path, message: str) -> None:
    """Append a checkpoint to the run's checkpoints.jsonl."""
    import json

    checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
    checkpoints_path.parent.mkdir(parents=True, exist_ok=True)

    checkpoint_data = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "message": message,
    }
    with checkpoints_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(checkpoint_data) + "\n")

    events_path = run_dir / "meta" / "events.jsonl"
    if events_path.parent.exists():
        _events.log_event(events_path, "checkpoint", {"message": message})


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
