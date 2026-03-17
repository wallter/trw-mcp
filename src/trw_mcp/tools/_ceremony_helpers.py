"""Extracted helper functions for ceremony.py — trw_session_start and trw_deliver.

Modularizes the two longest tool functions into focused, testable helpers:
- perform_session_recalls: execute focused + baseline recalls, return merged results
- run_auto_maintenance: auto-upgrade, stale run close, embeddings backfill
- check_delivery_gates: review/build gates, premature delivery guard
- finalize_run: checkpoint + run status update (placeholder for future expansion)
- step_log_session_event: log session_start event to events.jsonl
- step_telemetry_startup: queue telemetry events and start pipeline
- step_increment_session_counter: increment sessions_tracked counter
- step_sanitize_and_maintain: sanitize ceremony feedback + run auto-maintenance
- step_embed_health: check embeddings health status
- step_mark_session_started: mark session started in ceremony state
- step_ceremony_nudge: inject ceremony nudge into response
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._defaults import LIGHT_MODE_RECALL_CAP
from trw_mcp.models.typed_dicts import (
    AutoMaintenanceDict,
    AutoRecalledItemDict,
    ComplianceArtifactsDict,
    DeliveryGatesDict,
    FinalizeRunResult,
    RunStatusDict,
    SessionRecallExtrasDict,
)
from trw_mcp.scoring import rank_by_utility
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.ceremony_nudge import NudgeContext, compute_nudge, read_ceremony_state
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
)
from trw_mcp.state.receipts import log_recall_receipt

logger = structlog.get_logger()


# ── FR01: Ceremony nudge injection ──────────────────────────────────────


def append_ceremony_nudge(
    response: dict[str, object],
    trw_dir: Path | None = None,
    available_learnings: int = 0,
    context: NudgeContext | None = None,
) -> dict[str, object]:
    """Append ceremony nudge to a tool response dict.

    Reads ceremony state, computes nudge, adds it under 'ceremony_status' key.
    Fail-open: if anything fails, returns response unchanged.

    Args:
        response: The tool response dict to augment.
        trw_dir: Override the .trw directory (defaults to resolve_trw_dir()).
        available_learnings: Number of available learnings for nudge context.
        context: Optional NudgeContext for context-reactive messages (PRD-CORE-084).

    Returns:
        The response dict with 'ceremony_status' key added (or unchanged on error).
    """
    try:
        from trw_mcp.state.ceremony_nudge import (
            _highest_priority_pending_step,
            increment_nudge_count,
        )

        effective_dir = trw_dir if trw_dir is not None else resolve_trw_dir()
        state = read_ceremony_state(effective_dir)
        nudge = compute_nudge(state, available_learnings=available_learnings, context=context)
        response["ceremony_status"] = nudge
        # Increment nudge count for the pending step (tracks progressive urgency)
        pending = _highest_priority_pending_step(state)
        if pending:
            with contextlib.suppress(Exception):
                increment_nudge_count(effective_dir, pending)
        logger.debug(
            "append_ceremony_nudge",
            phase=state.phase,
            has_nudge=len(nudge) > 0,
        )
    except Exception:  # justified: fail-open — nudge injection must never raise or block
        logger.warning("append_ceremony_nudge_failed", exc_info=True)
    return response


# ── Phase-contextual tag map (PRD-CORE-049) ──────────────────────────────

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


# ── Session-start helpers ────────────────────────────────────────────────


def perform_session_recalls(
    trw_dir: Path,
    query: str,
    config: TRWConfig,
    reader: FileStateReader,
    run_dir: Path | None = None,
    run_status: RunStatusDict | None = None,
) -> tuple[list[dict[str, object]], list[AutoRecalledItemDict], SessionRecallExtrasDict]:
    """Execute focused + baseline recalls, return merged results.

    Returns:
        Tuple of (main_learnings, auto_recalled, extra_fields):
          - main_learnings: merged + deduped list from focused/baseline recall
          - auto_recalled: phase-contextual auto-recall results (empty if disabled)
          - extra_fields: dict with query_matched, total_available, etc.
    """
    from trw_mcp.state.memory_adapter import recall_learnings as adapter_recall
    from trw_mcp.state.memory_adapter import update_access_tracking as adapter_update_access

    is_focused = query.strip() not in ("", "*")
    extra: SessionRecallExtrasDict = {}
    learnings: list[dict[str, object]] = []

    # FR05 (PRD-CORE-084): Cap recall results for light ceremony mode.
    effective_max = (
        min(config.recall_max_results, LIGHT_MODE_RECALL_CAP)
        if config.ceremony_mode == "light"
        else config.recall_max_results
    )

    # Step 1: Core recall
    if is_focused:
        focused = adapter_recall(
            trw_dir,
            query=query,
            min_impact=0.3,
            max_results=effective_max,
            compact=True,
        )
        baseline = adapter_recall(
            trw_dir,
            query="*",
            min_impact=0.7,
            max_results=effective_max,
            compact=True,
        )
        extra["query"] = query
        extra["query_matched"] = len(focused)
        seen_ids: set[str] = set()
        for entry in focused + baseline:
            lid = str(entry.get("id", ""))
            if lid and lid not in seen_ids:
                seen_ids.add(lid)
                learnings.append(entry)
        learnings = learnings[:effective_max]
    else:
        learnings = adapter_recall(
            trw_dir,
            query="*",
            min_impact=0.7,
            max_results=effective_max,
            compact=True,
        )

    # Update access tracking
    matched_ids = [str(e.get("id", "")) for e in learnings if e.get("id")]
    adapter_update_access(trw_dir, matched_ids)
    log_recall_receipt(trw_dir, query if is_focused else "*", matched_ids)

    extra["total_available"] = len(learnings)

    # Phase-contextual auto-recall (PRD-CORE-049) — only when caller passes run context
    auto_recalled: list[AutoRecalledItemDict] = []

    return learnings, auto_recalled, extra


def _phase_contextual_recall(
    trw_dir: Path,
    query: str,
    config: TRWConfig,
    run_dir: Path | None,
    run_status: RunStatusDict | None,
) -> list[AutoRecalledItemDict]:
    """Execute phase-contextual auto-recall (PRD-CORE-049).

    Returns a list of auto-recalled learning summaries.
    """
    from trw_mcp.state.memory_adapter import recall_learnings as adapter_recall_ar

    is_focused = query.strip() not in ("", "*")
    query_tokens: list[str] = []
    if is_focused:
        query_tokens.extend(query.strip().split())

    phase_tags: list[str] | None = None
    if run_dir is not None and run_status is not None:
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
    ar_entries = adapter_recall_ar(
        trw_dir,
        query=ar_query,
        tags=phase_tags,
        min_impact=0.5,
        max_results=0,
        compact=False,
    )
    if not ar_entries:
        return []

    ranked = rank_by_utility(
        ar_entries,
        query_tokens,
        lambda_weight=config.recall_utility_lambda,
    )
    capped = ranked[: config.auto_recall_max_results]
    return [
        {
            "id": str(e.get("id", "")),
            "summary": str(e.get("summary", "")),
            "impact": float(str(e.get("impact", 0.0))),
        }
        for e in capped
    ]


def step_log_session_event(
    run_dir: Path | None,
    results: dict[str, object],
    query: str,
    is_focused: bool,
) -> None:
    """Log session_start event to events.jsonl (FR01, PRD-CORE-031).

    Writes to run-scoped events file if a run is active, otherwise falls
    back to a session-events file under the context directory.
    """
    from trw_mcp.models.config import get_config

    config = get_config()
    writer = FileStateWriter()
    events = FileEventLogger(writer)

    event_data: dict[str, object] = {
        "learnings_recalled": int(str(results.get("learnings_count", 0))),
        "run_detected": run_dir is not None,
        "query": query if is_focused else "*",
    }
    if run_dir is not None:
        events_path = run_dir / "meta" / "events.jsonl"
        if events_path.parent.exists():
            events.log_event(events_path, "session_start", event_data)
    else:
        trw_dir_path = resolve_trw_dir()
        context_path = trw_dir_path / config.context_dir
        writer.ensure_dir(context_path)
        fallback_path = context_path / "session-events.jsonl"
        events.log_event(fallback_path, "session_start", event_data)


def step_telemetry_startup(
    results: dict[str, object],
    run_dir: Path | None,
) -> None:
    """Queue SessionStartEvent for telemetry and start the telemetry pipeline.

    Fail-open: exceptions are logged but never propagated.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.telemetry.client import TelemetryClient
    from trw_mcp.telemetry.models import SessionStartEvent
    from trw_mcp.tools._deferred_delivery import _resolve_installation_id

    config = get_config()
    inst_id = _resolve_installation_id()
    tel_client = TelemetryClient.from_config()
    tel_client.record_event(SessionStartEvent(
        installation_id=inst_id,
        framework_version=config.framework_version,
        learnings_loaded=int(str(results.get("learnings_count", 0))),
        run_id=str(run_dir.name) if run_dir else None,
    ))
    tel_client.flush()
    # Start the unified telemetry pipeline (periodic background flush
    # replaces the old fire-and-forget BatchSender thread).
    try:
        from trw_mcp.telemetry.pipeline import TelemetryPipeline
        pipeline = TelemetryPipeline.get_instance()
        pipeline.start()
        pipeline.enqueue({
            "event_type": "session_start",
            "learnings_loaded": int(str(results.get("learnings_count", 0))),
            "run_id": str(run_dir.name) if run_dir else None,
        })
    except Exception:  # justified: fail-open, pipeline start must not block session start
        logger.warning("pipeline_start_failed", exc_info=True)


