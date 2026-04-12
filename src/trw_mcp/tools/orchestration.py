"""TRW orchestration tools — init, status, checkpoint."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
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
    TrwStatusDict,
)
from trw_mcp.scoring import classify_complexity, get_phase_requirements
from trw_mcp.state._paths import pin_active_run, resolve_project_root, resolve_run_path
from trw_mcp.state.analytics._stale_runs import count_stale_runs
from trw_mcp.state.artifact_scanner import scan_artifacts
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)
from trw_mcp.tools._orchestration_helpers import (
    _deploy_frameworks,
    _deploy_templates,
    _get_bundled_file,
)
from trw_mcp.tools._orchestration_lifecycle import (
    _apply_ceremony_status,
    _compute_last_activity_ts,
    _compute_reflection_metrics,
    _update_wave_status,
)
from trw_mcp.tools._orchestration_phase import (
    _check_framework_version_staleness,
    _compute_reversion_metrics,
    _compute_wave_progress,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)

_events = FileEventLogger(FileStateWriter())


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


def register_orchestration_tools(server: FastMCP) -> None:  # noqa: C901
    """Register orchestration tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool(output_schema=None)
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
        artifacts: list[str] | None = None,
        complexity_hint: Literal["EASY", "STANDARD", "HARD"] | None = None,
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
            artifacts: Optional list of artifact file paths (PRDs, exec plans, sprint docs)
                to scan for knowledge requirements (PRD-CORE-106). Extracted domains and
                learning IDs are stored in run metadata for recall boosting.
            complexity_hint: Optional hint to force a ceremony tier (EASY=MINIMAL, etc).
        """
        from trw_mcp.models.run import ComplexityClass

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

        if complexity_hint is not None:
            # PRD-CORE-134: Map complexity_hint to class
            hint_map = {
                "EASY": ComplexityClass.MINIMAL,
                "STANDARD": ComplexityClass.STANDARD,
                "HARD": ComplexityClass.COMPREHENSIVE,
            }
            complexity_class_val = hint_map.get(complexity_hint)
            if complexity_class_val:
                phase_reqs_val = get_phase_requirements(complexity_class_val)

        if complexity_class_val is None and complexity_signals is not None:
            # Parse dict[str, object] via model_validate for type safety
            parsed_signals = ComplexitySignals.model_validate(complexity_signals)
            tier, _raw, override = classify_complexity(parsed_signals)
            complexity_class_val = tier
            complexity_override_val = override
            phase_reqs_val = get_phase_requirements(tier)

        # PRD-CORE-106: Resolve artifact paths for storage
        resolved_artifacts = [str(p) for p in (artifacts or [])]

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
            artifacts=resolved_artifacts,
        )
        writer.write_yaml(
            run_root / "meta" / "run.yaml",
            model_to_dict(run_state),
        )

        # PRD-CORE-106: Scan artifacts for knowledge requirements
        if resolved_artifacts:
            try:
                kr = scan_artifacts(resolved_artifacts)
                # Write scanned knowledge requirements alongside run.yaml
                kr_data: dict[str, object] = {
                    "learning_ids": sorted(kr.learning_ids),
                    "domains": sorted(kr.domains),
                    "checks": kr.checks,
                    "research_notes": kr.research_notes,
                    "prd_references": sorted(kr.prd_references),
                    "phase_requirements": kr.phase_requirements,
                }
                writer.write_yaml(
                    run_root / "meta" / "knowledge_requirements.yaml",
                    kr_data,
                )
                logger.info(
                    "artifact_scan_complete",
                    run_id=run_id,
                    artifact_count=len(resolved_artifacts),
                    domains=len(kr.domains),
                    learning_ids=len(kr.learning_ids),
                )
            except Exception:  # justified: fail-open, artifact scanning must not block run init
                logger.warning("artifact_scan_failed", run_id=run_id, exc_info=True)

        # Pin this run as the active run for this process (RC-001 fix).
        # Prevents telemetry hijack when parallel instances share filesystem.
        pin_active_run(run_root)

        events_jsonl_path = run_root / "meta" / "events.jsonl"
        _events.log_event(
            events_jsonl_path,
            "run_init",
            {"task": task_name, "framework": config.framework_version},
        )

        # PRD-QUAL-050-FR03: always record a session_start boundary here;
        # a later explicit trw_session_start supersedes it.
        try:
            _events.log_event(
                events_jsonl_path,
                "session_start",
                {"source": "trw_init", "run_detected": True, "query": "*"},
            )
        except Exception:  # justified: fail-open, session boundary must not block run init
            logger.debug("init_session_start_event_skipped", exc_info=True)

        # Framework version is captured in run.yaml; full snapshot removed to save ~20 KB per run.

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

        _apply_ceremony_status(
            cast("dict[str, object]", result),
            tool_name="INIT",
            debug_event="init_nudge_injection_skipped",
            trw_dir=trw_dir,
        )

        return result

    @server.tool(output_schema=None)
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

        _apply_ceremony_status(
            cast("dict[str, object]", result),
            tool_name="STATUS",
            debug_event="status_nudge_injection_skipped",
        )

        return result

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_checkpoint(
        run_path: str | None = None,
        message: str = "",
        shard_id: str | None = None,
        wave_id: str | None = None,
    ) -> dict[str, str]:
        """Save your implementation progress — if context compacts, you resume here instead of re-implementing from scratch.

        When to call: after each working milestone (file saved, test passing, feature wired).
        If your context compacts, you resume from your last checkpoint instead of starting over.

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
        result: dict[str, str] = {
            "timestamp": ts,
            "status": "checkpoint_created",
            "message": message,
        }
        if wave_id:
            result["wave_id"] = wave_id

        _apply_ceremony_status(
            cast("dict[str, object]", result),
            tool_name="CHECKPOINT",
            debug_event="checkpoint_nudge_injection_skipped",
            mark_checkpoint_first=True,
        )

        return result
