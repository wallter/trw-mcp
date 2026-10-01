"""TRW orchestration tools — init, status, checkpoint."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Literal, cast

import structlog
from fastmcp import Context, FastMCP

from trw_mcp._checkout_write import write_checkout_file
from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config as get_config
from trw_mcp.models.run import Confidence, Phase, RunState, RunStatus
from trw_mcp.models.typed_dicts import TrwStatusDict
from trw_mcp.state._call_context import build_call_context as _build_call_context
from trw_mcp.state._helpers import read_jsonl_resilient
from trw_mcp.state._paths import pin_active_run, resolve_project_root, resolve_run_path

# Re-exported patch seams: _orchestration_status_assembly resolves these via
# this facade so tests patching trw_mcp.tools.orchestration.* keep working.
from trw_mcp.state.analytics._stale_runs import (
    count_stale_runs as count_stale_runs,
)
from trw_mcp.state.analytics._stale_runs import (
    stale_advisory_first_time as stale_advisory_first_time,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter, model_to_dict
from trw_mcp.tools import _orchestration_init_profile as _init_profile
from trw_mcp.tools._ceremony_heartbeat import compute_heartbeat_result
from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint
from trw_mcp.tools._orchestration_helpers import (
    _deploy_frameworks,
    _deploy_templates,
    _get_bundled_file,
    _log_init_events,
    _scan_init_artifacts,
)
from trw_mcp.tools._orchestration_init_advanced import parse_init_advanced
from trw_mcp.tools._orchestration_lifecycle import (
    _apply_ceremony_status,
)

# Re-exported patch/import seams for tests and _orchestration_status_assembly:
# helpers relocated during the status-assembly extraction remain importable here.
from trw_mcp.tools._orchestration_lifecycle import (
    _compute_last_activity_ts as _compute_last_activity_ts,
)
from trw_mcp.tools._orchestration_lifecycle import (
    _compute_reflection_metrics as _compute_reflection_metrics,
)
from trw_mcp.tools._orchestration_lifecycle import (
    _phase_duration_summary as _phase_duration_summary,
)
from trw_mcp.tools._orchestration_phase import (
    _check_framework_version_staleness as _check_framework_version_staleness,
)
from trw_mcp.tools._orchestration_phase import (
    _compute_reversion_metrics as _compute_reversion_metrics,
)
from trw_mcp.tools._orchestration_status_assembly import assemble_status_result, field_scope_label
from trw_mcp.tools._profile_cli import surface_detail
from trw_mcp.tools._status_feedback import status_feedback
from trw_mcp.tools._task_profile_observability import apply_task_profile_observability
from trw_mcp.tools.checkpoint import execute_pre_compact_checkpoint
from trw_mcp.tools.delivery_ops import delivery_status

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
    def trw_init(
        ctx: Context | None = None,
        task_name: str = "",
        objective: str = "",
        prd_scope: list[str] | None = None,
        run_type: str = "implementation",
        task_type: str | None = None,
        complexity_hint: Literal["EASY", "STANDARD", "HARD"] | None = None,
        advanced: dict[str, object] | str | None = None,
    ) -> dict[str, str]:
        """Use when starting a task, sprint, or investigation needing persistent
        TRW state (run metadata, events, framework assets, active-run pinning).

        Input: task_name (required; [A-Za-z0-9][A-Za-z0-9_-]*, max 128 chars),
        plus optional objective and prd_scope for context; complexity_hint:
        EASY|STANDARD|HARD.

        Output: run_id, run_path, trw_dir, phase, status, task_type, complexity class.

        Args:
            advanced: rare settings, as an object (or JSON string). Keys:
                artifacts, complexity_signals, config_overrides, formation,
                join_formation, protected, target_utc,
                task_root. Unknown keys are rejected.
        """

        # ``advanced`` collapses seven rare flat parameters into one schema entry
        # (see _orchestration_init_advanced for the byte-identical-keys and
        # refuse-don't-ignore rules). Parsed FIRST so a malformed bag fails before
        # any directory is created.
        adv = parse_init_advanced(advanced)
        config_overrides = adv.config_overrides
        task_root = adv.task_root
        complexity_signals = adv.complexity_signals
        protected = adv.protected

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
                write_checkout_file(trw_dir, gitignore_path, gitignore_content)

        # Deploy frameworks and templates to .trw/
        deploy_result = _deploy_frameworks(trw_dir)
        _deploy_templates(trw_dir)

        # Resolve task_root: explicit param > config field > default "docs"
        resolved_task_root = task_root if task_root is not None else config.task_root

        task_dir = project_root / resolved_task_root / task_name
        # PRD-FIX-141-FR07: run.yaml advertises TASK_DIR as the deliverable
        # destination (FRAMEWORK.md names it a write-scope boundary), so the
        # directory has to exist. It used to be computed, written into the run
        # record, and never created, leaving every agent to mkdir it by hand.
        task_dir.mkdir(parents=True, exist_ok=True)
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
        prof = _init_profile.resolve_init_profile(
            config,
            task_name=task_name,
            objective=objective,
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

        resolved_artifacts = list(adv.artifacts)
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
            target_utc=adv.target_utc,
        )
        from trw_mcp.state._run_yaml_update import complete_run_yaml

        # N1: the scaffold above created run.yaml exclusively; this replaces only that record.
        complete_run_yaml(run_root, model_to_dict(run_state))

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
            target_utc=adv.target_utc,
            prd_scope=prd_scope or [],
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
            # PRD-CORE-246-FR04: the classification's PROVENANCE, not just its
            # value. Without these a caller cannot tell a DETECTED type from a
            # DEFAULTED one, which is the "unset value indistinguishable from a
            # checked positive result" shape this PRD exists to close.
            "task_type_detection_method": detection.detection_method,
            "task_type_rationale": detection.rationale,
        }

        if deploy_result.get("status") == "skipped_stale_package":
            result["framework_deploy"] = deploy_result["status"]
            result["framework_nudge"] = deploy_result["nudge"]

        if complexity_class_val is not None:
            result["complexity_class"] = complexity_class_val.value
        result["task_profile_hash"] = task_profile.profile_hash
        apply_task_profile_observability(cast("dict[str, object]", result), task_profile.model_dump())

        # PRD-CORE-265-FR03/FR04: create a formation, or join one, from the same
        # validated bag. Delegated to the sibling so this facade stays small; it
        # runs AFTER run.yaml is written because join stamps the two ids onto it.
        if adv.formation is not None or adv.join_formation is not None:
            from trw_mcp.tools._orchestration_formation import apply_formation_init

            apply_formation_init(adv.formation, adv.join_formation, run_root, ctx, result)

        # PRD-CORE-184 FR01/FR02: surface an UP-FRONT REVIEW-mandatory signal
        # when the resolved run requires a REVIEW phase (STANDARD/COMPREHENSIVE).
        # The SessionStart hook may have advertised "Skip: REVIEW" before the
        # run complexity was known; this reconciles that at the trw_init boundary
        # by stating the run complexity overrides the session ceremony tier. This
        # is advisory only and does NOT alter the CORE-192 deliver gate (NFR05).
        _init_profile.apply_review_mandate_advisory(result, phase_requirements=prof.phase_requirements, config=config)

        _apply_ceremony_status(
            cast("dict[str, object]", result),
            tool_name="INIT",
            debug_event="init_nudge_injection_skipped",
            trw_dir=trw_dir,
        )

        return result

    @server.tool(output_schema=None)
    def trw_status(
        ctx: Context | None = None,
        run_path: str | None = None,
        delivery: str = "",
        feedback: dict[str, object] | str = "",
        detail: str = "",
    ) -> TrwStatusDict | dict[str, object]:
        """delivery=<id>: a trw_deliver status. feedback={category, subject,
        message}: post a memo (never a false success). detail="surface":
        the resolved profile and tool surface.

        Use when resuming or after a trw_deliver timeout. run_path is
        auto-detected. Output: phase, status, confidence, staleness.
        """
        if detail and detail != "surface":
            # INC-120 (b): an unknown detail used to fall through to the plain run status, so a typo looked fine.
            from fastmcp.exceptions import ToolError

            from trw_mcp._refusal_echo import key_name

            raise ToolError(f"unknown detail {key_name(detail)!r}; the only accepted value is 'surface'")
        if feedback:
            from trw_mcp.tools._learn_arg_bags import _coerce_json

            mapping, error = _coerce_json(feedback)
            if mapping is None:
                from fastmcp.exceptions import ToolError

                raise ToolError(f"feedback {error}")
            return status_feedback(mapping)
        if delivery:
            # PRD-CORE-300-FR03: the delivery owner status locator; needs no run, read-only.
            return delivery_status(delivery)
        if detail == "surface":
            # PRD-CORE-300 S11b: replaces the profile-explain tool; works with no pinned run.
            return surface_detail(context=_build_call_context(ctx))
        reader = FileStateReader()
        # PRD-CORE-141 FR03/FR05: ctx-aware resolution skips the mtime scan when unpinned.
        resolved_path = resolve_run_path(run_path, context=_build_call_context(ctx))
        meta_path = resolved_path / "meta"

        state_data = reader.read_yaml(meta_path / "run.yaml")

        # events.jsonl feeds only advisory analytics; run.yaml above is authoritative.
        # A torn concurrent append must drop one line, not abort status on every
        # resume, so use the resilient reader (as _do_reflect does), not read_jsonl.
        events_path = meta_path / "events.jsonl"
        events = read_jsonl_resilient(events_path)

        result: TrwStatusDict = assemble_status_result(
            state_data,
            events,
            resolved_path,
            reader,
            meta_path,
        )

        logger.info(
            "status_ok",
            run_id=result["run_id"],
            phase=result["phase"],
            events=result["event_count"],
        )
        logger.debug(
            "status_detail",
            run_dir=str(resolved_path),
        )
        logger.info("trw_status_read", run_id=result["run_id"])

        _apply_ceremony_status(
            cast("dict[str, object]", result),
            tool_name="STATUS",
            debug_event="status_nudge_injection_skipped",
        )
        result["field_scope"] = field_scope_label(cast("dict[str, object]", result))  # PRD-CORE-305-FR06
        return result

    @server.tool(output_schema=None)
    def trw_checkpoint(
        ctx: Context | None = None,
        run_path: str | None = None,
        message: str = "",
        shard_id: str | None = None,
        heartbeat: bool = False,
        pre_compact: bool = False,
        directive: str = "",
        context_anchor: str = "",
        blocked_decision: dict[str, object] | None = None,
        slice_done: str = "",
    ) -> dict[str, object]:
        """Use when a milestone is done; message required, blank no-ops.
        heartbeat=True refreshes a long pin. pre_compact=True takes a
        pre-compaction checkpoint (directive/context_anchor).
        blocked_decision: record an ESCALATE no one can answer now; the loop
        halts on it. slice_done: id of a finished PRD slice (feeds the ETA).

        Output: recorded, status, timestamp; reason+remedy if not.
        """
        if heartbeat:
            return cast("dict[str, object]", compute_heartbeat_result(ctx, message))
        if pre_compact:
            return cast(
                "dict[str, object]",
                execute_pre_compact_checkpoint(ctx, directive, context_anchor),
            )

        result = execute_checkpoint(
            run_path,
            message,
            shard_id,
            context=_build_call_context(ctx),
            blocked_decision=blocked_decision,
            slice_done=slice_done,
        )

        _apply_ceremony_status(
            result,
            tool_name="CHECKPOINT",
            debug_event="checkpoint_nudge_injection_skipped",
            # NFR04: ceremony progress must not claim a checkpoint that was
            # never persisted — the deliver gate and nudges read that counter.
            mark_checkpoint_first=result.get("recorded") is True,
            # Same source of truth for the PROSE: without this the nudge layer
            # defaults to tool_success=True and can attach "Progress saved." to
            # the very response that says recorded=False (CONSTITUTION §1).
            tool_success=result.get("recorded") is True,
        )

        return result
