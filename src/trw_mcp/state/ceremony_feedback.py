"""Self-improving ceremony feedback loop (PRD-CORE-069).

Tracks per-task-class quality outcomes and proposes ceremony depth
adjustments when statistical conditions are met.

Facade module: delegates sanitization to ``_ceremony_sanitize`` and
escalation/approval/history to ``_ceremony_escalation``.  Re-exports
all public symbols so callers keep a single import path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.typed_dicts import CeremonyFeedbackEntry
from trw_mcp.state._ceremony_escalation import (
    _derive_agent_id as _derive_agent_id,
    _history_path as _history_path,
    _log_ceremony_change as _log_ceremony_change,
    _overrides_path as _overrides_path,
    _pending_proposals as _pending_proposals,
    apply_auto_escalation as apply_auto_escalation,
    approve_proposal as approve_proposal,
    check_auto_escalation as check_auto_escalation,
    read_ceremony_history as read_ceremony_history,
    read_overrides as read_overrides,
    register_proposal as register_proposal,
    revert_change as revert_change,
)
from trw_mcp.state._ceremony_sanitize import (
    _sanitize_flag_path as _sanitize_flag_path,
    sanitize_ceremony_feedback as sanitize_ceremony_feedback,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)


def _get_class_sessions(
    task_class: str,
    feedback_data: dict[str, object],
) -> list[dict[str, object]]:
    """Extract session list for a task class from feedback data.

    Returns empty list if the path doesn't exist or types are wrong.
    """
    task_classes = feedback_data.get("task_classes", {})
    if not isinstance(task_classes, dict):
        return []
    class_data = task_classes.get(task_class, {})
    if not isinstance(class_data, dict):
        return []
    sessions = class_data.get("sessions", [])
    if not isinstance(sessions, list):
        return []
    return sessions


def _float_field(entry: dict[str, object], key: str, default: float = 0.0) -> float:
    """Safely extract a float value from a dict with object values."""
    val = entry.get(key, default)
    return float(str(val)) if val is not None else default


# --- FR01: Task Class Classifier ---


class TaskClass(str, Enum):
    DOCUMENTATION = "documentation"
    FEATURE = "feature"
    REFACTOR = "refactor"
    SECURITY = "security"
    INFRASTRUCTURE = "infrastructure"


# Keywords must be checked in priority order: SECURITY > INFRASTRUCTURE > REFACTOR > FEATURE > DOCUMENTATION
_DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "security": [
        "security",
        "vulnerability",
        "cve",
        "xss",
        "csrf",
        "injection",
        "bypass",
        "patch auth",
        "fix auth",
        "secret",
        "encrypt",
    ],
    "infrastructure": ["deploy", "migration", "infra", "docker", "ci", "pipeline"],
    "refactor": ["refactor", "cleanup", "simplify", "rename", "extract"],
    "feature": ["feat", "feature", "add feature", "implement", "new"],
}

# Priority order for classification
_CLASS_PRIORITY = [TaskClass.SECURITY, TaskClass.INFRASTRUCTURE, TaskClass.REFACTOR, TaskClass.FEATURE]


def classify_task_class(task_name: str, task_description: str | None = None) -> TaskClass:
    """Classify a task into one of 5 task classes using keyword matching.

    Priority: SECURITY > INFRASTRUCTURE > REFACTOR > FEATURE > DOCUMENTATION (catch-all).
    """
    text = (task_name + " " + (task_description or "")).lower().strip()
    if not text.strip():
        return TaskClass.DOCUMENTATION

    config = get_config()
    keywords = getattr(config, "ceremony_feedback_class_keywords", None) or _DEFAULT_KEYWORDS

    for task_class in _CLASS_PRIORITY:
        class_keywords = keywords.get(task_class.value, [])
        for kw in class_keywords:
            if kw in text:
                return task_class

    return TaskClass.DOCUMENTATION


# --- FR02: Quality Outcome Tracker ---


def _feedback_path(trw_dir: Path) -> Path:
    return trw_dir / "context" / "ceremony-feedback.yaml"


def read_feedback_data(trw_dir: Path) -> dict[str, object]:
    """Read ceremony feedback data, creating default if missing."""
    reader = FileStateReader()
    writer = FileStateWriter()
    path = _feedback_path(trw_dir)
    if not path.exists():
        default: dict[str, object] = {"task_classes": {}}
        writer.ensure_dir(path.parent)
        writer.write_yaml(path, default)
        return default
    return reader.read_yaml(path)


def record_session_outcome(
    trw_dir: Path,
    task_name: str,
    ceremony_score: float,
    build_passed: bool,
    coverage_delta: float,
    critical_findings: int,
    mutation_score_ok: bool,
    current_tier: str,
    run_path: str,
    session_id: str,
    task_description: str = "",
) -> CeremonyFeedbackEntry:
    """Record a session outcome for ceremony feedback tracking.

    Returns the recorded entry.
    """
    task_class = classify_task_class(task_name, task_description=task_description)

    # Compute outcome_quality (FR02 formula).
    # round(..., 4) avoids IEEE 754 artifacts like 0.6000000000000001 (FIX-050-FR04).
    outcome_quality = round(
        (0.4 if build_passed else 0.0)
        + (0.2 if coverage_delta >= 0 else 0.0)
        + (0.2 if critical_findings == 0 else 0.0)
        + (0.2 if mutation_score_ok else 0.0),
        4,
    )

    entry: CeremonyFeedbackEntry = {
        "session_id": session_id,
        "run_path": run_path,
        "ceremony_score": ceremony_score,
        "outcome_quality": outcome_quality,
        "current_tier": current_tier,
        "task_name": task_name,
        "task_class": task_class.value,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

    data = read_feedback_data(trw_dir)
    task_classes = data.get("task_classes", {})
    if not isinstance(task_classes, dict):
        task_classes = {}

    class_key = task_class.value
    if class_key not in task_classes or not isinstance(task_classes[class_key], dict):
        task_classes[class_key] = {"sessions": []}

    sessions = task_classes[class_key].get("sessions", [])
    if not isinstance(sessions, list):
        sessions = []

    sessions.append(entry)

    # Prune to last 50 records
    if len(sessions) > 50:
        sessions = sessions[-50:]

    task_classes[class_key]["sessions"] = sessions
    data["task_classes"] = task_classes

    writer = FileStateWriter()
    writer.ensure_dir(_feedback_path(trw_dir).parent)
    writer.write_yaml(_feedback_path(trw_dir), data)

    return entry


# --- FR03: Statistical Significance ---


def has_sufficient_samples(
    task_class: str,
    feedback_data: dict[str, object],
    config: TRWConfig | None = None,
) -> bool:
    """Check if a task class has enough samples for proposal generation."""
    if config is None:
        config = get_config()
    sessions = _get_class_sessions(task_class, feedback_data)
    return len(sessions) >= config.ceremony_feedback_min_samples


# --- FR04: Ceremony Reduction Proposal Generator ---

_TIER_REDUCTION: dict[str, str] = {
    "COMPREHENSIVE": "STANDARD",
    "STANDARD": "MINIMAL",
}


def generate_reduction_proposal(
    task_class: str,
    feedback_data: dict[str, object],
    config: TRWConfig | None = None,
) -> dict[str, object] | None:
    """Generate a ceremony reduction proposal if conditions are met.

    Conditions: sufficient samples, avg ceremony_score > threshold,
    avg outcome_quality > threshold.
    Returns proposal dict or None.
    """
    import uuid

    if config is None:
        config = get_config()

    if not has_sufficient_samples(task_class, feedback_data, config):
        return None

    sessions = _get_class_sessions(task_class, feedback_data)
    if not sessions:
        return None

    # Use the most recent min_samples sessions
    min_samples = config.ceremony_feedback_min_samples
    recent = sessions[-min_samples:]

    avg_score = sum(_float_field(s, "ceremony_score") for s in recent) / len(recent)
    avg_quality = sum(_float_field(s, "outcome_quality") for s in recent) / len(recent)

    score_threshold = config.ceremony_feedback_score_threshold
    quality_threshold = config.ceremony_feedback_quality_threshold

    if avg_score <= score_threshold:
        return None
    if avg_quality <= quality_threshold:
        return None

    # Determine current tier from most recent session
    current_tier = str(recent[-1].get("current_tier", "STANDARD")).upper()

    # NFR02: Never reduce below MINIMAL
    if current_tier not in _TIER_REDUCTION:
        return None

    to_tier = _TIER_REDUCTION[current_tier]

    return {
        "proposal_id": f"prop-{uuid.uuid4().hex[:12]}",
        "task_class": task_class,
        "from_tier": current_tier,
        "to_tier": to_tier,
        "sample_count": len(recent),
        "avg_ceremony_score": round(avg_score, 2),
        "avg_outcome_quality": round(avg_quality, 3),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }


# --- FR08: Status tool helper ---


def get_ceremony_status(
    trw_dir: Path,
    task_class: str | None = None,
) -> dict[str, object]:
    """Get ceremony feedback status for one or all task classes."""
    config = get_config()
    feedback_data = read_feedback_data(trw_dir)

    classes_to_check: list[str | None] = [task_class] if task_class else [tc.value for tc in TaskClass]

    if task_class and task_class not in [tc.value for tc in TaskClass]:
        raise ValueError(f"Invalid task_class '{task_class}'. Valid: {[tc.value for tc in TaskClass]}")

    results: list[dict[str, object]] = []
    for tc in classes_to_check:
        tc_str = str(tc)
        # Pass trw_dir so disk-persisted proposals from the deferred thread are surfaced.
        tc_data = _get_class_status(tc_str, feedback_data, config, trw_dir=trw_dir)
        results.append(tc_data)

    return {"task_classes": results}


def _get_class_status(
    task_class: str,
    feedback_data: dict[str, object],
    config: TRWConfig,
    trw_dir: Path | None = None,
) -> dict[str, object]:
    """Get status for a single task class."""
    sessions = _get_class_sessions(task_class, feedback_data)

    avg_score = sum(_float_field(s, "ceremony_score") for s in sessions) / len(sessions) if sessions else None
    avg_quality = sum(_float_field(s, "outcome_quality") for s in sessions) / len(sessions) if sessions else None

    current_tier = str(sessions[-1].get("current_tier", "STANDARD")) if sessions else "STANDARD"

    # Check for proposals -- merge in-memory with any persisted to disk
    # (FIX-051-FR03: deferred thread writes proposals to disk; main thread reads them here).
    proposals: list[dict[str, object]] = []
    proposal = generate_reduction_proposal(task_class, feedback_data, config)
    if proposal:
        register_proposal(proposal)
        proposals.append(proposal)

    # Also surface any disk-persisted proposals from the deferred delivery thread.
    if trw_dir is not None:
        disk_overrides = read_overrides(trw_dir)
        disk_proposals = disk_overrides.get("_pending_proposals", {})
        if isinstance(disk_proposals, dict):
            for pid, disk_prop in disk_proposals.items():
                if not isinstance(disk_prop, dict):
                    continue
                if str(disk_prop.get("task_class", "")) != task_class:
                    continue
                # Don't duplicate if already in _pending_proposals memory
                if pid not in _pending_proposals:
                    register_proposal(disk_prop)
                    proposals.append(disk_prop)

    # Check for escalation
    escalation = check_auto_escalation(task_class, feedback_data, config)

    # Warnings
    warnings: list[str] = []
    if not has_sufficient_samples(task_class, feedback_data, config):
        warnings.append("insufficient_samples")

    return {
        "task_class": task_class,
        "current_tier": current_tier,
        "session_count": len(sessions),
        "avg_ceremony_score": round(avg_score, 2) if avg_score is not None else None,
        "avg_outcome_quality": round(avg_quality, 3) if avg_quality is not None else None,
        "proposals": proposals,
        "auto_escalation": escalation,
        "warnings": warnings,
    }
