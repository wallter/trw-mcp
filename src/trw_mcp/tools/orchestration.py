"""TRW orchestration tools — init, status, phase check, wave validate, resume, checkpoint, event, shard context.

These 8 tools codify FRAMEWORK.md v18.0_TRW execution flow:
RESEARCH -> PLAN -> IMPLEMENT -> VALIDATE -> REVIEW -> DELIVER
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError, ValidationError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import (
    Confidence,
    PHASE_ORDER,
    Phase,
    ReversionTrigger,
    RunState,
    RunStatus,
    ShardCard,
    ShardStatus,
    WaveEntry,
    WaveManifest,
    WaveStatus,
)
from trw_mcp.scoring import process_outcome_for_event
from trw_mcp.state._paths import resolve_project_root, resolve_run_path, resolve_trw_dir
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)
from trw_mcp.state.framework import assemble_framework
from trw_mcp.state.validation import (
    check_phase_exit,
    check_phase_input,
    validate_wave_contracts,
)

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()
_events = FileEventLogger(_writer)


def register_orchestration_tools(server: FastMCP) -> None:
    """Register all 8 orchestration tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool()
    def trw_init(
        task_name: str,
        objective: str = "",
        config_overrides: dict[str, str] | None = None,
        prd_scope: list[str] | None = None,
        run_type: str = "implementation",
        task_root: str | None = None,
    ) -> dict[str, str]:
        """Bootstrap TRW run scaffolding — creates .trw/, run dirs, run.yaml, events.jsonl.

        Args:
            task_name: Name of the task (used for directory naming).
            objective: Optional objective description for the run.
            config_overrides: Optional config values to override defaults.
            prd_scope: Optional list of PRD IDs governing this run (e.g. ["PRD-CORE-009"]).
            run_type: Run type — "implementation" (default) or "research". Research runs skip PRD enforcement.
            task_root: Optional task directory root (default: config field or "docs").
        """
        project_root = resolve_project_root()
        trw_dir = project_root / _config.trw_dir

        # Generate run ID
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        random_hex = secrets.token_hex(4)
        run_id = f"{timestamp}-{random_hex}"

        # Create .trw/ structure if it doesn't exist
        trw_subdirs = [
            _config.learnings_dir + "/" + _config.entries_dir,
            _config.reflections_dir,
            _config.scripts_dir,
            _config.patterns_dir,
            _config.context_dir,
            _config.frameworks_dir,
            _config.templates_dir,
        ]
        for subdir in trw_subdirs:
            _writer.ensure_dir(trw_dir / subdir)

        # Write default config if missing
        config_path = trw_dir / "config.yaml"
        if not _reader.exists(config_path):
            config_data: dict[str, object] = {
                "framework_version": _config.framework_version,
                "telemetry": _config.telemetry,
                "parallelism_max": _config.parallelism_max,
                "timebox_hours": _config.timebox_hours,
            }
            if config_overrides:
                config_data.update(config_overrides)
            _writer.write_yaml(config_path, config_data)

        # Write .trw/.gitignore
        gitignore_path = trw_dir / ".gitignore"
        if not _reader.exists(gitignore_path):
            gitignore_content = (
                "# TRW self-learning layer gitignore\n"
                "# Track: config, learnings, scripts, patterns, context\n"
                "# Ignore: reflections, event streams, locks, databases\n"
                "reflections/\n"
                "*.jsonl\n"
                "*.lock\n"
                "knowledge.db\n"
            )
            gitignore_path.parent.mkdir(parents=True, exist_ok=True)
            gitignore_path.write_text(gitignore_content, encoding="utf-8")

        # Deploy frameworks and templates to .trw/
        _deploy_frameworks(trw_dir)
        _deploy_templates(trw_dir)

        # Resolve task_root: explicit param > config field > default "docs"
        resolved_task_root = task_root if task_root is not None else _config.task_root

        # Create run directory structure
        task_dir = project_root / resolved_task_root / task_name
        run_root = task_dir / "runs" / run_id
        run_subdirs = [
            "meta",
            "reports",
            "artifacts",
            "artifacts/logs",
            "artifacts/legacy",
            "scratch",
            "scratch/_orchestrator",
            "scratch/_blackboard",
            "shards",
            "validation",
        ]
        for subdir in run_subdirs:
            _writer.ensure_dir(run_root / subdir)

        # Write run.yaml
        run_state = RunState(
            run_id=run_id,
            task=task_name,
            framework=_config.framework_version,
            status=RunStatus.ACTIVE,
            phase=Phase.RESEARCH,
            confidence=Confidence.MEDIUM,
            objective=objective,
            variables={
                "TASK": task_name,
                "TASK_DIR": str(task_dir),
                "RUN_ROOT": str(run_root),
                "TASK_ROOT": resolved_task_root,
            },
            prd_scope=prd_scope or [],
            run_type=run_type,
        )
        _writer.write_yaml(
            run_root / "meta" / "run.yaml",
            model_to_dict(run_state),
        )

        # Initialize events.jsonl
        _events.log_event(
            run_root / "meta" / "events.jsonl",
            "run_init",
            {"task": task_name, "framework": _config.framework_version},
        )

        # Assemble phase-specific framework snapshot (PRD-CORE-017).
        # Uses core + research overlay if available, falls back to monolithic.
        snapshot_path = run_root / "meta" / "FRAMEWORK_SNAPSHOT.md"
        try:
            snapshot_content = assemble_framework(trw_dir, "research")
        except FileNotFoundError:
            snapshot_content = _get_bundled_data("framework.md") or ""

        if snapshot_content:
            snapshot_path.write_text(snapshot_content, encoding="utf-8")

        logger.info(
            "trw_init_complete",
            task=task_name,
            run_id=run_id,
            run_path=str(run_root),
        )

        return {
            "run_id": run_id,
            "run_path": str(run_root),
            "trw_dir": str(trw_dir),
            "status": "initialized",
            "phase": "research",
        }

    @server.tool()
    def trw_status(run_path: str | None = None) -> dict[str, object]:
        """Return current run state — phase, wave progress, shard status, confidence.

        Args:
            run_path: Path to the run directory. Auto-detects if not provided.
        """
        resolved_path = resolve_run_path(run_path)
        meta_path = resolved_path / "meta"

        run_yaml_path = meta_path / "run.yaml"
        state_data = _reader.read_yaml(run_yaml_path)

        # Read wave manifest if exists (prefer shards/, fallback meta/)
        wave_data: dict[str, object] = {}
        from trw_mcp.tools.wave import resolve_wave_manifest_path

        wave_manifest_path = resolve_wave_manifest_path(resolved_path)
        if wave_manifest_path is not None:
            wave_data = _reader.read_yaml(wave_manifest_path)

        # Count events
        events_path = meta_path / "events.jsonl"
        events = _reader.read_jsonl(events_path)

        # Reflection metrics
        reflection_events = [
            e for e in events if e.get("event") == "reflection_complete"
        ]
        sync_events = [
            e for e in events if e.get("event") == "claude_md_synced"
        ]

        result: dict[str, object] = {
            "run_id": state_data.get("run_id", "unknown"),
            "task": state_data.get("task", "unknown"),
            "phase": state_data.get("phase", "unknown"),
            "status": state_data.get("status", "unknown"),
            "confidence": state_data.get("confidence", "unknown"),
            "framework": state_data.get("framework", "unknown"),
            "event_count": len(events),
            "reflection": {
                "count": len(reflection_events),
                "claude_md_synced": len(sync_events) > 0,
            },
        }

        if wave_data:
            result["waves"] = wave_data.get("waves", [])

            # PRD-CORE-012-FR07: Wave progress with shard status counts
            wave_progress = _compute_wave_progress(
                wave_data, resolved_path,
            )
            if wave_progress:
                result["wave_progress"] = wave_progress

        # PRD-CORE-013-FR07: Reversion frequency metrics
        reversion_metrics = _compute_reversion_metrics(events)
        result["reversions"] = reversion_metrics

        # PRD-FIX-005: Stale framework version warning
        version_warning = _check_framework_version_staleness(
            str(state_data.get("framework", "")),
        )
        if version_warning:
            result["version_warning"] = version_warning

        # PRD-CORE-015-FR05: Velocity summary in status output
        velocity_summary = _get_velocity_summary()
        if velocity_summary is not None:
            result["velocity_summary"] = velocity_summary

        logger.info("trw_status_read", run_id=result["run_id"])
        return result

    @server.tool()
    def trw_phase_check(
        phase_name: str,
        run_path: str | None = None,
        direction: str = "exit",
    ) -> dict[str, object]:
        """Validate exit criteria for a framework phase — reports pass/fail per criterion.

        Args:
            phase_name: Phase to check (research, plan, implement, validate, review, deliver).
            run_path: Path to the run directory. Auto-detects if not provided.
            direction: Check direction — "exit" (default) validates exit criteria,
                "enter" validates input prerequisites for entering the phase.
        """
        try:
            phase = Phase(phase_name.lower())
        except ValueError as exc:
            valid_phases = [p.value for p in Phase]
            raise ValidationError(
                f"Invalid phase: {phase_name!r}. Valid: {valid_phases}",
                phase=phase_name,
            ) from exc

        valid_directions = ("exit", "enter")
        if direction not in valid_directions:
            raise ValidationError(
                f"Invalid direction: {direction!r}. Valid: {list(valid_directions)}",
                phase=phase_name,
            )

        resolved_path = resolve_run_path(run_path)

        if direction == "enter":
            result = check_phase_input(phase, resolved_path, _config)
        else:
            result = check_phase_exit(phase, resolved_path, _config)

        _events.log_event(
            resolved_path / "meta" / "events.jsonl",
            "phase_check",
            {
                "phase": phase_name,
                "direction": direction,
                "valid": result.valid,
                "failures": len(result.failures),
            },
        )

        # Outcome correlation (PRD-CORE-004 Phase 1c) — best-effort
        outcome_label = "phase_gate_passed" if result.valid else "phase_gate_failed"
        q_updated: list[str] = []
        try:
            q_updated = process_outcome_for_event(outcome_label)
        except Exception:  # noqa: BLE001
            pass

        phase_result: dict[str, object] = {
            "phase": phase_name,
            "direction": direction,
            "valid": result.valid,
            "completeness_score": result.completeness_score,
            "failures": [
                {
                    "field": f.field,
                    "rule": f.rule,
                    "message": f.message,
                    "severity": f.severity,
                }
                for f in result.failures
            ],
        }
        if q_updated:
            phase_result["q_updates"] = len(q_updated)

        # PRD-QUAL-007: Architecture fitness in phase check output
        if _config.architecture_fitness_enabled:
            try:
                from trw_mcp.state.architecture import (
                    check_architecture_fitness,
                    load_architecture_config,
                )

                proj_root = resolve_project_root()
                arch_cfg = load_architecture_config(proj_root)
                if arch_cfg is not None:
                    fitness = check_architecture_fitness(
                        phase_name, resolved_path, arch_cfg, proj_root,
                    )
                    phase_result["architecture_fitness"] = {
                        "score": fitness.score,
                        "violations": len(fitness.violations),
                        "checks_run": fitness.checks_run,
                    }
            except Exception:  # noqa: BLE001
                pass  # Best-effort

        # PRD-CORE-015-FR08: Velocity alert for negative acceleration
        velocity_alert = _get_velocity_alert()
        if velocity_alert is not None:
            phase_result["velocity_alert"] = velocity_alert

        # PRD-CORE-025-FR03: Auto-progress PRD statuses on passing exit check
        if result.valid and direction == "exit":
            try:
                from trw_mcp.state.validation import auto_progress_prds

                proj_root = resolve_project_root()
                prds_dir = proj_root / Path(_config.prds_relative_path)
                if prds_dir.is_dir():
                    progressions = auto_progress_prds(
                        resolved_path, phase_name, prds_dir, _config,
                    )
                    if progressions:
                        phase_result["auto_progression"] = progressions
                        # FR04: Log events for each applied progression
                        for prog in progressions:
                            if prog.get("applied"):
                                _events.log_event(
                                    resolved_path / "meta" / "events.jsonl",
                                    "auto_prd_progress",
                                    {
                                        "prd_id": str(prog["prd_id"]),
                                        "from_status": str(prog["from_status"]),
                                        "to_status": str(prog["to_status"]),
                                        "phase": phase_name,
                                    },
                                )
            except Exception:  # noqa: BLE001
                pass  # Best-effort — never fail phase check for auto-progression

        return phase_result

    @server.tool()
    def trw_wave_validate(
        wave_number: int,
        run_path: str | None = None,
    ) -> dict[str, object]:
        """Validate output contracts for all shards in a wave — checks file existence and required keys.

        Args:
            wave_number: Wave number to validate (1-based).
            run_path: Path to the run directory. Auto-detects if not provided.
        """
        resolved_path = resolve_run_path(run_path)

        # Read wave manifest (prefer shards/, fallback meta/ — PRD-CORE-012 NFR03)
        from trw_mcp.tools.wave import resolve_wave_manifest_path as _resolve_wm

        wave_manifest_path = _resolve_wm(resolved_path)
        if wave_manifest_path is None:
            return {
                "wave": wave_number,
                "valid": False,
                "error": "No wave_manifest.yaml found",
                "failures": [],
            }

        manifest_data = _reader.read_yaml(wave_manifest_path)
        waves_raw = manifest_data.get("waves", [])

        # Find the target wave
        target_wave: WaveEntry | None = None
        if isinstance(waves_raw, list):
            for w in waves_raw:
                if isinstance(w, dict) and w.get("wave") == wave_number:
                    target_wave = WaveEntry(**w)
                    break

        if target_wave is None:
            return {
                "wave": wave_number,
                "valid": False,
                "error": f"Wave {wave_number} not found in manifest",
                "failures": [],
            }

        # Load shard cards
        shards: list[ShardCard] = []
        shards_manifest_path = resolved_path / "shards" / "manifest.yaml"
        if _reader.exists(shards_manifest_path):
            shard_data = _reader.read_yaml(shards_manifest_path)
            raw_shards = shard_data.get("shards", [])
            if isinstance(raw_shards, list):
                for s in raw_shards:
                    if isinstance(s, dict) and s.get("wave") == wave_number:
                        shards.append(ShardCard(**s))

        if not shards:
            return {
                "wave": wave_number,
                "valid": False,
                "error": f"No shard cards found for wave {wave_number}",
                "failures": [],
            }

        try:
            failures = validate_wave_contracts(
                target_wave, shards, resolved_path,
            )
        except ValidationError as exc:
            return {
                "wave": wave_number,
                "valid": False,
                "error": str(exc),
                "failures": [],
            }

        is_valid = len(failures) == 0

        _events.log_event(
            resolved_path / "meta" / "events.jsonl",
            "wave_validated",
            {
                "wave": wave_number,
                "valid": is_valid,
                "failures": len(failures),
            },
        )

        # Outcome correlation (PRD-CORE-004 Phase 1c) — best-effort
        q_updated: list[str] = []
        if is_valid:
            try:
                q_updated = process_outcome_for_event("wave_validation_passed")
            except Exception:  # noqa: BLE001
                pass

        wave_result: dict[str, object] = {
            "wave": wave_number,
            "valid": is_valid,
            "shards_checked": len(shards),
            "failures": [
                {
                    "field": f.field,
                    "rule": f.rule,
                    "message": f.message,
                    "severity": f.severity,
                }
                for f in failures
            ],
        }
        if q_updated:
            wave_result["q_updates"] = len(q_updated)
        return wave_result

    @server.tool()
    def trw_resume(run_path: str | None = None) -> dict[str, object]:
        """Scan findings, classify complete/partial/failed shards, propose recovery plan.

        Args:
            run_path: Path to the run directory. Auto-detects if not provided.
        """
        resolved_path = resolve_run_path(run_path)
        meta_path = resolved_path / "meta"
        scratch_path = resolved_path / "scratch"

        # Read run state
        state_data = _reader.read_yaml(meta_path / "run.yaml")

        # Scan scratch directories for findings
        complete: list[str] = []
        partial: list[str] = []
        failed: list[str] = []
        not_started: list[str] = []

        if scratch_path.exists():
            for shard_dir in sorted(scratch_path.iterdir()):
                if not shard_dir.is_dir():
                    continue
                if shard_dir.name.startswith("_"):
                    continue  # Skip _orchestrator, _blackboard

                findings_path = shard_dir / "findings.yaml"
                if not findings_path.exists():
                    not_started.append(shard_dir.name)
                    continue

                try:
                    findings = _reader.read_yaml(findings_path)
                    status = findings.get("status", "unknown")
                    if status == "complete":
                        complete.append(shard_dir.name)
                    elif status == "partial":
                        partial.append(shard_dir.name)
                    elif status == "failed":
                        failed.append(shard_dir.name)
                    else:
                        partial.append(shard_dir.name)
                except StateError:
                    failed.append(shard_dir.name)

        # Build recovery plan
        recovery: list[str] = []
        if failed:
            recovery.append(f"Re-launch failed shards: {', '.join(failed)}")
        if partial:
            recovery.append(f"Complete partial shards: {', '.join(partial)}")
        if not_started:
            recovery.append(f"Launch not-started shards: {', '.join(not_started)}")
        if not recovery:
            recovery.append("All shards complete — proceed to next phase")

        _events.log_event(
            meta_path / "events.jsonl",
            "run_resumed",
            {
                "complete": len(complete),
                "partial": len(partial),
                "failed": len(failed),
                "not_started": len(not_started),
            },
        )

        logger.info(
            "trw_resume_scan",
            complete=len(complete),
            partial=len(partial),
            failed=len(failed),
        )

        return {
            "run_id": state_data.get("run_id", "unknown"),
            "phase": state_data.get("phase", "unknown"),
            "status": state_data.get("status", "unknown"),
            "shards": {
                "complete": complete,
                "partial": partial,
                "failed": failed,
                "not_started": not_started,
            },
            "recovery_plan": recovery,
        }

    @server.tool()
    def trw_checkpoint(
        run_path: str | None = None,
        message: str = "",
        shard_id: str | None = None,
    ) -> dict[str, str]:
        """Create atomic state snapshot — appends to checkpoints.jsonl with timestamp.

        Args:
            run_path: Path to the run directory. Auto-detects if not provided.
            message: Optional message describing the checkpoint context.
            shard_id: Optional shard identifier for sub-agent attribution.
        """
        resolved_path = resolve_run_path(run_path)
        meta_path = resolved_path / "meta"

        # Read current state
        state_data = _reader.read_yaml(meta_path / "run.yaml")

        # Create checkpoint record
        ts = datetime.now(timezone.utc).isoformat()
        checkpoint: dict[str, object] = {
            "ts": ts,
            "message": message,
            "state": state_data,
        }
        if shard_id:
            checkpoint["shard_id"] = shard_id

        # Append to checkpoints.jsonl
        checkpoints_path = meta_path / "checkpoints.jsonl"
        _writer.append_jsonl(checkpoints_path, checkpoint)

        event_data: dict[str, object] = {"message": message}
        if shard_id:
            event_data["shard_id"] = shard_id
        _events.log_event(
            meta_path / "events.jsonl",
            "checkpoint",
            event_data,
        )

        logger.info("trw_checkpoint_created", message=message)
        return {"timestamp": ts, "status": "checkpoint_created", "message": message}

    @server.tool()
    def trw_event(
        event_type: str,
        run_path: str | None = None,
        data: dict[str, str | int | float | bool] | None = None,
        shard_id: str | None = None,
        agent_role: str | None = None,
    ) -> dict[str, object]:
        """Log a structured event to events.jsonl — append-only audit trail.

        Args:
            event_type: Event type identifier (e.g., "phase_enter", "shard_complete").
            run_path: Path to the run directory. Auto-detects if not provided.
            data: Additional event data as key-value pairs.
            shard_id: Optional shard identifier for sub-agent attribution.
            agent_role: Optional agent role (e.g., "research", "implementation").
        """
        resolved_path = resolve_run_path(run_path)
        event_data: dict[str, object] = dict(data) if data else {}
        if shard_id:
            event_data["shard_id"] = shard_id
        if agent_role:
            event_data["agent_role"] = agent_role

        # PRD-CORE-013-FR01: phase_revert event handling
        reversion_applied = False
        if event_type == "phase_revert":
            reversion_applied = _handle_phase_revert(
                event_data, resolved_path,
            )

        _events.log_event(
            resolved_path / "meta" / "events.jsonl",
            event_type,
            event_data,
        )

        # Outcome correlation (PRD-CORE-004 Phase 1c, PRD-CORE-026-FR03) — best-effort
        q_updated: list[str] = []
        try:
            q_updated = process_outcome_for_event(event_type, event_data)
        except Exception:  # noqa: BLE001 — best-effort, never fail the event
            pass

        result: dict[str, object] = {
            "status": "event_logged",
            "event_type": event_type,
        }
        if q_updated:
            result["q_updates"] = len(q_updated)
        if event_type == "phase_revert":
            result["reversion_applied"] = reversion_applied
        return result

    @server.tool()
    def trw_shard_context(
        run_path: str,
        shard_id: str,
    ) -> dict[str, object]:
        """Return context for a sub-agent shard — paths, IDs, tool guidance.

        Sub-agents call this first to discover run state, working paths,
        and which TRW tools to use during their shard execution.

        Args:
            run_path: Path to the run directory (required for sub-agents).
            shard_id: Shard identifier (e.g., "S1", "shard-research-01").
        """
        resolved_path = resolve_run_path(run_path)
        meta_path = resolved_path / "meta"

        # Read run state for wave number
        wave_number: int | None = None
        run_id = ""
        if (meta_path / "run.yaml").exists():
            state_data = _reader.read_yaml(meta_path / "run.yaml")
            run_id = str(state_data.get("run_id", ""))
            # Wave number from wave_progress if available
            wave_progress = state_data.get("wave_progress", {})
            if isinstance(wave_progress, dict):
                try:
                    wave_number = int(str(wave_progress.get("current_wave", 1)))
                except (ValueError, TypeError):
                    wave_number = 1

        trw_dir = resolve_project_root() / _config.trw_dir
        findings_path = resolved_path / _config.findings_dir
        events_path = meta_path / _config.events_file
        scratch_path = resolved_path / _config.scratch_dir / shard_id

        resolved_str = str(resolved_path)
        tool_guidance = (
            f"1. Use trw_event(shard_id='{shard_id}') to log progress\n"
            f"2. Use trw_finding_register(run_path='{resolved_str}') for discoveries\n"
            f"3. Use trw_learn(shard_id='{shard_id}') for learnings\n"
            f"4. Use trw_checkpoint(shard_id='{shard_id}') for state saves\n"
            f"5. Write shard outputs to {_config.scratch_dir}/{shard_id}/\n"
        )

        return {
            "run_path": str(resolved_path),
            "run_id": run_id,
            "shard_id": shard_id,
            "wave_number": wave_number,
            "trw_dir": str(trw_dir),
            "scratch_path": str(scratch_path),
            "findings_path": str(findings_path),
            "events_path": str(events_path),
            "tool_guidance": tool_guidance,
        }


