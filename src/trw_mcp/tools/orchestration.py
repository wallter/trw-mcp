"""TRW orchestration tools — init, status, checkpoint.

These 3 tools codify the FRAMEWORK.md execution flow:
RESEARCH -> PLAN -> IMPLEMENT -> VALIDATE -> REVIEW -> DELIVER
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.models.run import (
    ComplexitySignals,
    Confidence,
    Phase,
    RunState,
    RunStatus,
)
from trw_mcp.scoring import classify_complexity, get_phase_requirements
from trw_mcp.state._paths import pin_active_run, resolve_project_root, resolve_run_path
from trw_mcp.state.analytics_report import count_stale_runs
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger()

_config = get_config()
_reader = FileStateReader()
_writer = FileStateWriter()
_events = FileEventLogger(_writer)


def register_orchestration_tools(server: FastMCP) -> None:
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
        project_root = resolve_project_root()
        trw_dir = project_root / _config.trw_dir

        # Generate run ID: timestamp + random suffix for uniqueness
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{timestamp}-{secrets.token_hex(4)}"

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

        # Write .trw/.gitignore from bundled template (DRY with bootstrap.py)
        gitignore_path = trw_dir / ".gitignore"
        if not _reader.exists(gitignore_path):
            gitignore_content = _get_bundled_file("gitignore.txt")
            if gitignore_content:
                gitignore_path.parent.mkdir(parents=True, exist_ok=True)
                gitignore_path.write_text(gitignore_content, encoding="utf-8")

        # Deploy frameworks and templates to .trw/
        _deploy_frameworks(trw_dir)
        _deploy_templates(trw_dir)

        # Resolve task_root: explicit param > config field > default "docs"
        resolved_task_root = task_root if task_root is not None else _config.task_root

        task_dir = project_root / resolved_task_root / task_name
        run_root = task_dir / "runs" / run_id
        run_subdirs = [
            "meta",
            "reports",
            "scratch/_orchestrator",
            "shards",
        ]
        for subdir in run_subdirs:
            _writer.ensure_dir(run_root / subdir)

        initial_phase = Phase.RESEARCH

        variables: dict[str, str] = {
            "TASK": task_name,
            "TASK_DIR": str(task_dir),
            "RUN_ROOT": str(run_root),
            "TASK_ROOT": resolved_task_root,
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
            framework=_config.framework_version,
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
        _writer.write_yaml(
            run_root / "meta" / "run.yaml",
            model_to_dict(run_state),
        )

        # Pin this run as the active run for this process (RC-001 fix).
        # Prevents telemetry hijack when parallel instances share filesystem.
        pin_active_run(run_root)

        _events.log_event(
            run_root / "meta" / "events.jsonl",
            "run_init",
            {"task": task_name, "framework": _config.framework_version},
        )

        # Framework version captured in run.yaml `framework` field.
        # Full snapshot removed — saves ~20 KB per run, reconstruct from git if needed.

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

        return result

    @server.tool()
    @log_tool_call
    def trw_status(run_path: str | None = None) -> dict[str, object]:
        """See your current phase, completed work, and what to do next — so you pick up where you left off instead of redoing work.

        Returns run state including phase, wave progress, shard status, confidence,
        and framework version. Essential when resuming after a context compaction
        or session restart.

        Args:
            run_path: Path to the run directory. Auto-detects if not provided.
        """
        resolved_path = resolve_run_path(run_path)
        meta_path = resolved_path / "meta"

        state_data = _reader.read_yaml(meta_path / "run.yaml")

        wave_data: dict[str, object] = {}
        wave_manifest_path = resolved_path / "shards" / "wave_manifest.yaml"
        if not wave_manifest_path.exists():
            wave_manifest_path = meta_path / "wave_manifest.yaml"
        if wave_manifest_path.exists():
            wave_data = _reader.read_yaml(wave_manifest_path)

        events_path = meta_path / "events.jsonl"
        events = _reader.read_jsonl(events_path)

        # Reflection metrics (count only, no need to collect full lists)
        reflection_count = sum(
            1 for e in events if e.get("event") == "reflection_complete"
        )
        has_synced = any(
            e.get("event") == "claude_md_synced" for e in events
        )

        result: dict[str, object] = {
            "run_id": state_data.get("run_id", "unknown"),
            "task": state_data.get("task", "unknown"),
            "phase": state_data.get("phase", "unknown"),
            "status": state_data.get("status", "unknown"),
            "confidence": state_data.get("confidence", "unknown"),
            "framework": state_data.get("framework", "unknown"),
            "event_count": len(events),
            "reflection": {
                "count": reflection_count,
                "claude_md_synced": has_synced,
            },
        }

        if wave_data:
            result["waves"] = wave_data.get("waves", [])

            wave_progress = _compute_wave_progress(
                wave_data, resolved_path,
            )
            if wave_progress:
                result["wave_progress"] = wave_progress

        # Reversion frequency metrics
        reversion_metrics = _compute_reversion_metrics(events)
        result["reversions"] = reversion_metrics

        # Last activity tracking (RC-002: detect stale/abandoned tracks)
        checkpoints_path = meta_path / "checkpoints.jsonl"
        if checkpoints_path.exists():
            checkpoints = _reader.read_jsonl(checkpoints_path)
            if checkpoints:
                last_cp = checkpoints[-1]
                last_ts = str(last_cp.get("ts", ""))
                result["last_activity_ts"] = last_ts
                if last_ts:
                    try:
                        last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        delta_hours = (now - last_dt).total_seconds() / 3600
                        result["hours_since_activity"] = round(delta_hours, 1)
                    except (ValueError, TypeError):
                        pass
        if "last_activity_ts" not in result:
            # Fall back to run.yaml creation (run_init event)
            run_init_events = [
                e for e in events if str(e.get("event", "")) == "run_init"
            ]
            if run_init_events:
                init_ts = str(run_init_events[0].get("ts", ""))
                if init_ts:
                    result["last_activity_ts"] = init_ts
                    try:
                        init_dt = datetime.fromisoformat(init_ts.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        delta_hours = (now - init_dt).total_seconds() / 3600
                        result["hours_since_activity"] = round(delta_hours, 1)
                    except (ValueError, TypeError):
                        pass

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
                    f"{stale} stale run(s) detected. "
                    f"Use trw_session_start to auto-close them."
                )
        except Exception:
            pass  # Fail-open: omit stale_count on error

        logger.info("trw_status_read", run_id=result["run_id"])
        return result

    @server.tool()
    @log_tool_call
    def trw_checkpoint(
        run_path: str | None = None,
        message: str = "",
        shard_id: str | None = None,
    ) -> dict[str, str]:
        """Save your implementation progress — if context compacts, you resume here instead of re-implementing from scratch.

        Appends an atomic snapshot to checkpoints.jsonl with timestamp. The checkpoint
        message becomes your resumption point: the next session reads it to understand
        exactly where you left off and what to work on next.

        Args:
            run_path: Path to the run directory. Auto-detects if not provided.
            message: Describe what you accomplished and what comes next — this becomes your resume point after compaction.
            shard_id: Optional shard identifier for sub-agent attribution.
        """
        resolved_path = resolve_run_path(run_path)
        meta_path = resolved_path / "meta"

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


# --- Private helpers ---


def _compute_wave_progress(
    wave_data: dict[str, object],
    run_path: Path,
) -> dict[str, object] | None:
    """Compute wave-level and shard-level progress summary.

    Args:
        wave_data: Parsed wave_manifest.yaml content.
        run_path: Path to the run directory (for reading shard manifest).

    Returns:
        Wave progress dict, or None if no waves found.
    """
    waves_raw = wave_data.get("waves", [])
    if not isinstance(waves_raw, list) or not waves_raw:
        return None

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


def _get_bundled_file(filename: str, subdir: str = "") -> str | None:
    """Load a bundled file from the package data directory.

    Args:
        filename: File to load (e.g., "framework.md", "claude_md.md").
        subdir: Optional subdirectory under data/ (e.g., "templates").

    Returns:
        File text content, or None if not found.
    """
    data_dir = Path(__file__).parent.parent / "data"
    if subdir:
        data_dir = data_dir / subdir
    file_path = data_dir / filename
    if file_path.exists():
        return file_path.read_text(encoding="utf-8")
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
        existing_versions = (
            str(existing.get("framework_version", "")),
            str(existing.get("aaref_version", "")),
            str(existing.get("trw_mcp_version", "")),
        )
        if existing_versions == (current_fw_version, current_aaref_version, current_pkg_version):
            return {"status": "up_to_date", "framework_version": current_fw_version}

        # Version mismatch — log upgrade event
        _events.log_event(trw_dir / "upgrade_events.jsonl", "framework_upgrade", {
            "old_framework": existing_versions[0],
            "new_framework": current_fw_version,
            "old_aaref": existing_versions[1],
            "new_aaref": current_aaref_version,
            "old_pkg": existing_versions[2],
            "new_pkg": current_pkg_version,
        })

    framework_files = [
        ("framework.md", "FRAMEWORK.md"),
        ("aaref.md", "AARE-F-FRAMEWORK.md"),
    ]
    for source_name, target_name in framework_files:
        content = _get_bundled_file(source_name)
        if content:
            (frameworks_dir / target_name).write_text(content, encoding="utf-8")

    version_data: dict[str, object] = {
        "framework_version": current_fw_version,
        "aaref_version": current_aaref_version,
        "trw_mcp_version": current_pkg_version,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
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

    template_data = _get_bundled_file("claude_md.md", subdir="templates")
    if template_data:
        template_path.write_text(template_data, encoding="utf-8")


def _check_framework_version_staleness(run_framework: str) -> str | None:
    """Compare run's framework version against the current deployed version.

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


def __reload_hook__() -> None:
    """Reset module-level caches on mcp-hmr hot-reload."""
    from trw_mcp.models.config import _reset_config

    global _config, _reader, _writer, _events
    _reset_config()
    _config = get_config()
    _reader = FileStateReader()
    _writer = FileStateWriter()
    _events = FileEventLogger(_writer)
