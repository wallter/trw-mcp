"""Progressive trust model — Crawl/Walk/Run graduated autonomy (PRD-CORE-068).

Per-project trust accumulates with successful sessions. Security-tagged
changes always require human review regardless of tier.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.models.typed_dicts import HumanReviewResult, TrustLevelResult
from trw_mcp.models.typed_dicts._trust import ApprovalControlMapResult

# PRD-CORE-206: outcome-based lifecycle trust. The eligibility matrix + atomic
# one-time consumption live in focused siblings (kept under the 350 effective-LOC
# gate); callers import them from this facade. Safe to import at module top: the
# siblings never import trust.py at module load (consume_trust_outcome resolves
# trust.py helpers function-locally), so there is no import cycle.
from trw_mcp.state._trust_outcome import (
    CONSUMED_IDS_KEY as CONSUMED_IDS_KEY,
)
from trw_mcp.state._trust_outcome import (
    TrustConsumeResult as TrustConsumeResult,
)
from trw_mcp.state._trust_outcome import (
    TrustEligibility as TrustEligibility,
)
from trw_mcp.state._trust_outcome import (
    classify_trust_eligibility as classify_trust_eligibility,
)
from trw_mcp.state._trust_outcome import (
    compute_receipt_set_digest as compute_receipt_set_digest,
)
from trw_mcp.state._trust_outcome import (
    compute_trust_outcome_id as compute_trust_outcome_id,
)
from trw_mcp.state._trust_outcome import (
    consume_trust_outcome as consume_trust_outcome,
)
from trw_mcp.state._trust_receipts import (
    collect_positive_trust_evidence as collect_positive_trust_evidence,
)
from trw_mcp.state._trust_receipts import (
    evaluate_and_consume_trust_outcome as evaluate_and_consume_trust_outcome,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)


# --- FR01: Trust Registry ---


def _registry_path(trw_dir: Path) -> Path:
    return trw_dir / "context" / "trust-registry.yaml"


def _audit_log_path(trw_dir: Path) -> Path:
    return trw_dir / "logs" / "trust-audit.jsonl"


def read_trust_registry(trw_dir: Path) -> dict[str, object]:
    """Read trust registry, creating default if missing."""
    reader = FileStateReader()
    writer = FileStateWriter()
    path = _registry_path(trw_dir)
    if not path.exists():
        default: dict[str, object] = {
            "project": {
                "session_count": 0,
                "successful_sessions": 0,
                "last_session_at": None,
                "tier": "crawl",
                # PRD-CORE-206: outcome→receipt-set-digest ledger for one-time
                # consumption. Old registries without it load as an empty map.
                "consumed_trust_outcome_ids": {},
            }
        }
        writer.ensure_dir(path.parent)
        writer.write_yaml(path, default)
        return default
    return reader.read_yaml(path)


def write_trust_registry(trw_dir: Path, data: dict[str, object]) -> None:
    """Write trust registry atomically."""
    writer = FileStateWriter()
    path = _registry_path(trw_dir)
    writer.ensure_dir(path.parent)
    writer.write_yaml(path, data)


# --- FR02: Trust Level Calculation ---


def trust_level_calculate(trw_dir: Path, config: TRWConfig | None = None) -> TrustLevelResult:
    """Calculate current trust tier from session count.

    Returns dict with: tier, session_count, review_mode, review_sample_rate,
    locked, lock_reason.
    """
    if config is None:
        config = get_config()

    registry = read_trust_registry(trw_dir)
    project = registry.get("project", {})
    if not isinstance(project, dict):
        project = {}
    session_count = int(project.get("session_count", 0))

    # FR08: Admin lock overrides everything
    if config.trust_locked:
        return {
            "tier": "crawl",
            "session_count": session_count,
            "review_mode": "mandatory",
            "review_sample_rate": 1.0,
            "locked": True,
            "lock_reason": "admin_override",
        }

    # FR02: Tier assignment from boundaries
    crawl_boundary = config.trust_crawl_boundary
    walk_boundary = config.trust_walk_boundary

    if session_count <= crawl_boundary:
        tier = "crawl"
        review_mode = "mandatory"
        review_sample_rate: float | None = 1.0
    elif session_count <= walk_boundary:
        tier = "walk"
        review_mode = "sampled"
        review_sample_rate = config.trust_walk_sample_rate
    else:
        tier = "run"
        review_mode = "risk_based"
        review_sample_rate = None

    return {
        "tier": tier,
        "session_count": session_count,
        "review_mode": review_mode,
        "review_sample_rate": review_sample_rate,
        "locked": False,
        "lock_reason": None,
    }


# --- FR03: Security-Tagged Change Override ---


def requires_human_review(
    security_tags: list[str],
    changed_files: list[str],
    trust_result: dict[str, object],
    config: TRWConfig | None = None,
) -> HumanReviewResult:
    """Determine if a change requires human review.

    Security-tagged changes ALWAYS require review regardless of tier.
    """
    if config is None:
        config = get_config()

    tier = str(trust_result.get("tier", "crawl"))

    # Security tag override
    config_security_tags = set(config.trust_security_tags)
    if any(tag in config_security_tags for tag in security_tags):
        return {
            "required": True,
            "reason": "security_tagged",
            "override_tier": True,
        }

    # Tier-based review
    if tier == "crawl":
        return {"required": True, "reason": "crawl_mandatory", "override_tier": False}
    if tier == "walk":
        return {"required": True, "reason": "sampled_review", "override_tier": False}
    # run — Risk-based: check changed files for risk patterns
    risk_patterns = (
        "auth",
        "secret",
        "permission",
        "encrypt",
        "password",
        "token",
        "key",
    )
    has_risk = any(any(p in f.lower() for p in risk_patterns) for f in changed_files)
    if has_risk:
        return {
            "required": True,
            "reason": "risk_based_file_pattern",
            "override_tier": False,
        }
    return {"required": False, "reason": "risk_based", "override_tier": False}


def approval_control_map() -> ApprovalControlMapResult:
    """Map internal approval primitives without claiming external compliance."""
    return {
        "compliance_claim": "none",
        "non_compliance_boundary": "These are operator approval controls, not a SOC 2 attestation or certification.",
        "operator_diagnostics": (
            "approval controls require project-specific compliance review before external claims",
        ),
        "controls": {
            "trust_registry": {
                "purpose": "graduated review mode by successful session count",
                "code_path": "trw_mcp.state.trust.trust_level_calculate",
            },
            "human_review_gate": {
                "purpose": "force approval for crawl tier, sampled walk tier, and risk/security changes",
                "code_path": "trw_mcp.state.trust.requires_human_review",
            },
            "ceremony_proposals": {
                "purpose": "register/approve/revert ceremony tier changes with audit history",
                "code_path": "trw_mcp.state._ceremony_escalation.approve_proposal",
            },
        },
    }


def _tier_for_count(count: int, config: TRWConfig) -> str:
    crawl_boundary = config.trust_crawl_boundary
    walk_boundary = config.trust_walk_boundary
    if count <= crawl_boundary:
        return "crawl"
    if count <= walk_boundary:
        return "walk"
    return "run"


# --- FR07: Trust Transition Audit Log ---


def _log_trust_transition(
    trw_dir: Path,
    agent_id: str,
    previous_tier: str,
    new_tier: str,
    session_count: int,
    boundary_crossed: int,
    triggered_by: str,
) -> None:
    """Append immutable audit entry for tier transition (SOC 2 CC8)."""
    writer = FileStateWriter()
    path = _audit_log_path(trw_dir)
    writer.ensure_dir(path.parent)

    entry: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": agent_id,
        "previous_tier": previous_tier,
        "new_tier": new_tier,
        "session_count": session_count,
        "boundary_crossed": boundary_crossed,
        "triggered_by": triggered_by,
    }

    writer.append_jsonl(path, entry)
    logger.info(
        "trust_transition_logged",
        previous_tier=previous_tier,
        new_tier=new_tier,
        session_count=session_count,
    )


def read_audit_log(trw_dir: Path) -> list[dict[str, object]]:
    """Read all trust audit log entries."""
    reader = FileStateReader()
    path = _audit_log_path(trw_dir)
    if not path.exists():
        return []
    return reader.read_jsonl(path, strict=True)