def _handle_phase_revert(
    event_data: dict[str, object],
    run_path: Path,
) -> bool:
    """Handle phase_revert event: validate and update run.yaml phase.

    PRD-CORE-013-FR01: Validates that to_phase is strictly earlier than
    from_phase. If valid, updates run.yaml phase. If invalid, logs warning.
    PRD-CORE-013-FR03: Captures affected PRDs in event data.

    Args:
        event_data: Event data dict (mutated in place with validation results).
        run_path: Path to the run directory.

    Returns:
        True if reversion was applied, False if invalid.
    """
    from_phase = str(event_data.get("from_phase", ""))
    to_phase = str(event_data.get("to_phase", ""))
    trigger_str = str(event_data.get("trigger", "other"))

    # Classify trigger (FR02)
    trigger = ReversionTrigger.classify(trigger_str)
    if trigger_str != trigger.value:
        event_data["trigger_warning"] = (
            f"Unknown trigger '{trigger_str}' classified as OTHER"
        )
    event_data["trigger_classified"] = trigger.value

    # Validate phase ordering (FR01)
    from_order = PHASE_ORDER.get(from_phase, -1)
    to_order = PHASE_ORDER.get(to_phase, -1)

    if from_order < 0 or to_order < 0:
        event_data["reversion_status"] = "invalid"
        event_data["reversion_error"] = (
            f"Invalid phase values: from={from_phase!r}, to={to_phase!r}"
        )
        return False

    if to_order >= from_order:
        event_data["reversion_status"] = "invalid"
        event_data["reversion_error"] = (
            f"Cannot revert forward or to same phase: "
            f"{from_phase} -> {to_phase}"
        )
        return False

    # FR03: Capture affected PRDs
    try:
        from trw_mcp.state.prd_utils import discover_governing_prds, parse_frontmatter

        prd_ids = discover_governing_prds(run_path, _config)
        if prd_ids:
            affected_prds: list[dict[str, str]] = []
            project_root = resolve_project_root()
            prds_dir = project_root / _config.prds_relative_path
            for prd_id in prd_ids:
                prd_file = prds_dir / f"{prd_id}.md"
                if prd_file.exists():
                    content = prd_file.read_text(encoding="utf-8")
                    fm = parse_frontmatter(content)
                    affected_prds.append({
                        "id": prd_id,
                        "status_at_reversion": str(fm.get("status", "unknown")),
                    })
            if affected_prds:
                event_data["affected_prds"] = affected_prds
    except Exception:  # noqa: BLE001
        pass  # Best-effort PRD capture

    # Valid reversion — update run.yaml phase
    meta_path = run_path / "meta"
    run_yaml_path = meta_path / "run.yaml"
    if run_yaml_path.exists():
        state_data = _reader.read_yaml(run_yaml_path)
        state_data["phase"] = to_phase
        _writer.write_yaml(run_yaml_path, state_data)

    event_data["reversion_status"] = "applied"
    logger.info(
        "phase_revert_applied",
        from_phase=from_phase,
        to_phase=to_phase,
        trigger=trigger.value,
    )
    return True


