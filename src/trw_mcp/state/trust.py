"""Progressive trust model — Crawl/Walk/Run graduated autonomy (PRD-CORE-068).

Per-project trust accumulates with successful sessions. Security-tagged
changes always require human review regardless of tier.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig

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


# --- FR03: Security-Tagged Change Override ---


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