def step_increment_session_counter() -> None:
    """Increment sessions_tracked counter (FIX-050-FR06)."""
    from trw_mcp.state.analytics.counters import increment_session_start_counter
    increment_session_start_counter(resolve_trw_dir())


def step_sanitize_and_maintain(
    run_dir: Path | None,
) -> AutoMaintenanceDict:
    """Sanitize ceremony feedback, then run auto-maintenance.

    Wraps ``sanitize_ceremony_feedback`` + ``run_auto_maintenance`` in
    a single fail-open step.

    Returns:
        AutoMaintenanceDict with keys for each maintenance operation.
    """
    from trw_mcp.models.config import get_config

    config = get_config()

    # One-time sanitization of test-polluted ceremony feedback (FIX-050-FR07)
    try:
        from trw_mcp.state.ceremony_feedback import sanitize_ceremony_feedback
        sanitize_ceremony_feedback(resolve_trw_dir())
    except Exception:  # justified: fail-open, sanitization must not block session start
        logger.warning("ceremony_feedback_sanitize_failed", exc_info=True)

    return run_auto_maintenance(resolve_trw_dir(), config, run_dir)


def step_embed_health() -> dict[str, object]:
    """Check embeddings health status for agents (FR01, PRD-FIX-053).

    Returns:
        Dict with enabled, available, advisory, recent_failures keys.
        Falls back to a safe default dict if the check fails.
    """
    try:
        from trw_mcp.state.memory_adapter import check_embeddings_status
        embed_status = check_embeddings_status()
        return dict(embed_status)
    except Exception:  # justified: fail-open, embed health check must not block session start
        return {
            "enabled": False,
            "available": False,
            "advisory": "",
            "recent_failures": 0,
        }