def _compute_wave_progress(
    wave_data: dict[str, object],
    run_path: Path,
) -> dict[str, object] | None:
    """Compute wave-level and shard-level progress summary.

    PRD-CORE-012-FR07: Enhanced trw_status with wave/shard progress.

    Args:
        wave_data: Parsed wave_manifest.yaml content.
        run_path: Path to the run directory (for reading shard manifest).

    Returns:
        Wave progress dict, or None if no waves found.
    """
    waves_raw = wave_data.get("waves", [])
    if not isinstance(waves_raw, list) or not waves_raw:
        return None

    # Read shard manifest for status data
    shard_statuses: dict[str, str] = {}
    shard_manifest_path = run_path / "shards" / "manifest.yaml"
    if shard_manifest_path.exists():
        try:
            shard_data = _reader.read_yaml(shard_manifest_path)
            raw_shards = shard_data.get("shards", [])
            if isinstance(raw_shards, list):
                for s in raw_shards:
                    if isinstance(s, dict):
                        sid = str(s.get("id", ""))
                        shard_statuses[sid] = str(s.get("status", "pending"))
        except (StateError, OSError, ValueError, TypeError):
            pass

    completed_waves = 0
    active_wave: int | None = None
    wave_details: list[dict[str, object]] = []

    for w in waves_raw:
        if not isinstance(w, dict):
            continue
        wave_num = w.get("wave", 0)
        wave_status = str(w.get("status", "pending"))
        wave_shard_ids = w.get("shards", [])
        if not isinstance(wave_shard_ids, list):
            wave_shard_ids = []

        # Count shard statuses for this wave
        counts: dict[str, int] = {
            "complete": 0, "active": 0, "pending": 0,
            "failed": 0, "partial": 0,
        }
        for sid in wave_shard_ids:
            st = shard_statuses.get(str(sid), "pending")
            if st in counts:
                counts[st] += 1

        if wave_status in ("complete", "partial"):
            completed_waves += 1
        elif wave_status == "active" or counts["active"] > 0:
            active_wave = wave_num

        wave_details.append({
            "wave": wave_num,
            "status": wave_status,
            "shards": {
                "total": len(wave_shard_ids),
                **counts,
            },
        })

    return {
        "total_waves": len(waves_raw),
        "completed_waves": completed_waves,
        "active_wave": active_wave,
        "wave_details": wave_details,
    }


