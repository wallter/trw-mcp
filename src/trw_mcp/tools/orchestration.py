"""TRW orchestration tools — init, status, checkpoint.

These 3 tools codify the FRAMEWORK.md execution flow:
RESEARCH -> PLAN -> IMPLEMENT -> VALIDATE -> REVIEW -> DELIVER
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config as get_config
from trw_mcp.models.run import (
    ComplexitySignals,
    Confidence,
    Phase,
    RunState,
    RunStatus,
)
from trw_mcp.models.typed_dicts import (
    CheckpointEventDataDict,
    CheckpointRecordDict,
    StatusReflectionDict,
    TrwStatusDict,
)
from trw_mcp.scoring import classify_complexity, get_phase_requirements
from trw_mcp.state._paths import pin_active_run, resolve_project_root, resolve_run_path
from trw_mcp.state.analytics.report import count_stale_runs
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)

_events = FileEventLogger(FileStateWriter())


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


def _compute_reflection_metrics(events: list[dict[str, object]]) -> StatusReflectionDict:
    """Count reflection completions and claude_md sync status from event stream."""
    reflection_count = sum(1 for e in events if e.get("event") == "reflection_complete")
    has_synced = any(e.get("event") == "claude_md_synced" for e in events)
    return StatusReflectionDict(
        count=reflection_count,
        claude_md_synced=has_synced,
    )


def _compute_last_activity_ts(
    reader: FileStateReader,
    meta_path: Path,
    events: list[dict[str, object]],
) -> tuple[str, float | None]:
    """Extract last activity timestamp and hours-since-activity."""
    checkpoints_path = meta_path / "checkpoints.jsonl"
    last_ts = ""
    hours_since = None

    if checkpoints_path.exists():
        checkpoints = reader.read_jsonl(checkpoints_path)
        if checkpoints:
            last_cp = checkpoints[-1]
            last_ts = str(last_cp.get("ts", ""))
            if last_ts:
                hours_since = _parse_timestamp_hours(last_ts)
                if hours_since is not None:
                    return last_ts, hours_since

    # Fall back to run_init event
    run_init_events = [e for e in events if str(e.get("event", "")) == "run_init"]
    if run_init_events:
        init_ts = str(run_init_events[0].get("ts", ""))
        if init_ts:
            last_ts = init_ts
            hours_since = _parse_timestamp_hours(init_ts)

    return last_ts, hours_since


def _parse_timestamp_hours(ts: str) -> float | None:
    """Parse ISO timestamp and return hours since then, or None on error."""
    try:
        last_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return round((now - last_dt).total_seconds() / 3600, 1)
    except (ValueError, TypeError):
        logger.debug("timestamp_parse_failed", timestamp=ts)
        return None


def _update_wave_status(
    reader: FileStateReader,
    writer: FileStateWriter,
    meta_path: Path,
    wave_id: str,
    ts: str,
    message: str,
) -> None:
    """Update wave status in run.yaml with checkpoint metadata."""
    try:
        run_yaml = meta_path / "run.yaml"
        if not run_yaml.exists():
            return
        run_data = reader.read_yaml(run_yaml)
        if not isinstance(run_data, dict):
            return
        wave_status = run_data.get("wave_status", {})
        if not isinstance(wave_status, dict):
            wave_status = {}
        wave_status[wave_id] = {
            "last_checkpoint": ts,
            "message": message,
        }
        run_data["wave_status"] = wave_status
        writer.write_yaml(run_yaml, run_data)
    except Exception:  # justified: fail-open, wave status metadata update must not block checkpoint
        logger.debug("wave_status_update_failed", wave_id=wave_id)


def _inject_ceremony_nudge(result: dict[str, str], _trw_dir: Path | None) -> None:
    """Inject ceremony nudge into checkpoint response."""
    if _trw_dir is None:
        return
    try:
        from trw_mcp.state.ceremony_nudge import NudgeContext, ToolName
        from trw_mcp.tools._ceremony_helpers import append_ceremony_nudge

        ctx = NudgeContext(tool_name=ToolName.CHECKPOINT)
        append_ceremony_nudge(cast("dict[str, object]", result), _trw_dir, context=ctx)
    except Exception:  # justified: fail-open, nudge injection must not block checkpoint
        logger.debug("checkpoint_nudge_injection_skipped", exc_info=True)  # justified: fail-open


def register_orchestration_tools(server: FastMCP) -> None:  # noqa: C901
    """Register orchestration tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool()
    @log_tool_call
    def trw_init(
        task_name: str,
        objective: str = "",
        config_overrides: dict[str, str] | None = None,
        prd_scope: list[str] | None = None,
        run_type: str = "implementation",
        task_root: str | None = None,
        wave_manifest: list[dict[str, object]] | None = None,
        complexity_signals: dict[str, object] | None = None,
    ) -> dict[str, str]:
        """Create your run directory so checkpoints and progress tracking work — required for structured tasks.

        Bootstraps .trw/ directories, run.yaml, and events.jsonl. Without a run,
        trw_checkpoint and trw_status have nowhere to write, and delivery cannot
        track what you accomplished. Use this for any task beyond a quick fix.

        Args:
            task_name: Name of the task — becomes the directory name and appears in status reports.
            objective: Optional objective description for the run.
            config_overrides: Optional config values to override defaults.
            prd_scope: Optional list of PRD IDs governing this run (e.g. ["PRD-CORE-009"]).
            run_type: Run type — "implementation" (default) or "research". Research runs skip PRD enforcement.
            task_root: Optional task directory root (default: config field or "docs").
            wave_manifest: Optional wave plan definitions. When provided, delegates to
                trw_wave_plan after run scaffolding for one-step initialization.
            complexity_signals: Optional complexity signals dict for adaptive ceremony depth.
                When provided, classifies task complexity into MINIMAL/STANDARD/COMPREHENSIVE tier.
        """
        # Input validation (PRD-QUAL-042-FR01)
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", task_name):
            raise StateError(
                f"Invalid task_name: must match [a-zA-Z0-9][a-zA-Z0-9_-]*, got: {task_name!r}",
            )

        config = get_config()
        reader = FileStateReader()
        writer = FileStateWriter()
        project_root = resolve_project_root()
        trw_dir = project_root / config.trw_dir

        # Generate run ID: timestamp + random suffix for uniqueness
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{timestamp}-{secrets.token_hex(4)}"

        trw_subdirs = [
            config.learnings_dir + "/" + config.entries_dir,
            config.reflections_dir,
            config.scripts_dir,
            config.patterns_dir,
            config.context_dir,
            config.frameworks_dir,
            config.templates_dir,
        ]
        for subdir in trw_subdirs:
            writer.ensure_dir(trw_dir / subdir)

        config_path = trw_dir / "config.yaml"
        if not reader.exists(config_path):
            config_data: dict[str, object] = {
                "framework_version": config.framework_version,
                "telemetry": config.telemetry,
                "parallelism_max": config.parallelism_max,
                "timebox_hours": config.timebox_hours,
            }
            if config_overrides:
                config_data.update(config_overrides)
            writer.write_yaml(config_path, config_data)

        # Write .trw/.gitignore from bundled template (DRY with bootstrap.py)
        gitignore_path = trw_dir / ".gitignore"
        if not reader.exists(gitignore_path):
            gitignore_content = _get_bundled_file("gitignore.txt")
            if gitignore_content:
                gitignore_path.parent.mkdir(parents=True, exist_ok=True)
                gitignore_path.write_text(gitignore_content, encoding="utf-8")

        # Deploy frameworks and templates to .trw/
        _deploy_frameworks(trw_dir)
        _deploy_templates(trw_dir)

        # Resolve task_root: explicit param > config field > default "docs"
        resolved_task_root = task_root if task_root is not None else config.task_root

        task_dir = project_root / resolved_task_root / task_name
        resolved_runs_root = project_root / config.runs_root
        run_root = resolved_runs_root / task_name / run_id
        run_subdirs = [
            "meta",
            "reports",
            "scratch/_orchestrator",
            "shards",
        ]
        for subdir in run_subdirs:
            writer.ensure_dir(run_root / subdir)

        initial_phase = Phase.RESEARCH

        variables: dict[str, str] = {
            "TASK": task_name,
            "TASK_DIR": str(task_dir),
            "RUN_ROOT": str(run_root),
            "TASK_ROOT": resolved_task_root,
            "RUNS_ROOT": config.runs_root,
        }

        # PRD-CORE-060: Classify complexity if signals provided
        parsed_signals = None
        complexity_class_val = None
        complexity_override_val = None
        phase_reqs_val = None
        if complexity_signals is not None:
            # Parse dict[str, object] via model_validate for type safety
            parsed_signals = ComplexitySignals.model_validate(complexity_signals)
            tier, _raw, override = classify_complexity(parsed_signals)
            complexity_class_val = tier
            complexity_override_val = override
            phase_reqs_val = get_phase_requirements(tier)

        run_state = RunState(
            run_id=run_id,
            task=task_name,
            framework=config.framework_version,
            status=RunStatus.ACTIVE,
            phase=initial_phase,
            confidence=Confidence.MEDIUM,
            objective=objective,
            variables=variables,
            prd_scope=prd_scope or [],
            run_type=run_type,
            complexity_class=complexity_class_val,
            complexity_signals=parsed_signals,
            complexity_override=complexity_override_val,
            phase_requirements=phase_reqs_val,
        )
        writer.write_yaml(
            run_root / "meta" / "run.yaml",
            model_to_dict(run_state),
        )

        # Pin this run as the active run for this process (RC-001 fix).
        # Prevents telemetry hijack when parallel instances share filesystem.
        pin_active_run(run_root)

        _events.log_event(
            run_root / "meta" / "events.jsonl",
            "run_init",
            {"task": task_name, "framework": config.framework_version},
        )

        # Framework version captured in run.yaml `framework` field.
        # Full snapshot removed — saves ~20 KB per run, reconstruct from git if needed.

        # Reset ceremony state for new run (PRD-CORE-074 FR04, P0-3)
        try:
            from trw_mcp.state.ceremony_nudge import reset_ceremony_state

            reset_ceremony_state(trw_dir)
        except Exception:  # justified: fail-open, ceremony state reset must not block run init
            logger.debug("init_ceremony_state_reset_skipped", exc_info=True)  # justified: fail-open

        logger.info(
            "run_init_ok",
            run_id=run_id,
            task=task_name,
            complexity_class=complexity_class_val.value if complexity_class_val else None,
        )
        logger.info(
            "run_phase_transition",
            run_id=run_id,
            from_phase="none",
            to_phase=initial_phase.value,
        )
        logger.info(
            "trw_init_complete",
            task=task_name,
            run_id=run_id,
            run_path=str(run_root),
        )

        result: dict[str, str] = {
            "run_id": run_id,
            "run_path": str(run_root),
            "trw_dir": str(trw_dir),
            "status": "initialized",
            "phase": initial_phase.value,
        }

        if complexity_class_val is not None:
            result["complexity_class"] = complexity_class_val.value

        # Inject ceremony nudge into response (PRD-CORE-084 FR02)
        try:
            from trw_mcp.state.ceremony_nudge import NudgeContext, ToolName
            from trw_mcp.tools._ceremony_helpers import append_ceremony_nudge

            ctx = NudgeContext(tool_name=ToolName.INIT)
            append_ceremony_nudge(cast("dict[str, object]", result), trw_dir, context=ctx)
        except Exception:  # justified: fail-open, nudge injection must not block init
            logger.debug("init_nudge_injection_skipped", exc_info=True)  # justified: fail-open

        return result

    @server.tool()
    @log_tool_call
    def trw_status(run_path: str | None = None) -> TrwStatusDict:
        """See your current phase, completed work, and what to do next — so you pick up where you left off instead of redoing work.

        Returns run state including phase, wave progress, shard status, confidence,
        and framework version. Essential when resuming after a context compaction
        or session restart.

        Args:
            run_path: Path to the run directory. Auto-detects if not provided.
        """
        reader = FileStateReader()
        resolved_path = resolve_run_path(run_path)
        meta_path = resolved_path / "meta"

        state_data = reader.read_yaml(meta_path / "run.yaml")

        wave_data: dict[str, object] = {}
        wave_manifest_path = resolved_path / "shards" / "wave_manifest.yaml"
        if not wave_manifest_path.exists():
            wave_manifest_path = meta_path / "wave_manifest.yaml"
        if wave_manifest_path.exists():
            wave_data = reader.read_yaml(wave_manifest_path)

        events_path = meta_path / "events.jsonl"
        events = reader.read_jsonl(events_path)

        result: TrwStatusDict = {
            "run_id": str(state_data.get("run_id", "unknown")),
            "task": str(state_data.get("task", "unknown")),
            "phase": str(state_data.get("phase", "unknown")),
            "status": str(state_data.get("status", "unknown")),
            "confidence": str(state_data.get("confidence", "unknown")),
            "framework": str(state_data.get("framework", "unknown")),
            "event_count": len(events),
            "reflection": _compute_reflection_metrics(events),
        }

        if wave_data:
            raw_waves = wave_data.get("waves", [])
            result["waves"] = raw_waves if isinstance(raw_waves, list) else []

            wave_progress = _compute_wave_progress(
                wave_data,
                resolved_path,
            )
            if wave_progress:
                result["wave_progress"] = wave_progress

        # Wave status from run.yaml checkpoints (PRD-INFRA-036-FR03)
        wave_status = state_data.get("wave_status")
        if isinstance(wave_status, dict) and wave_status:
            result["wave_status"] = wave_status

        # Reversion frequency metrics
        reversion_metrics = _compute_reversion_metrics(events)
        result["reversions"] = reversion_metrics

        # Last activity tracking (RC-002: detect stale/abandoned tracks)
        last_ts, hours_since = _compute_last_activity_ts(reader, meta_path, events)
        if last_ts:
            result["last_activity_ts"] = last_ts
        if hours_since is not None:
            result["hours_since_activity"] = hours_since

        # Stale framework version warning
        version_warning = _check_framework_version_staleness(
            str(state_data.get("framework", "")),
        )
        if version_warning:
            result["version_warning"] = version_warning

        # Stale run count (PRD-FIX-028: hour-level TTL reporting)
        try:
            stale = count_stale_runs()
            result["stale_count"] = stale
            if stale > 0:
                result["stale_runs_advisory"] = (
                    f"{stale} stale run(s) detected. Use trw_session_start to auto-close them."
                )
        except Exception:  # justified: fail-open, stale run count is advisory only
            result["stale_count_error"] = True
            logger.warning("stale_count_scan_failed", exc_info=True)

        logger.info(
            "status_ok",
            run_id=result["run_id"],
            phase=result["phase"],
            events=result["event_count"],
        )
        logger.debug(
            "status_detail",
            run_dir=str(resolved_path),
            wave_status=result.get("wave_status"),
        )
        logger.info("trw_status_read", run_id=result["run_id"])

        # Inject ceremony nudge into response (PRD-CORE-074 FR01, PRD-CORE-084 FR02)
        try:
            from trw_mcp.state._paths import resolve_trw_dir
            from trw_mcp.state.ceremony_nudge import NudgeContext, ToolName
            from trw_mcp.tools._ceremony_helpers import append_ceremony_nudge

            _trw_dir = resolve_trw_dir()
            ctx = NudgeContext(tool_name=ToolName.STATUS)
            result = cast(
                "TrwStatusDict", append_ceremony_nudge(cast("dict[str, object]", result), _trw_dir, context=ctx)
            )
        except Exception:  # justified: fail-open, nudge injection must not block status
            logger.debug("status_nudge_injection_skipped", exc_info=True)  # justified: fail-open

        return result

    @server.tool()
    @log_tool_call
    def trw_checkpoint(
        run_path: str | None = None,
        message: str = "",
        shard_id: str | None = None,
        wave_id: str | None = None,
    ) -> dict[str, str]:
        """Save your implementation progress — if context compacts, you resume here instead of re-implementing from scratch.

        Appends an atomic snapshot to checkpoints.jsonl with timestamp. The checkpoint
        message becomes your resumption point: the next session reads it to understand
        exactly where you left off and what to work on next.

        Args:
            run_path: Path to the run directory. Auto-detects if not provided.
            message: Describe what you accomplished and what comes next — this becomes your resume point after compaction.
            shard_id: Optional shard identifier for sub-agent attribution.
            wave_id: Optional wave identifier for wave-aware progress tracking (PRD-INFRA-036).
        """
        reader = FileStateReader()
        writer = FileStateWriter()
        resolved_path = resolve_run_path(run_path)
        meta_path = resolved_path / "meta"

        state_data = reader.read_yaml(meta_path / "run.yaml")
        ts = datetime.now(timezone.utc).isoformat()

        # Create checkpoint record
        checkpoint: CheckpointRecordDict = {
            "ts": ts,
            "message": message,
            "state": state_data,
        }
        if shard_id:
            checkpoint["shard_id"] = shard_id
        if wave_id:
            checkpoint["wave_id"] = wave_id

        checkpoints_path = meta_path / "checkpoints.jsonl"
        writer.append_jsonl(checkpoints_path, cast("dict[str, object]", checkpoint))

        event_data: CheckpointEventDataDict = {"message": message}
        if shard_id:
            event_data["shard_id"] = shard_id
        if wave_id:
            event_data["wave_id"] = wave_id
        _events.log_event(
            meta_path / "events.jsonl",
            "checkpoint",
            cast("dict[str, object]", event_data),
        )

        # Update wave status in run.yaml if wave_id provided (PRD-INFRA-036-FR02)
        if wave_id:
            _update_wave_status(reader, writer, meta_path, wave_id, ts, message)

        logger.info(
            "checkpoint_ok",
            run_id=str(state_data.get("run_id", "")),
            message=message[:80],
            wave_id=wave_id,
        )
        logger.info("trw_checkpoint_created", message=message)
        result: dict[str, str] = {
            "timestamp": ts,
            "status": "checkpoint_created",
            "message": message,
        }
        if wave_id:
            result["wave_id"] = wave_id

        # Resolve trw_dir once for ceremony state + nudge injection
        try:
            from trw_mcp.state._paths import resolve_trw_dir

            _trw_dir = resolve_trw_dir()
        except Exception:  # justified: fail-open, ceremony features must not block checkpoint
            _trw_dir = None

        # Mark checkpoint in ceremony state tracker (PRD-CORE-074 FR04)
        if _trw_dir is not None:
            try:
                from trw_mcp.state.ceremony_nudge import mark_checkpoint as _mark_cp

                _mark_cp(_trw_dir)
            except Exception:  # justified: fail-open, ceremony state update must not block checkpoint
                logger.debug("checkpoint_ceremony_state_update_skipped", exc_info=True)  # justified: fail-open

        # Inject ceremony nudge into response (PRD-CORE-074 FR01, PRD-CORE-084 FR02)
        _inject_ceremony_nudge(result, _trw_dir)

        return result


# --- Private helpers (extracted to _orchestration_helpers.py) ---
# Re-exported here for backward compatibility with existing callers/tests.

from trw_mcp.tools._orchestration_helpers import (  # noqa: E402
    _check_framework_version_staleness as _check_framework_version_staleness,
    _compute_reversion_metrics as _compute_reversion_metrics,
    _compute_wave_progress as _compute_wave_progress,
    _deploy_frameworks as _deploy_frameworks,
    _deploy_templates as _deploy_templates,
    _get_bundled_file as _get_bundled_file,
    _get_package_version as _get_package_version,
)