def step_mark_session_started() -> None:
    """Mark session started in ceremony state tracker (PRD-CORE-074 FR04)."""
    from trw_mcp.state.ceremony_nudge import mark_session_started
    mark_session_started(resolve_trw_dir())


def step_ceremony_nudge(
    results: dict[str, object],
    learnings_count: int,
) -> None:
    """Inject ceremony nudge into response (PRD-CORE-074 FR01, PRD-CORE-084 FR02).

    Skipped for light ceremony mode (FR07, PRD-CORE-084).
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state.ceremony_nudge import NudgeContext, ToolName

    config = get_config()
    if config.ceremony_mode == "light":
        return

    ctx = NudgeContext(tool_name=ToolName.SESSION_START)
    append_ceremony_nudge(
        results,
        resolve_trw_dir(),
        available_learnings=learnings_count,
        context=ctx,
    )


def _check_version_sentinel(
    trw_dir: Path,
    maintenance: AutoMaintenanceDict,
) -> None:
    """Detect if the installer wrote a newer version since this process started.

    The installer writes ``.trw/installed-version.json`` after upgrading.
    If the on-disk version is newer than the running version, inject an
    ``update_advisory`` telling the user to run ``/mcp`` to reload.
    """
    import json

    sentinel = trw_dir / "installed-version.json"
    if not sentinel.is_file():
        return

    try:
        data = json.loads(sentinel.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    installed_version = str(data.get("version", ""))
    if not installed_version:
        return

    # Compare with running version
    try:
        from importlib.metadata import version as pkg_version

        running_version = pkg_version("trw-mcp")
    except Exception:  # justified: importlib.metadata may fail in edge cases
        return

    if installed_version != running_version and "update_advisory" not in maintenance:
        maintenance["update_advisory"] = (
            f"TRW v{installed_version} was installed but this MCP server is still "
            f"running v{running_version}. Run /mcp to reload."
        )


def run_auto_maintenance(
    trw_dir: Path,
    config: TRWConfig,
    run_dir: Path | None = None,
) -> AutoMaintenanceDict:
    """Run auto-upgrade check, stale run close, and embeddings backfill.

    Returns a dict with keys for each maintenance operation that produced results.
    All operations are fail-open — individual failures do not affect others.
    """
    maintenance: AutoMaintenanceDict = {}

    # Version sentinel check — detect if installer ran since this process started
    try:
        _check_version_sentinel(trw_dir, maintenance)
    except Exception:  # justified: fail-open, version sentinel check must not block session start
        logger.warning("maintenance_version_sentinel_failed", exc_info=True)

    # Auto-upgrade check (PRD-INFRA-014)
    try:
        from trw_mcp.state.auto_upgrade import check_for_update

        update_info = check_for_update()
        if update_info.get("available"):
            maintenance["update_advisory"] = str(update_info.get("advisory", ""))
            if config.auto_upgrade:
                from trw_mcp.state.auto_upgrade import perform_upgrade

                upgrade_result = perform_upgrade(update_info)
                if upgrade_result.get("applied"):
                    parts: list[str] = []
                    parts.append(
                        f"Auto-upgraded to v{upgrade_result.get('version', '?')}: {upgrade_result.get('details', '')}"
                    )
                    maintenance["auto_upgrade"] = upgrade_result
    except Exception:  # justified: fail-open, auto-upgrade must not block session start
        logger.warning("maintenance_auto_upgrade_failed", exc_info=True)

    # Auto-close stale runs
    try:
        if config.run_auto_close_enabled:
            from trw_mcp.state.analytics.report import auto_close_stale_runs

            close_result = auto_close_stale_runs()
            closed_count = int(str(close_result.get("count", 0)))
            if closed_count > 0:
                maintenance["stale_runs_closed"] = close_result
    except Exception:  # justified: fail-open, stale run cleanup must not block session start
        logger.warning("maintenance_stale_runs_close_failed", exc_info=True)

    # Embeddings status check + backfill
    try:
        from trw_mcp.state.memory_adapter import check_embeddings_status

        emb_status = check_embeddings_status()
        if emb_status.get("advisory"):
            maintenance["embeddings_advisory"] = str(emb_status["advisory"])
        elif emb_status.get("enabled") and emb_status.get("available"):
            from trw_mcp.state.memory_adapter import backfill_embeddings

            backfill = backfill_embeddings(resolve_trw_dir())
            if backfill.get("embedded", 0) > 0:
                maintenance["embeddings_backfill"] = backfill
    except Exception:  # justified: fail-open, embeddings check must not block session start
        logger.warning("maintenance_embeddings_check_failed", exc_info=True)

    return maintenance


# ── Deliver helpers ──────────────────────────────────────────────────────


def _read_complexity_class(run_path: Path, reader: FileStateReader) -> str:
    """Read the complexity_class from run.yaml, or return empty string."""
    run_yaml_path = run_path / "meta" / "run.yaml"
    if not run_yaml_path.exists():
        return ""
    try:
        run_data = reader.read_yaml(run_yaml_path)
        return str(run_data.get("complexity_class", ""))
    except Exception:  # justified: fail-open, complexity read must not block delivery
        logger.warning("complexity_class_read_failed", run_path=str(run_path), exc_info=True)
        return ""


def _check_review_gate(
    run_path: Path,
    reader: FileStateReader,
) -> tuple[str | None, str | None]:
    """Check review gate and return (warning, advisory) if found."""
    warning: str | None = None
    advisory: str | None = None

    review_path = run_path / "meta" / "review.yaml"
    if review_path.exists():
        try:
            review_data = reader.read_yaml(review_path)
            rv_verdict = str(review_data.get("verdict", ""))
            rv_critical = int(str(review_data.get("critical_count", 0)))
            if rv_verdict == "block" and rv_critical > 0:
                warning = (
                    f"Review has {rv_critical} critical findings. "
                    f"Delivery proceeding but review issues should be addressed."
                )
        except Exception:  # justified: fail-open, review gate check must not block delivery
            logger.warning("maintenance_review_gate_failed", exc_info=True)
    else:
        # Check complexity — STANDARD+ tasks MUST have review (Sprint 68 enforcement)
        complexity_class = _read_complexity_class(run_path, reader)
        if complexity_class in ("STANDARD", "COMPREHENSIVE"):
            warning = (
                f"No trw_review was run before delivery (complexity: {complexity_class}). "
                "Review is MANDATORY for STANDARD+ tasks — adversarial audit catches "
                "false completions that self-review misses. "
                "Run trw_review() or /trw-audit before delivering."
            )
        else:
            advisory = (
                "No trw_review was run before delivery. Consider running trw_review for quality assurance."
            )

    return warning, advisory


def _check_integration_review_gate(
    run_path: Path,
    reader: FileStateReader,
) -> tuple[str | None, str | None]:
    """Check integration review gate and return (block, warning) if found."""
    block: str | None = None
    warning: str | None = None

    integration_path = run_path / "meta" / "integration-review.yaml"
    if integration_path.exists():
        try:
            int_data = reader.read_yaml(integration_path)
            int_verdict = str(int_data.get("verdict", ""))
            if int_verdict == "block":
                raw_findings = int_data.get("findings", [])
                int_findings = raw_findings if isinstance(raw_findings, list) else []
                critical_list = [f for f in int_findings if isinstance(f, dict) and f.get("severity") == "critical"]
                block = (
                    f"Integration review verdict is 'block' with {len(critical_list)} critical finding(s). "
                    f"Delivery blocked. Fix critical integration issues before delivering."
                )
            elif int_verdict == "warn":
                warning = (
                    "Integration review has warnings. Review findings before merging."
                )
        except Exception:  # justified: fail-open, integration review check must not block delivery
            logger.warning("maintenance_integration_review_failed", exc_info=True)

    return block, warning


def _check_untracked_files(run_path: Path) -> str | None:
    """Check for untracked source/test files and return warning if found."""
    try:
        import subprocess

        git_result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],  # noqa: S607 — git is a well-known VCS tool; all args are static literals, no user input
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(run_path.parent.parent.parent),  # project root
        )
        if git_result.returncode == 0:
            untracked = [
                f
                for f in git_result.stdout.strip().splitlines()
                if f
                and (f.endswith((".py", ".ts", ".tsx")))
                and ("/src/" in f or "/tests/" in f or f.startswith(("src/", "tests/")))
            ]
            if untracked:
                return (
                    f"{len(untracked)} untracked source/test file(s) detected. "
                    f"These won't be included in commits: {', '.join(untracked[:5])}"
                    + (f" (+{len(untracked) - 5} more)" if len(untracked) > 5 else "")
                )
    except Exception:  # justified: fail-open, untracked file detection is advisory only
        logger.debug("untracked_file_check_failed", exc_info=True)

    return None


def _check_build_and_work_events(
    run_path: Path,
    reader: FileStateReader,
) -> tuple[str | None, str | None]:
    """Check build gate and work events, return (build_warning, premature_warning) if found."""
    build_warning: str | None = None
    premature_warning: str | None = None

    try:
        events_path = run_path / "meta" / "events.jsonl"
        if reader.exists(events_path):
            all_events = reader.read_jsonl(events_path)

            # Build gate (RC-003 + RC-006)
            def _build_passed(ev: dict[str, object]) -> bool:
                if str(ev.get("event", "")) != "build_check_complete":
                    return False
                data = ev.get("data")
                if isinstance(data, dict):
                    val = data.get("tests_passed")
                    return val is True or (isinstance(val, str) and val.lower() == "true")
                return False

            if not any(_build_passed(e) for e in all_events):
                build_warning = (
                    "No successful build check found before delivery. "
                    "Run trw_build_check() to verify tests pass and type-check is clean."
                )

            # Premature delivery guard
            ceremony_only = {
                "run_init",
                "checkpoint",
                "reflection_complete",
                "trw_reflect_complete",
                "trw_deliver_complete",
                "trw_session_start_complete",
            }
            work_events = [e for e in all_events if str(e.get("event", "")) not in ceremony_only]
            if len(work_events) == 0 and len(all_events) > 0:
                premature_warning = (
                    "Premature delivery — no work events found beyond ceremony. "
                    "This run has only init/checkpoint events. Proceeding anyway, "
                    "but consider whether work was actually completed."
                )
                logger.warning(
                    "premature_delivery",
                    total_events=len(all_events),
                    work_events=0,
                )
    except Exception:  # justified: fail-open, build gate check must not block delivery
        logger.warning("maintenance_build_gate_failed", exc_info=True)

    return build_warning, premature_warning


def check_delivery_gates(
    run_path: Path | None,
    reader: FileStateReader,
) -> DeliveryGatesDict:
    """Check review/build gates and premature delivery guard.

    Returns a dict with any warnings/advisories found:
      - review_warning: critical review findings present
      - review_advisory: no review was run
      - build_gate_warning: no successful build check found
      - warning: premature delivery (only ceremony events)
    """
    result: DeliveryGatesDict = {}

    if run_path is None:
        return result

    # Review gate (PRD-QUAL-022)
    review_warning, review_advisory = _check_review_gate(run_path, reader)
    if review_warning:
        result["review_warning"] = review_warning
    elif review_advisory:
        result["review_advisory"] = review_advisory

    # Integration review gate (PRD-INFRA-027-FR06)
    int_block, int_warning = _check_integration_review_gate(run_path, reader)
    if int_block:
        result["integration_review_block"] = int_block
    elif int_warning:
        result["integration_review_warning"] = int_warning

    # Untracked files
    untracked_warning = _check_untracked_files(run_path)
    if untracked_warning:
        result["untracked_warning"] = untracked_warning

    # Build gate and work events
    build_warning, premature_warning = _check_build_and_work_events(run_path, reader)
    if build_warning:
        result["build_gate_warning"] = build_warning
    if premature_warning:
        result["warning"] = premature_warning

    return result


def finalize_run(
    run_path: Path | None,
    trw_dir: Path,
    config: TRWConfig,
    reader: FileStateReader,
    writer: FileStateWriter,
    events: FileEventLogger,
) -> FinalizeRunResult:
    """Post-delivery finalization — placeholder for future run status updates.

    Currently a no-op pass-through. Checkpoint and reflect are handled inline
    in ceremony.py to preserve patch-point compatibility with existing tests.
    Future expansion: close run.yaml status, archive run, etc.
    """
    return {}


def copy_compliance_artifacts(
    run_path: Path | None,
    trw_dir: Path,
    config: TRWConfig,
    reader: FileStateReader,
    writer: FileStateWriter,
) -> ComplianceArtifactsDict:
    """Copy review artifacts to compliance retention directory (INFRA-027-FR05)."""
    result: ComplianceArtifactsDict = {}
    if run_path is None:
        return result

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    run_id = run_path.name

    compliance_dir = trw_dir / config.compliance_dir / "reviews" / str(now.year) / f"{now.month:02d}" / run_id

    artifacts = ["review.yaml", "review-all.yaml", "integration-review.yaml"]
    copied = []
    for artifact_name in artifacts:
        src = run_path / "meta" / artifact_name
        if reader.exists(src):
            try:
                data = reader.read_yaml(src)
                writer.ensure_dir(compliance_dir)
                writer.write_yaml(compliance_dir / artifact_name, data)
                copied.append(artifact_name)
            except Exception:  # justified: fail-open, compliance artifact copy is best-effort
                logger.warning("maintenance_compliance_copy_failed", exc_info=True)

    if copied:
        result["compliance_artifacts_copied"] = copied
        result["compliance_dir"] = str(compliance_dir)

    return result