def _compute_reversion_metrics(
    events: list[dict[str, object]],
) -> dict[str, object]:
    """Compute reversion frequency metrics from events.

    PRD-CORE-013-FR07: Scans events for phase_revert and phase_enter
    events to compute reversion rate and classification.

    Args:
        events: List of event dicts from events.jsonl.

    Returns:
        Reversion metrics dict with count, rate, by_trigger, classification, latest.
    """
    revert_events = [
        e for e in events if e.get("event") == "phase_revert"
    ]
    phase_enter_events = [
        e for e in events if e.get("event") == "phase_enter"
    ]

    revert_count = len(revert_events)
    total_transitions = revert_count + len(phase_enter_events)
    rate = revert_count / total_transitions if total_transitions > 0 else 0.0

    # By-trigger aggregation
    by_trigger: dict[str, int] = {}
    for evt in revert_events:
        trigger = str(evt.get("trigger_classified", evt.get("trigger", "other")))
        by_trigger[trigger] = by_trigger.get(trigger, 0) + 1

    # Classification with configurable thresholds
    if rate >= _config.reversion_rate_concerning:
        classification = "concerning"
    elif rate >= _config.reversion_rate_elevated:
        classification = "elevated"
    else:
        classification = "healthy"

    # Latest reversion
    latest: dict[str, object] | None = None
    if revert_events:
        last = revert_events[-1]
        latest = {
            "from_phase": last.get("from_phase", ""),
            "to_phase": last.get("to_phase", ""),
            "trigger": last.get("trigger_classified", last.get("trigger", "")),
            "reason": last.get("reason", ""),
            "ts": last.get("ts", ""),
        }

    return {
        "count": revert_count,
        "rate": round(rate, 4),
        "by_trigger": by_trigger,
        "classification": classification,
        "latest": latest,
    }


