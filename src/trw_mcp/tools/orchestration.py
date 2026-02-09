"""TRW orchestration tools — init, status, phase check, wave validate, resume, checkpoint, event.

These 7 tools codify FRAMEWORK.md v18.0_TRW execution flow:
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
    Phase,
    RunState,
    RunStatus,
    ShardCard,
    ShardStatus,
    WaveEntry,
    WaveManifest,
    WaveStatus,
)
from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
from trw_mcp.tools.learning import process_outcome_for_event
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)
from trw_mcp.state.validation import (
    check_phase_exit,
    validate_wave_contracts,
)

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()
_events = FileEventLogger(_writer)


def register_orchestration_tools(server: FastMCP) -> None:
    """Register all 7 orchestration tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool()
    def trw_init(
        task_name: str,
        objective: str = "",
        config_overrides: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Bootstrap TRW run scaffolding — creates .trw/, run dirs, run.yaml, events.jsonl.

        Args:
            task_name: Name of the task (used for directory naming).
            objective: Optional objective description for the run.
            config_overrides: Optional config values to override defaults.
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

        # Create run directory structure
        task_dir = project_root / "docs" / task_name
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
            },
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

        # Copy framework snapshot if available
        framework_data = _get_bundled_framework()
        if framework_data:
            snapshot_path = run_root / "meta" / "FRAMEWORK_SNAPSHOT.md"
            snapshot_path.write_text(framework_data, encoding="utf-8")

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
        resolved_path = _resolve_run_path(run_path)
        meta_path = resolved_path / "meta"

        run_yaml_path = meta_path / "run.yaml"
        state_data = _reader.read_yaml(run_yaml_path)

        # Read wave manifest if exists
        wave_data: dict[str, object] = {}
        wave_manifest_path = meta_path / "wave_manifest.yaml"
        if _reader.exists(wave_manifest_path):
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

        logger.info("trw_status_read", run_id=result["run_id"])
        return result

    @server.tool()
    def trw_phase_check(
        phase_name: str,
        run_path: str | None = None,
    ) -> dict[str, object]:
        """Validate exit criteria for a framework phase — reports pass/fail per criterion.

        Args:
            phase_name: Phase to check (research, plan, implement, validate, review, deliver).
            run_path: Path to the run directory. Auto-detects if not provided.
        """
        try:
            phase = Phase(phase_name.lower())
        except ValueError as exc:
            valid_phases = [p.value for p in Phase]
            raise ValidationError(
                f"Invalid phase: {phase_name!r}. Valid: {valid_phases}",
                phase=phase_name,
            ) from exc

        resolved_path = _resolve_run_path(run_path)
        result = check_phase_exit(phase, resolved_path, _config)

        _events.log_event(
            resolved_path / "meta" / "events.jsonl",
            "phase_check",
            {
                "phase": phase_name,
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
        resolved_path = _resolve_run_path(run_path)

        # Read wave manifest
        wave_manifest_path = resolved_path / "meta" / "wave_manifest.yaml"
        if not _reader.exists(wave_manifest_path):
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
        resolved_path = _resolve_run_path(run_path)
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
    ) -> dict[str, str]:
        """Create atomic state snapshot — appends to checkpoints.jsonl with timestamp.

        Args:
            run_path: Path to the run directory. Auto-detects if not provided.
            message: Optional message describing the checkpoint context.
        """
        resolved_path = _resolve_run_path(run_path)
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

        # Append to checkpoints.jsonl
        checkpoints_path = meta_path / "checkpoints.jsonl"
        _writer.append_jsonl(checkpoints_path, checkpoint)

        _events.log_event(
            meta_path / "events.jsonl",
            "checkpoint",
            {"message": message},
        )

        logger.info("trw_checkpoint_created", message=message)
        return {"timestamp": ts, "status": "checkpoint_created", "message": message}

    @server.tool()
    def trw_event(
        event_type: str,
        run_path: str | None = None,
        data: dict[str, str | int | float | bool] | None = None,
    ) -> dict[str, object]:
        """Log a structured event to events.jsonl — append-only audit trail.

        Args:
            event_type: Event type identifier (e.g., "phase_enter", "shard_complete").
            run_path: Path to the run directory. Auto-detects if not provided.
            data: Additional event data as key-value pairs.
        """
        resolved_path = _resolve_run_path(run_path)
        event_data: dict[str, object] = dict(data) if data else {}

        _events.log_event(
            resolved_path / "meta" / "events.jsonl",
            event_type,
            event_data,
        )

        # Outcome correlation (PRD-CORE-004 Phase 1c) — best-effort
        q_updated: list[str] = []
        try:
            q_updated = process_outcome_for_event(event_type)
        except Exception:  # noqa: BLE001 — best-effort, never fail the event
            pass

        result: dict[str, object] = {
            "status": "event_logged",
            "event_type": event_type,
        }
        if q_updated:
            result["q_updates"] = len(q_updated)
        return result


def _resolve_run_path(run_path: str | None) -> Path:
    """Resolve a run path from explicit argument or auto-detection.

    Args:
        run_path: Explicit run path, or None for auto-detection.

    Returns:
        Resolved absolute path to the run directory.

    Raises:
        StateError: If run path cannot be determined or doesn't exist.
    """
    if run_path:
        resolved = Path(run_path).resolve()
        if not resolved.exists():
            raise StateError(
                f"Run path does not exist: {resolved}",
                path=str(resolved),
            )
        return resolved

    # Auto-detect: look for most recent run in docs/*/runs/
    project_root = resolve_project_root()
    docs_dir = project_root / "docs"
    if not docs_dir.exists():
        raise StateError(
            "Cannot auto-detect run path: docs/ directory not found",
            project_root=str(project_root),
        )

    latest_run: Path | None = None
    latest_time: float = 0.0

    for task_dir in docs_dir.iterdir():
        if not task_dir.is_dir():
            continue
        runs_dir = task_dir / "runs"
        if not runs_dir.exists():
            continue
        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            run_yaml = run_dir / "meta" / "run.yaml"
            if run_yaml.exists():
                mtime = run_yaml.stat().st_mtime
                if mtime > latest_time:
                    latest_time = mtime
                    latest_run = run_dir

    if latest_run is None:
        raise StateError(
            "No active runs found in docs/*/runs/",
            project_root=str(project_root),
        )

    return latest_run


def _get_bundled_aaref() -> str | None:
    """Load bundled AARE-F-FRAMEWORK.md from package data.

    Returns:
        AARE-F framework text content, or None if not found.
    """
    data_dir = Path(__file__).parent.parent / "data"
    aaref_path = data_dir / "aaref.md"
    if aaref_path.exists():
        return aaref_path.read_text(encoding="utf-8")
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
    framework_data = _get_bundled_framework()
    if framework_data:
        fw_path = frameworks_dir / "FRAMEWORK.md"
        fw_path.write_text(framework_data, encoding="utf-8")

    aaref_data = _get_bundled_aaref()
    if aaref_data:
        aaref_path = frameworks_dir / "AARE-F-FRAMEWORK.md"
        aaref_path.write_text(aaref_data, encoding="utf-8")

    # Write VERSION.yaml
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

    template_data = _get_bundled_template("claude_md.md")
    if template_data:
        template_path.write_text(template_data, encoding="utf-8")


def _get_bundled_framework() -> str | None:
    """Load bundled FRAMEWORK.md from package data.

    Returns:
        Framework text content, or None if not found.
    """
    # Path(__file__) = .../trw_mcp/tools/orchestration.py
    # parent.parent = .../trw_mcp/
    # data dir = .../trw_mcp/data/
    data_dir = Path(__file__).parent.parent / "data"
    framework_path = data_dir / "framework.md"
    if framework_path.exists():
        return framework_path.read_text(encoding="utf-8")

    return None
