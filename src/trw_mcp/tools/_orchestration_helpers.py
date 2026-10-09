"""Private helpers for orchestration tools — deployment and bundled-file access.

Extracted from orchestration.py to stay under the 600-line module size gate.
Parent facade: ``trw_mcp.tools.orchestration``

Imports ``get_config`` directly from ``trw_mcp.models.config`` (not via
orchestration.py) to avoid a circular import -- orchestration.py re-exports
symbols from this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp._checkout_write import write_checkout_file
from trw_mcp.models.config import get_config
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
)
from trw_mcp.tools._orchestration_phase import (
    _check_framework_version_staleness as _check_framework_version_staleness,
)

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from trw_mcp.models.run import (
        ComplexityClass,
        ComplexityOverride,
        ComplexitySignals,
        PhaseRequirements,
    )

_events = FileEventLogger(FileStateWriter())


def _resolve_init_complexity(
    complexity_hint: str | None,
    complexity_signals: dict[str, object] | None,
) -> tuple[
    ComplexitySignals | None,
    ComplexityClass | None,
    ComplexityOverride | None,
    PhaseRequirements | None,
]:
    """Resolve complexity class/override/phase-reqs from a hint or signals.

    Extracted from ``trw_init`` (PRD-CORE-060 / PRD-CORE-134) to keep the
    orchestration facade under the 350-effective-LOC gate. A ``complexity_hint``
    takes precedence over ``complexity_signals``; when neither is supplied every
    element of the returned tuple is ``None``.
    """
    from trw_mcp.models.run import ComplexityClass, ComplexitySignals
    from trw_mcp.scoring import classify_complexity, get_phase_requirements

    parsed_signals: ComplexitySignals | None = None
    complexity_class_val: ComplexityClass | None = None
    complexity_override_val: ComplexityOverride | None = None
    phase_reqs_val: PhaseRequirements | None = None

    if complexity_hint is not None:
        hint_map = {
            "EASY": ComplexityClass.MINIMAL,
            "STANDARD": ComplexityClass.STANDARD,
            "HARD": ComplexityClass.COMPREHENSIVE,
        }
        complexity_class_val = hint_map.get(complexity_hint)
        if complexity_class_val:
            phase_reqs_val = get_phase_requirements(complexity_class_val)

    if complexity_class_val is None and complexity_signals is not None:
        parsed_signals = ComplexitySignals.model_validate(complexity_signals)
        tier, _raw, override = classify_complexity(parsed_signals)
        complexity_class_val = tier
        complexity_override_val = override
        phase_reqs_val = get_phase_requirements(tier)

    return parsed_signals, complexity_class_val, complexity_override_val, phase_reqs_val


def _scan_init_artifacts(
    writer: FileStateWriter,
    run_root: Path,
    resolved_artifacts: list[str],
    run_id: str,
) -> None:
    """Scan run artifacts for knowledge requirements and persist them (PRD-CORE-106).

    Fail-open: artifact scanning must never block ``trw_init``.
    """
    from trw_mcp.state.artifact_scanner import scan_artifacts

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


def _log_init_events(
    events_jsonl_path: Path,
    *,
    task_name: str,
    framework_version: str,
    task_type: str,
    detection_method: str,
    rationale: str,
    recall_policy: str,
    target_utc: str | None = None,
    prd_scope: list[str] | None = None,
) -> None:
    """Log the run_init, task_type_detected, and session_start boundary events for trw_init."""
    # EVIDENCE-DELETION-POLICY: the declared scope is also witnessed in the append-only log, so deleting
    # meta/run.yaml cannot undeclare it (state/_evidence_witness.recorded_scope).
    _events.log_event(
        events_jsonl_path,
        "run_init",
        {"task": task_name, "framework": framework_version, "prd_scope": list(prd_scope or [])},
    )
    if target_utc:
        # PRD-CORE-338-FR03: one time_target event, written through the same stamping writer.
        _events.log_event(events_jsonl_path, "time_target", {"target_utc": target_utc})

    # PRD-CORE-184-FR05: observability — emit a task_type_detected event so
    # eval campaigns can stratify by task type without parsing run.yaml.
    try:
        _events.log_event(
            events_jsonl_path,
            "task_type_detected",
            {
                "task_type": task_type,
                "detection_method": detection_method,
                "rationale": rationale,
                "recall_policy": recall_policy,
            },
        )
    except Exception:  # justified: fail-open, observability event must not block init
        logger.debug("task_type_detected_event_skipped", exc_info=True)

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


def _get_bundled_file(filename: str, subdir: str = "") -> str | None:
    """Load a bundled file from the package data directory.

    Args:
        filename: File to load (e.g., "framework.md", "claude_md.md").
        subdir: Optional subdirectory under data/ (e.g., "templates").

    Returns:
        File text content, or None if not found.
    """
    data_dir = (Path(__file__).parent.parent / "data").resolve()
    if subdir:
        data_dir = data_dir / subdir
    file_path = (data_dir / filename).resolve()

    # QUAL-042-FR04: Path containment — prevent traversal outside data dir
    if not file_path.is_relative_to(data_dir):
        logger.warning("bundled_file_path_traversal", filename=filename, subdir=subdir)
        return None

    if file_path.exists():
        return file_path.read_text(encoding="utf-8")
    return None


def _deploy_frameworks(trw_dir: Path) -> dict[str, str]:
    """Deploy bundled frameworks to .trw/frameworks/ as read-only references.

    Writes FRAMEWORK.md, AARE-F-FRAMEWORK.md, and VERSION.yaml.
    Skips if VERSION.yaml matches current bundled versions (idempotent).

    Args:
        trw_dir: Path to the .trw directory.

    Returns:
        Dictionary with deployment status and version info.
    """
    config = get_config()
    from trw_mcp.bootstrap._framework_modified_guard import modified_warning
    from trw_mcp.canons.registry import bundled_manifest_bytes, load_registry
    from trw_mcp.framework_integrity import repair_framework_runtime

    reader = FileStateReader()
    writer = FileStateWriter()
    frameworks_dir = trw_dir / config.frameworks_dir
    writer.ensure_dir(frameworks_dir)

    version_path = frameworks_dir / "VERSION.yaml"
    current_fw_version = config.framework_version
    current_aaref_version = config.aaref_version

    framework_source = _get_bundled_file("framework.md") or ""
    aaref_source = _get_bundled_file("aaref.md") or ""
    registry = load_registry(bundled_manifest_bytes())

    from trw_mcp import __version__ as package_version
    from trw_mcp.framework_decision import deploy_decision

    # One rule for both deploy paths: current -> write nothing; an older package -> leave the project alone;
    # otherwise deploy (an edited canon body is replaced, and said so, with its old bytes saved).
    decision = deploy_decision(
        trw_dir.parent,
        framework_source=framework_source,
        aaref_source=aaref_source,
        framework_version=current_fw_version,
        aaref_version=current_aaref_version,
        registry_digest=registry.digest,
        package_version=package_version,
    )
    if decision.stale is not None:
        newer = decision.stale
        logger.warning("framework_deploy_skipped_stale_package", running=newer.running, deployed=newer.deployed)
        return {
            "status": "skipped_stale_package",
            "running": newer.running,
            "deployed": newer.deployed,
            "nudge": newer.nudge,
        }
    if decision.action == "current":
        return {"status": "up_to_date", "framework_version": current_fw_version}
    edited = list(decision.edited)
    if edited:
        logger.warning("framework_deploy_replaces_modified_bodies", bodies=edited)

    if reader.exists(version_path):
        existing = reader.read_yaml(version_path)
        _events.log_event(
            trw_dir / "upgrade_events.jsonl",
            "framework_upgrade",
            {
                "old_framework": str(existing.get("framework_version", "")),
                "new_framework": current_fw_version,
                "old_aaref": str(existing.get("aaref_version", "")),
                "new_aaref": current_aaref_version,
            },
        )

    repair_framework_runtime(
        trw_dir.parent,
        framework_source=framework_source,
        aaref_source=aaref_source,
        framework_version=current_fw_version,
        aaref_version=current_aaref_version,
        registry_digest=registry.digest,
        package_version=package_version,
    )

    logger.info(
        "frameworks_deployed",
        framework_version=current_fw_version,
        aaref_version=current_aaref_version,
    )

    deployed = {
        "status": "deployed",
        "framework_version": current_fw_version,
        "aaref_version": current_aaref_version,
    }
    if edited:
        deployed.update(
            replaced_modified=", ".join(edited), nudge=modified_warning(edited, trw_dir.parent, decision.edited_digests)
        )
    return deployed


def _deploy_templates(trw_dir: Path) -> None:
    """Copy bundled CLAUDE.md template to .trw/templates/ if not present.

    Does NOT overwrite existing template (preserves project customizations).

    Args:
        trw_dir: Path to the .trw directory.
    """
    config = get_config()
    writer = FileStateWriter()
    templates_dir = trw_dir / config.templates_dir
    writer.ensure_dir(templates_dir)

    template_path = templates_dir / "claude_md.md"
    if template_path.exists():
        return  # Preserve project customization

    template_data = _get_bundled_file("claude_md.md", subdir="templates")
    if template_data:
        write_checkout_file(trw_dir, template_path, template_data)


def _checkout_head(project_root: Path) -> str | None:
    """HEAD of *project_root*'s checkout, or None outside git (PRD-CORE-213 FR08 base_commit).

    The PRD transition gate diffs against this, so a ``status: implemented`` edit committed
    before ``trw_deliver`` is still seen. Fail-open: no base means the gate's uncommitted-only diff.
    """
    import os
    import subprocess

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, read-only
            ["git", "-C", str(project_root), "rev-parse", "--verify", "-q", "HEAD"],  # noqa: S607
            capture_output=True, text=True, timeout=10, check=False, env={**env, "GIT_TERMINAL_PROMPT": "0"},
        )  # fmt: skip
    except (
        OSError,
        subprocess.SubprocessError,
    ):  # trw-fail-silent-allow: no git means no base; the gate falls back to the uncommitted diff and logs it
        logger.info("base_commit_unavailable", exc_info=True)
        return None
    head = done.stdout.strip()
    return head if done.returncode == 0 and head else None