def _get_bundled_data(filename: str) -> str | None:
    """Load a bundled data file from the package data directory.

    Args:
        filename: File to load (e.g., "framework.md", "aaref.md").

    Returns:
        File text content, or None if not found.
    """
    data_dir = Path(__file__).parent.parent / "data"
    file_path = data_dir / filename
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")
    return None


def _get_bundled_template(name: str) -> str | None:
    """Load bundled template from data/templates/.

    Args:
        name: Template filename (e.g. "claude_md.md").

    Returns:
        Template text content, or None if not found.
    """
    data_dir = Path(__file__).parent.parent / "data" / "templates"
    template_path = data_dir / name
    if template_path.exists():
        return template_path.read_text(encoding="utf-8")
    return None


def _get_package_version() -> str:
    """Get the trw-mcp package version from importlib.metadata.

    Returns:
        Package version string, or "unknown" if not installed.
    """
    try:
        from importlib.metadata import version as pkg_version
        return pkg_version("trw-mcp")
    except Exception:
        return "unknown"


def _deploy_frameworks(trw_dir: Path) -> dict[str, str]:
    """Deploy bundled frameworks to .trw/frameworks/ as read-only references.

    Writes FRAMEWORK.md, AARE-F-FRAMEWORK.md, and VERSION.yaml.
    Skips if VERSION.yaml matches current bundled versions (idempotent).

    Args:
        trw_dir: Path to the .trw directory.

    Returns:
        Dictionary with deployment status and version info.
    """
    frameworks_dir = trw_dir / _config.frameworks_dir
    _writer.ensure_dir(frameworks_dir)

    version_path = frameworks_dir / "VERSION.yaml"
    current_fw_version = _config.framework_version
    current_aaref_version = _config.aaref_version
    current_pkg_version = _get_package_version()

    # Check existing VERSION.yaml for skip logic
    if _reader.exists(version_path):
        existing = _reader.read_yaml(version_path)
        existing_fw = str(existing.get("framework_version", ""))
        existing_aaref = str(existing.get("aaref_version", ""))
        existing_pkg = str(existing.get("trw_mcp_version", ""))
        if (
            existing_fw == current_fw_version
            and existing_aaref == current_aaref_version
            and existing_pkg == current_pkg_version
        ):
            return {"status": "up_to_date", "framework_version": current_fw_version}

        # Version mismatch — log upgrade event
        events_path = trw_dir / "upgrade_events.jsonl"
        _events.log_event(events_path, "framework_upgrade", {
            "old_framework": existing_fw,
            "new_framework": current_fw_version,
            "old_aaref": existing_aaref,
            "new_aaref": current_aaref_version,
            "old_pkg": existing_pkg,
            "new_pkg": current_pkg_version,
        })

    # Deploy framework files
    framework_data = _get_bundled_data("framework.md")
    if framework_data:
        fw_path = frameworks_dir / "FRAMEWORK.md"
        fw_path.write_text(framework_data, encoding="utf-8")

    aaref_data = _get_bundled_data("aaref.md")
    if aaref_data:
        aaref_path = frameworks_dir / "AARE-F-FRAMEWORK.md"
        aaref_path.write_text(aaref_data, encoding="utf-8")

    # Deploy overlay files (PRD-CORE-017)
    core_data = _get_bundled_data("trw-core.md")
    if core_data:
        core_path = frameworks_dir / "trw-core.md"
        core_path.write_text(core_data, encoding="utf-8")

    overlays_dir = frameworks_dir / "overlays"
    _writer.ensure_dir(overlays_dir)
    deployed_overlays: list[str] = []
    for phase in (p.value for p in Phase):
        overlay_data = _get_bundled_data(f"overlays/trw-{phase}.md")
        if overlay_data:
            overlay_path = overlays_dir / f"trw-{phase}.md"
            overlay_path.write_text(overlay_data, encoding="utf-8")
            deployed_overlays.append(phase)

    # Write VERSION.yaml
    version_data: dict[str, object] = {
        "framework_version": current_fw_version,
        "aaref_version": current_aaref_version,
        "trw_mcp_version": current_pkg_version,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "overlays_deployed": deployed_overlays,
    }
    _writer.write_yaml(version_path, version_data)

    logger.info(
        "frameworks_deployed",
        framework_version=current_fw_version,
        aaref_version=current_aaref_version,
    )

    return {
        "status": "deployed",
        "framework_version": current_fw_version,
        "aaref_version": current_aaref_version,
    }


