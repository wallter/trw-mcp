"""TRW orchestration tools — init, status, checkpoint."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Literal, cast

import structlog
from fastmcp import Context, FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config as get_config
from trw_mcp.models.run import (
    Confidence,
    Phase,
    RunState,
    RunStatus,
)
from trw_mcp.models.typed_dicts import TrwStatusDict
from trw_mcp.state._call_context import build_call_context as _build_call_context
from trw_mcp.state._helpers import read_jsonl_resilient
from trw_mcp.state._paths import (
    pin_active_run,
    resolve_project_root,
    resolve_run_path,
)
from trw_mcp.state.analytics._stale_runs import count_stale_runs
from trw_mcp.state.persistence import FileStateReader, FileStateWriter, model_to_dict
from trw_mcp.tools import _orchestration_scaling as _scaling
from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint
from trw_mcp.tools._orchestration_gate_scan import (
    apply_deliver_gate_status as _apply_deliver_gate_status,
)
from trw_mcp.tools._orchestration_helpers import (
    _deploy_frameworks,
    _deploy_templates,
    _get_bundled_file,
    _log_init_events,
    _scan_init_artifacts,
)
from trw_mcp.tools._orchestration_lifecycle import (
    _apply_ceremony_status,
    _compute_last_activity_ts,
    _compute_reflection_metrics,
)
from trw_mcp.tools._orchestration_lifecycle import (
    _phase_duration_summary as _phase_duration_summary,
)
from trw_mcp.tools._orchestration_phase import (
    _check_framework_version_staleness,
    _compute_reversion_metrics,
    _compute_wave_progress,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)

# PRD-QUAL-042-FR01: cap trw_init ``task_name`` (a filesystem path component)
# below NAME_MAX (255) with headroom for the appended run_id suffix.
_MAX_TASK_NAME_CHARS = 128


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


def register_orchestration_tools(server: FastMCP) -> None:
    """Register orchestration tools on the MCP server."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_init(
        ctx: Context | None = None,
        task_name: str = "",
        objective: str = "",
        config_overrides: dict[str, str] | None = None,
        prd_scope: list[str] | None = None,
        run_type: str = "implementation",
        task_type: str | None = None,
        task_root: str | None = None,
        wave_manifest: list[dict[str, object]] | None = None,
        complexity_signals: dict[str, object] | None = None,
        artifacts: list[str] | None = None,
        complexity_hint: Literal["EASY", "STANDARD", "HARD"] | None = None,
        protected: bool = False,
        planning_mode: str | None = None,
    ) -> dict[str, str]:
        """Create a run directory and register it as the active run.

        Use when:
        - Starting a new task, sprint, or investigation that needs persistent TRW state.
        - You need run metadata, framework assets, and active-run pinning before work begins.

        Bootstraps state, run metadata, events, framework assets, optional
        wave/artifact metadata, and a trace/profile-aware task_profile.

        Input: task_name plus optional objective, config_overrides, task_root,
        wave_manifest, complexity signals, artifacts, and protection flag.

        Output: dict with run_id, run_path, task_dir, phase, and status fields.
        """

        # Input validation (PRD-QUAL-042-FR01). ``task_name`` defaults to "" only
        # so FastMCP can inject ``ctx`` first (PRD-CORE-141 FR03); empty is rejected.
        if not task_name or not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", task_name):
            raise StateError(
                f"Invalid task_name: must match [a-zA-Z0-9][a-zA-Z0-9_-]*, got: {task_name!r}",
            )
        # Cap length: an over-long name (a path component) can exceed NAME_MAX
        # and fail mkdir mid-init. 128 leaves headroom for the run_id suffix.
        if len(task_name) > _MAX_TASK_NAME_CHARS:
            raise StateError(f"Invalid task_name: exceeds {_MAX_TASK_NAME_CHARS} chars (got {len(task_name)})")

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

        # PRD-FIX-073-FR02: Delegate directory scaffolding to shared service layer
        # (DRY with `trw-mcp local init` CLI subcommand)
        from trw_mcp.services.orchestration_service import (
            scaffold_run_directory as _scaffold_run,
        )

        _scaffold_run(task_name, runs_root=resolved_runs_root, trw_dir=trw_dir, run_id=run_id)

        initial_phase = Phase.RESEARCH

        variables: dict[str, str] = {
            "TASK": task_name,
            "TASK_DIR": str(task_dir),
            "RUN_ROOT": str(run_root),
            "TASK_ROOT": resolved_task_root,
            "RUNS_ROOT": config.runs_root,
        }

        # PRD-CORE-060/134 + PRD-CORE-184: complexity + task-type + task_profile
        # resolution (extracted to the scaling sibling to keep this module under
        # the 350 eLOC gate when SCALE-001 FR13 wiring landed).
        prof = _scaling.resolve_init_profile(
            config,
            task_name=task_name,
            run_type=run_type,
            prd_scope=prd_scope,
            task_type=task_type,
            complexity_hint=complexity_hint,
            complexity_signals=complexity_signals,
        )
        complexity_class_val = prof.complexity_class
        resolved_task_type = prof.task_type
        task_profile = prof.task_profile
        detection = prof.detection

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
            task_type=resolved_task_type,
            recall_policy=task_profile.recall_policy,
            complexity_class=complexity_class_val,
            complexity_signals=prof.parsed_signals,
            complexity_override=prof.complexity_override,
            phase_requirements=prof.phase_requirements,
            task_profile=task_profile,
            artifacts=resolved_artifacts,
            protected=protected,
        )
        writer.write_yaml(
            run_root / "meta" / "run.yaml",
            model_to_dict(run_state),
        )

        # PRD-CORE-106: Scan artifacts for knowledge requirements
        if resolved_artifacts:
            _scan_init_artifacts(writer, run_root, resolved_artifacts, run_id)

        # Pin this run as the active run for this process (RC-001 fix).
        # Prevents telemetry hijack when parallel instances share filesystem.
        # PRD-CORE-141 FR03: thread ctx so pin is keyed to the caller's
        # ctx-resolved session (not the process UUID) on shared-HTTP deployments.
        pin_active_run(run_root, context=_build_call_context(ctx))

        _log_init_events(
            run_root / "meta" / "events.jsonl",
            task_name=task_name,
            framework_version=config.framework_version,
            task_type=resolved_task_type,
            detection_method=detection.detection_method,
            rationale=detection.rationale,
            recall_policy=task_profile.recall_policy,
        )

        logger.info(
            "trw_init_complete",
            run_id=run_id,
            task=task_name,
            run_path=str(run_root),
            complexity_class=complexity_class_val.value if complexity_class_val else None,
        )
        logger.info(
            "run_phase_transition",
            run_id=run_id,
            from_phase="none",
            to_phase=initial_phase.value,
        )

        result: dict[str, str] = {
            "run_id": run_id,
            "run_path": str(run_root),
            "trw_dir": str(trw_dir),
            "status": "initialized",
            "phase": initial_phase.value,
            "task_type": resolved_task_type,
        }

        if complexity_class_val is not None:
            result["complexity_class"] = complexity_class_val.value
        result["task_profile_hash"] = task_profile.profile_hash

        # PRD-SCALE-001 FR13/FR03: run the Cognitive Scaling Scout (honoring a
        # --planning-mode override) and write meta/session_profile.yaml — the H2
        # profile resolver reads it as the session-layer overlay on the next
        # trw_session_start, making ceremony dynamic per task. Surfaces the mode
        # + tier onto ``result``. Fail-open.
        _scaling.run_scout_for_init(
            config,
            task_name=task_name,
            objective=objective,
            prd_scope=prd_scope,
            run_root=run_root,
            project_root=project_root,
            trw_dir=trw_dir,
            planning_mode=planning_mode,
            result=result,
        )

        _apply_ceremony_status(
            cast("dict[str, object]", result),
            tool_name="INIT",
            debug_event="init_nudge_injection_skipped",
            trw_dir=trw_dir,
        )

        return result

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_status(
        ctx: Context | None = None,
        run_path: str | None = None,
    ) -> TrwStatusDict:
        """Report the active run's phase, wave progress, shard state, and last activity.

        Use when:
        - Resuming after context compaction or a session restart.
        - Deciding whether to checkpoint, advance phase, or re-delegate a wave.

        Input:
        - run_path: path to the run directory. Auto-detects from pin if None.

        Output: TrwStatusDict with fields
        {run_id, task, phase, status, confidence, framework, event_count,
         reflection, waves?, wave_progress?, wave_status?, reversions,
         last_activity_ts?, hours_since_activity?, stale_count}.
        """
        reader = FileStateReader()
        # PRD-CORE-141 FR03/FR05: ctx-aware resolve_run_path suppresses the
        # mtime scan fallback when no pin exists for this session.
        resolved_path = resolve_run_path(run_path, context=_build_call_context(ctx))
        meta_path = resolved_path / "meta"

        state_data = reader.read_yaml(meta_path / "run.yaml")

        wave_data: dict[str, object] = {}
        wave_manifest_path = resolved_path / "shards" / "wave_manifest.yaml"
        if not wave_manifest_path.exists():
            wave_manifest_path = meta_path / "wave_manifest.yaml"
        if wave_manifest_path.exists():
            wave_data = reader.read_yaml(wave_manifest_path)

        # events.jsonl feeds only advisory analytics here (event_count,
        # reflection, phase_durations, reversions); authoritative state is the
        # run.yaml read above. A torn concurrent append must drop that one line,
        # not StateError-abort status (invoked on every resume) — so use the
        # resilient reader, matching the _do_reflect / collect_reflection_inputs
        # seams over this same log, not strict FileStateReader.read_jsonl.
        events_path = meta_path / "events.jsonl"
        events = read_jsonl_resilient(events_path)

        result: TrwStatusDict = {
            "run_id": str(state_data.get("run_id", "unknown")),
            "task": str(state_data.get("task", "unknown")),
            "phase": str(state_data.get("phase", "unknown")),
            "status": str(state_data.get("status", "unknown")),
            "confidence": str(state_data.get("confidence", "unknown")),
            "framework": str(state_data.get("framework", "unknown")),
            # PRD-CORE-184-FR05: surface task_type in the run summary block.
            "task_type": str(state_data.get("task_type", "unknown")),
            "event_count": len(events),
            "reflection": _compute_reflection_metrics(events),
        }

        # PRD-CORE-184-FR04: surface effective per-task-type nudge pool weights
        # so operators (and eval stratification) can observe the active policy.
        task_profile_data = state_data.get("task_profile")
        if isinstance(task_profile_data, dict):
            weights = task_profile_data.get("nudge_pool_weights")
            if isinstance(weights, (list, tuple)) and len(weights) == 4:
                result["nudge_pool_weights"] = {
                    "workflow": int(weights[0]),
                    "learnings": int(weights[1]),
                    "ceremony": int(weights[2]),
                    "context": int(weights[3]),
                }
            recall_policy = task_profile_data.get("recall_policy")
            if recall_policy:
                result["recall_policy"] = str(recall_policy)
        result["phase_durations"] = _phase_duration_summary(events, result["phase"])

        if wave_data:
            raw_waves = wave_data.get("waves", [])
            result["waves"] = raw_waves if isinstance(raw_waves, list) else []

            wave_progress = _compute_wave_progress(
                wave_data,
                resolved_path,
            )
            if wave_progress:
                result["wave_progress"] = wave_progress

        wave_status = state_data.get("wave_status")
        if isinstance(wave_status, dict) and wave_status:
            result["wave_status"] = wave_status

        reversion_metrics = _compute_reversion_metrics(events)
        result["reversions"] = reversion_metrics

        # PRD-QUAL-105: surface deliver-gate readiness at status-check time so an
        # agent can answer "can I deliver now?" without a deliver-then-fail-then-
        # retry cycle. Reuses the already-read ``events`` list (FR01 build gate)
        # plus ceremony_state.json (FR02 review gate). Fail-open per FR04 inside
        # the helper — the three fields are simply omitted on any scan error.
        _apply_deliver_gate_status(cast("dict[str, object]", result), events, resolved_path)

        last_ts, hours_since = _compute_last_activity_ts(reader, meta_path, events)
        if last_ts:
            result["last_activity_ts"] = last_ts
        if hours_since is not None:
            result["hours_since_activity"] = hours_since

        version_warning = _check_framework_version_staleness(
            str(state_data.get("framework", "")),
        )
        if version_warning:
            result["version_warning"] = version_warning

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
        ctx: Context | None = None,
        run_path: str | None = None,
        message: str = "",
        shard_id: str | None = None,
        wave_id: str | None = None,
    ) -> dict[str, str]:
        """Append a progress snapshot so work survives context compaction.

        Use when:
        - You complete a milestone or before context compaction/interruption.
        - After each meaningful work batch so another agent can resume safely.

        Input: optional run_path plus required message. Optional shard_id and
        wave_id annotate delegated or wave-aware progress.

        Output: dict with status, run_path, checkpoint path, and message metadata.
        """

        result = execute_checkpoint(
            run_path,
            message,
            shard_id,
            wave_id,
            context=_build_call_context(ctx),
        )

        _apply_ceremony_status(
            cast("dict[str, object]", result),
            tool_name="CHECKPOINT",
            debug_event="checkpoint_nudge_injection_skipped",
            mark_checkpoint_first=True,
        )

        return result
