# Parent facade: state/ceremony_feedback.py
"""Ceremony escalation detection and application.

Extracted from ``ceremony_feedback.py`` to keep the facade focused on
scoring, recording, and status reporting.  All public names are
re-exported from ``ceremony_feedback.py`` so existing import paths are
preserved.

FR05: Auto-escalation on quality regression.
FR06: Human approval gate (proposal register/approve/revert).
FR09: Ceremony change history (audit trail).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.typed_dicts import EscalationResult
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)


def _derive_agent_id(run_id: str | None = None) -> str:
    """Derive agent_id using priority chain: env var > run_id > pid fallback.

    FIX-050-FR05: Replaces the previous os.environ.get("TRW_AGENT_ID", "unknown")
    pattern that always returned "unknown".

    Priority:
    1. TRW_AGENT_ID env var (explicit operator override)
    2. run_id parameter (from run context, already public in run.yaml)
    3. f"pid-{os.getpid()}" (ephemeral but non-sensitive fallback)
    """
    env_val = os.environ.get("TRW_AGENT_ID", "")
    if env_val:
        return env_val
    if run_id:
        return run_id
    return f"pid-{os.getpid()}"


def _overrides_path(trw_dir: Path) -> Path:
    """Return the path to ceremony tier overrides file."""
    return trw_dir / "context" / "ceremony-overrides.yaml"


def _history_path(trw_dir: Path) -> Path:
    """Return the path to ceremony change history file."""
    return trw_dir / "logs" / "ceremony-history.jsonl"


# --- FR05: Auto-Escalation on Quality Regression ---


def check_auto_escalation(
    task_class: str,
    feedback_data: dict[str, object],
    config: TRWConfig | None = None,
) -> EscalationResult | None:
    """Check if a task class should be auto-escalated due to quality regression.

    Returns escalation dict or None.
    """
    from trw_mcp.state.ceremony_feedback import _float_field, _get_class_sessions

    if config is None:
        config = get_config()

    window = config.ceremony_feedback_escalation_window
    threshold = config.ceremony_feedback_escalation_threshold

    sessions = _get_class_sessions(task_class, feedback_data)
    if len(sessions) < window:
        return None

    recent = sessions[-window:]
    scores = [_float_field(s, "ceremony_score") for s in recent]

    # FIX-051-FR04: Guard against corrupted all-zero score history.
    # When ALL window scores are exactly 0.0, this indicates missing/corrupt data
    # (e.g., session_start events written to session-events.jsonl instead of
    # events.jsonl), not genuine low ceremony compliance. Skip escalation.
    if all(s == 0.0 for s in scores):
        logger.debug(
            "auto_escalation_skipped_zero_scores",
            task_class=task_class,
            window=window,
        )
        return None

    if all(s < threshold for s in scores):
        current_tier = str(recent[-1].get("current_tier", "STANDARD")).upper()
        if current_tier == "COMPREHENSIVE":
            return None  # Already at max

        return {
            "triggered": True,
            "new_tier": "COMPREHENSIVE",
            "from_tier": current_tier,
            "reason": "avg_score_below_threshold",
            "window_scores": scores,
            "threshold": threshold,
        }

    return None


def apply_auto_escalation(
    trw_dir: Path,
    task_class: str,
    escalation: dict[str, object],
) -> None:
    """Apply auto-escalation by writing override and audit entry."""
    writer = FileStateWriter()
    # Write override
    overrides = read_overrides(trw_dir)
    overrides[task_class] = str(escalation.get("new_tier", "COMPREHENSIVE"))
    writer.ensure_dir(_overrides_path(trw_dir).parent)
    writer.write_yaml(_overrides_path(trw_dir), overrides)

    # Log to ceremony-history.jsonl
    _log_ceremony_change(
        trw_dir=trw_dir,
        task_class=task_class,
        from_tier=str(escalation.get("from_tier", "STANDARD")),
        new_tier=str(escalation.get("new_tier", "COMPREHENSIVE")),
        triggered_by="auto_escalation",
        proposal_id=None,
        agent_id=_derive_agent_id(),
    )


# --- FR06: Human Approval Gate ---

# Pending proposals stored in memory (session-scoped)
_pending_proposals: dict[str, dict[str, object]] = {}


def register_proposal(proposal: dict[str, object]) -> None:
    """Register a pending proposal for approval."""
    pid = str(proposal.get("proposal_id", ""))
    if pid:
        _pending_proposals[pid] = proposal


def approve_proposal(trw_dir: Path, proposal_id: str) -> dict[str, object]:
    """Approve a pending ceremony proposal.

    Raises ValueError if proposal not found.
    """
    if proposal_id not in _pending_proposals:
        raise ValueError(f"No pending proposal with id: {proposal_id}")

    writer = FileStateWriter()
    proposal = _pending_proposals.pop(proposal_id)
    task_class = str(proposal.get("task_class", ""))
    new_tier = str(proposal.get("to_tier", ""))
    from_tier = str(proposal.get("from_tier", ""))

    # Write override
    overrides = read_overrides(trw_dir)
    overrides[task_class] = new_tier
    writer.ensure_dir(_overrides_path(trw_dir).parent)
    writer.write_yaml(_overrides_path(trw_dir), overrides)

    # Log
    change_id = f"chg-{uuid.uuid4().hex[:12]}"
    _log_ceremony_change(
        trw_dir=trw_dir,
        task_class=task_class,
        from_tier=from_tier,
        new_tier=new_tier,
        triggered_by="human_approved",
        proposal_id=proposal_id,
        agent_id=_derive_agent_id(),
        change_id=change_id,
    )

    return {
        "status": "approved",
        "change_id": change_id,
        "task_class": task_class,
        "new_tier": new_tier,
    }


def revert_change(trw_dir: Path, change_id: str) -> dict[str, object]:
    """Revert a ceremony change by change_id.

    Reads history to find the original tier and restores it.
    """
    history = read_ceremony_history(trw_dir)
    original_entry = None
    for entry in history:
        if entry.get("change_id") == change_id:
            original_entry = entry
            break

    if original_entry is None:
        raise ValueError(f"No change found with id: {change_id}")

    task_class = str(original_entry.get("task_class", ""))
    from_tier = str(original_entry.get("new_tier", ""))
    to_tier = str(original_entry.get("from_tier", ""))

    # Remove or restore override
    writer = FileStateWriter()
    overrides = read_overrides(trw_dir)
    if task_class in overrides:
        if to_tier:
            overrides[task_class] = to_tier
        else:
            del overrides[task_class]
    writer.write_yaml(_overrides_path(trw_dir), overrides)

    _log_ceremony_change(
        trw_dir=trw_dir,
        task_class=task_class,
        from_tier=from_tier,
        new_tier=to_tier,
        triggered_by="human_reverted",
        proposal_id=str(original_entry.get("proposal_id")),
        agent_id=_derive_agent_id(),
    )

    return {"status": "reverted", "task_class": task_class, "restored_tier": to_tier}


def read_overrides(trw_dir: Path) -> dict[str, object]:
    """Read ceremony tier overrides."""
    reader = FileStateReader()
    path = _overrides_path(trw_dir)
    if not path.exists():
        return {}
    try:
        data = reader.read_yaml(path)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# --- FR09: Ceremony Change History ---


def _log_ceremony_change(
    trw_dir: Path,
    task_class: str,
    from_tier: str,
    new_tier: str,
    triggered_by: str,
    proposal_id: str | None,
    agent_id: str,
    change_id: str | None = None,
) -> None:
    """Log ceremony tier change to audit trail."""
    writer = FileStateWriter()
    path = _history_path(trw_dir)
    writer.ensure_dir(path.parent)

    entry: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "change_id": change_id or f"chg-{uuid.uuid4().hex[:12]}",
        "task_class": task_class,
        "from_tier": from_tier,
        "new_tier": new_tier,
        "triggered_by": triggered_by,
        "proposal_id": proposal_id,
        "agent_id": agent_id,
    }

    writer.append_jsonl(path, entry)


def read_ceremony_history(trw_dir: Path) -> list[dict[str, object]]:
    """Read ceremony change history."""
    reader = FileStateReader()
    path = _history_path(trw_dir)
    if not path.exists():
        return []
    return reader.read_jsonl(path)