def _deploy_templates(trw_dir: Path) -> None:
    """Copy bundled CLAUDE.md template to .trw/templates/ if not present.

    Does NOT overwrite existing template (preserves project customizations).

    Args:
        trw_dir: Path to the .trw directory.
    """
    templates_dir = trw_dir / _config.templates_dir
    _writer.ensure_dir(templates_dir)

    template_path = templates_dir / "claude_md.md"
    if template_path.exists():
        return  # Preserve project customization

    template_data = _get_bundled_template("claude_md.md")
    if template_data:
        template_path.write_text(template_data, encoding="utf-8")


def _check_framework_version_staleness(run_framework: str) -> str | None:
    """Compare run's framework version against the current deployed version.

    Reads ``.trw/frameworks/VERSION.yaml`` and compares against the run's
    framework field. Returns a warning message if they differ, None otherwise.

    Args:
        run_framework: Framework version string from run.yaml.

    Returns:
        Warning message string if versions differ, None if current or unreadable.
    """
    if not run_framework:
        return None

    try:
        trw_dir = resolve_project_root() / _config.trw_dir
        version_path = trw_dir / _config.frameworks_dir / "VERSION.yaml"
        if not _reader.exists(version_path):
            return None

        version_data = _reader.read_yaml(version_path)
        current_version = str(version_data.get("framework_version", ""))
        if not current_version or run_framework == current_version:
            return None

        return (
            f"Run uses framework {run_framework} but current is "
            f"{current_version}. Consider re-bootstrapping or "
            f"acknowledging the version delta."
        )
    except (StateError, ValueError, TypeError, OSError):
        return None


def _read_velocity_history(min_entries: int) -> list[dict[str, object]] | None:
    """Read velocity history from .trw/context/velocity.yaml.

    Args:
        min_entries: Minimum number of history entries required.

    Returns:
        History list if available and meets minimum, None otherwise.
    """
    trw_dir = resolve_trw_dir()
    velocity_path = trw_dir / _config.context_dir / "velocity.yaml"
    if not _reader.exists(velocity_path):
        return None

    data = _reader.read_yaml(velocity_path)
    history = data.get("history", [])
    if not isinstance(history, list) or len(history) < min_entries:
        return None
    return history


def _extract_throughputs(history: list[dict[str, object]]) -> list[float]:
    """Extract shard_throughput values from velocity history entries.

    Args:
        history: List of velocity history dicts with nested metrics.

    Returns:
        List of throughput floats, one per history entry.
    """
    throughputs: list[float] = []
    for entry in history:
        metrics = entry.get("metrics", {})
        if isinstance(metrics, dict):
            throughputs.append(float(str(metrics.get("shard_throughput", 0.0))))
        else:
            throughputs.append(0.0)
    return throughputs


def _get_velocity_summary() -> dict[str, object] | None:
    """Get compact velocity summary for trw_status output.

    PRD-CORE-015-FR05: Returns velocity_summary block when history exists
    with >= 2 entries. Returns None otherwise.

    Returns:
        Velocity summary dict or None.
    """
    try:
        history = _read_velocity_history(min_entries=2)
        if history is None:
            return None

        throughputs = _extract_throughputs(history)
        last_throughput = throughputs[-1]

        if len(history) >= 3:
            from trw_mcp.velocity import linear_fit

            x = [float(i) for i in range(len(throughputs))]
            try:
                slope, _, r_squared = linear_fit(x, throughputs)
                mean_t = sum(throughputs) / len(throughputs)
                threshold = _config.velocity_stable_threshold * max(mean_t, 0.01)
                if abs(slope) < threshold:
                    direction = "stable"
                elif slope > 0:
                    direction = "improving"
                else:
                    direction = "declining"
            except ValueError:
                direction = "insufficient_data"
                r_squared = None
        else:
            direction = "insufficient_data"
            r_squared = None

        return {
            "last_run_throughput": round(last_throughput, 4),
            "trend_direction": direction,
            "trend_confidence": round(r_squared, 4) if r_squared is not None else None,
            "runs_in_history": len(history),
        }
    except (StateError, OSError, ValueError, TypeError):
        return None


def _get_velocity_alert() -> dict[str, object] | None:
    """Check for negative velocity acceleration and return alert if detected.

    PRD-CORE-015-FR08: Advisory alert when velocity history >= min_runs
    and linear trend is negative with R-squared above threshold.

    Returns:
        Velocity alert dict or None.
    """
    try:
        history = _read_velocity_history(
            min_entries=_config.velocity_alert_min_runs,
        )
        if history is None:
            return None

        from trw_mcp.velocity import linear_fit

        throughputs = _extract_throughputs(history)
        x = [float(i) for i in range(len(throughputs))]
        slope, _, r_squared = linear_fit(x, throughputs)

        if slope < 0 and r_squared > _config.velocity_alert_r_squared_min:
            return {
                "type": "negative_acceleration",
                "trend_slope": round(slope, 4),
                "trend_r_squared": round(r_squared, 4),
                "message": (
                    f"Velocity declining over last {len(history)} runs "
                    f"(R²={r_squared:.2f}). Consider investigating debt "
                    f"indicators or learning effectiveness."
                ),
                "severity": "warning",
            }
    except (StateError, OSError, ValueError, TypeError):
        pass

    return None


