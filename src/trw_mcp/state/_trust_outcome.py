"""Outcome-based lifecycle trust — PRD-CORE-206 FR04/NFR01/NFR02.

Belongs to the ``state/trust.py`` facade. Re-exported there so callers keep a
single import point (``from trw_mcp.state.trust import consume_trust_outcome``).

This module owns the *outcome* half of the trust model: the closed
task-type/evidence eligibility matrix (FR04), the canonical ``trust_outcome_id``
plus receipt-set digest, and the single process-safe transaction that consumes
one eligible aggregate outcome *at most once* while incrementing ``session_count``
and ``successful_sessions`` together (NFR02).

Design invariants (never soften these):

- **Activity is not evidence.** Learning/checkpoint/edit/commit counts never
  appear here — only *validated typed receipts* (PRD-CORE-205) reach this layer.
- **Only VALID is positive.** ``ReviewReceipt`` and acceptable-failure records are
  never eligible kinds, so review-only or acceptable-failure sessions can never
  increment (FR04 "always non-positive" column).
- **One outcome, one increment.** A ``trust_outcome_id`` reused with the identical
  receipt-set digest is idempotent (no second increment); a *changed* receipt set
  for an already-consumed outcome is a conflict and never increments (FR04).
- **Fail toward no increment.** An unknown task class, a write failure, or no
  eligible fresh receipt all leave the registry byte-identical (NFR01/NFR02).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypeVar

import structlog

from trw_mcp._locking import _lock_ex, _lock_un
from trw_mcp.models._evidence_core import canonical_json
from trw_mcp.models.config import TRWConfig, get_config

logger = structlog.get_logger(__name__)

_T = TypeVar("_T")

# Registry key for the consumed-outcome ledger. Old registries without it load
# as an empty map and are atomically upgraded on the first eligible receipt
# (PRD-CORE-206 migration).
CONSUMED_IDS_KEY = "consumed_trust_outcome_ids"

# Domain-separated digest prefix so a receipt-set digest can never collide with a
# manifest/scope/plan digest even on identical byte payloads (mirrors CORE-205).
_RECEIPT_SET_DOMAIN = b"trw.core206.receipt_set.v1\x00"

# PRD-CORE-206-FR04 closed eligibility matrix: the validated receipt *kinds* that
# are eligible positive evidence per task type. ``review`` and acceptable-failure
# are deliberately absent from every set (always non-positive). ``unknown`` has no
# entry — an operator must classify the task first.
_ELIGIBLE_KINDS: dict[str, frozenset[str]] = {
    "coding": frozenset({"build", "verification"}),
    "rca": frozenset({"verification", "build"}),
    "eval": frozenset({"verification", "build"}),
    "docs": frozenset({"verification"}),
    "research": frozenset({"verification"}),
    "planning": frozenset({"verification"}),
}


# --- FR04: eligibility classifier (pure) ---


@dataclass(frozen=True)
class TrustEligibility:
    """Result of applying the closed task-type/evidence matrix (FR04)."""

    eligible: bool
    reason: str
    task_type: str
    contributing_kinds: tuple[str, ...]


def classify_trust_eligibility(task_type: str, positive_kinds: Iterable[str]) -> TrustEligibility:
    """Apply the closed matrix to a set of VALIDATED positive receipt kinds.

    ``positive_kinds`` are kinds whose typed receipts already validated to VALID
    (current binding + pass) — a naked artifact, review verdict, or acceptable
    failure never reaches this set. Returns non-eligible for an unknown task class
    or when no validated kind is eligible for the class.
    """
    allowed = _ELIGIBLE_KINDS.get(task_type)
    if allowed is None:
        return TrustEligibility(False, "unknown_task_class", task_type, ())
    eligible_kinds = tuple(sorted(frozenset(positive_kinds) & allowed))
    if not eligible_kinds:
        return TrustEligibility(False, "no_eligible_positive_receipt", task_type, ())
    return TrustEligibility(True, "eligible", task_type, eligible_kinds)


# --- FR04: canonical outcome identity + receipt-set digest ---


def compute_trust_outcome_id(project_identity: str, run_id: str | None, session_id: str | None) -> str:
    """Canonical project identity plus run ID, or effective session ID when no run.

    Receipt *contents* never change this identity (FR04): it is derived only from
    the resolved project and the run/session it aggregates.
    """
    tail = run_id or session_id
    if not project_identity or not tail:
        raise ValueError("trust_outcome_id requires a project identity and a run or session id")
    return f"{project_identity}:{tail}"


def compute_receipt_set_digest(receipt_pairs: Iterable[tuple[str, str]]) -> str:
    """Canonical SHA-256 over the complete sorted (receipt_id, digest) set (FR04).

    Order-independent and duplicate-collapsing: the same logical receipt set always
    yields the same digest, and any added/removed/changed receipt changes it. This
    digest is bound immutably to the outcome ID at consumption time.
    """
    ordered = sorted({(str(rid), str(dig)) for rid, dig in receipt_pairs})
    body = canonical_json([[rid, dig] for rid, dig in ordered])
    return hashlib.sha256(_RECEIPT_SET_DOMAIN + body).hexdigest()


# --- NFR02: atomic single-consumption transaction ---


@dataclass(frozen=True)
class TrustConsumeResult:
    """Outcome of one atomic ``consume_trust_outcome`` transaction."""

    status: Literal["incremented", "idempotent", "conflict", "write_failed"]
    reason: str
    trust_outcome_id: str
    receipt_set_digest: str
    session_count: int
    successful_sessions: int
    previous_tier: str
    new_tier: str
    transitioned: bool
    incremented: bool


def _trust_lock_path(trw_dir: Path) -> Path:
    return trw_dir / "context" / ".trust-registry.lock"


def _with_registry_lock(trw_dir: Path, fn: Callable[[], _T]) -> _T:
    """Run ``fn`` while holding an exclusive advisory lock on the registry.

    A separate lock file (never the registry itself) is used so the atomic
    temp-write + ``os.replace`` of the registry cannot invalidate the held lock's
    file description. Concurrent workers — threads with independent fds or separate
    processes — serialize through ``flock`` (NFR02).
    """
    lock_path = _trust_lock_path(trw_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        _lock_ex(fd)
        return fn()
    finally:
        try:
            _lock_un(fd)
        finally:
            os.close(fd)


def _coerce_project(registry: dict[str, object]) -> dict[str, object]:
    project = registry.get("project")
    if isinstance(project, dict):
        return project
    default: dict[str, object] = {
        "session_count": 0,
        "successful_sessions": 0,
        "last_session_at": None,
        "tier": "crawl",
    }
    return default


def _read_consumed_map(project: dict[str, object]) -> dict[str, str]:
    """Load the consumed-outcome ledger; missing/malformed loads as empty (migration)."""
    raw = project.get(CONSUMED_IDS_KEY)
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _as_int(value: object) -> int:
    return int(str(value)) if value is not None else 0


def consume_trust_outcome(
    trw_dir: Path,
    trust_outcome_id: str,
    receipt_set_digest: str,
    *,
    agent_id: str | None = None,
    config: TRWConfig | None = None,
) -> TrustConsumeResult:
    """Consume one eligible aggregate outcome at most once (FR04/NFR02).

    Under one process-safe lock this reads the registry, checks the consumed-outcome
    ledger, and — only for a fresh outcome — increments ``session_count`` and
    ``successful_sessions`` together, records the outcome→receipt-set-digest binding,
    updates the derived tier, and appends the audit transition. All counters, the
    consumption marker, and the tier land in one atomic registry write; the audit is
    appended only after that write is durable, so an injected write failure leaves
    the prior registry, ledger, and audit intact and a retry remains possible.
    """
    # Local import avoids a circular import at module load (trust.py re-exports us).
    from trw_mcp.state.trust import (
        _log_trust_transition,
        _tier_for_count,
        read_trust_registry,
        write_trust_registry,
    )

    cfg = config if config is not None else get_config()

    def _txn() -> TrustConsumeResult:
        registry = read_trust_registry(trw_dir)
        project = _coerce_project(registry)
        consumed = _read_consumed_map(project)
        old_count = _as_int(project.get("session_count", 0))
        old_succ = _as_int(project.get("successful_sessions", 0))
        old_tier = _tier_for_count(old_count, cfg)

        prior = consumed.get(trust_outcome_id)
        if prior is not None:
            if prior == receipt_set_digest:
                return TrustConsumeResult(
                    "idempotent",
                    "already_consumed_identical",
                    trust_outcome_id,
                    receipt_set_digest,
                    old_count,
                    old_succ,
                    old_tier,
                    old_tier,
                    False,
                    False,
                )
            logger.warning("trust_outcome_conflict", trust_outcome_id=trust_outcome_id)
            return TrustConsumeResult(
                "conflict",
                "receipt_set_changed_for_consumed_outcome",
                trust_outcome_id,
                receipt_set_digest,
                old_count,
                old_succ,
                old_tier,
                old_tier,
                False,
                False,
            )

        new_count = old_count + 1
        new_succ = old_succ + 1
        new_tier = _tier_for_count(new_count, cfg)
        consumed[trust_outcome_id] = receipt_set_digest
        project["session_count"] = new_count
        project["successful_sessions"] = new_succ
        project["last_session_at"] = datetime.now(timezone.utc).isoformat()
        project["tier"] = new_tier
        project[CONSUMED_IDS_KEY] = consumed
        registry["project"] = project

        try:
            write_trust_registry(trw_dir, registry)
        except OSError:
            logger.warning("trust_registry_write_failed", trust_outcome_id=trust_outcome_id, exc_info=True)
            return TrustConsumeResult(
                "write_failed",
                "registry_write_failed",
                trust_outcome_id,
                receipt_set_digest,
                old_count,
                old_succ,
                old_tier,
                old_tier,
                False,
                False,
            )

        if old_tier != new_tier:
            _log_trust_transition(
                trw_dir=trw_dir,
                agent_id=agent_id or os.environ.get("TRW_AGENT_ID", "unknown"),
                previous_tier=old_tier,
                new_tier=new_tier,
                session_count=new_count,
                boundary_crossed=(cfg.trust_crawl_boundary if new_tier == "walk" else cfg.trust_walk_boundary),
                triggered_by="trust_outcome",
            )
        return TrustConsumeResult(
            "incremented",
            "consumed",
            trust_outcome_id,
            receipt_set_digest,
            new_count,
            new_succ,
            old_tier,
            new_tier,
            old_tier != new_tier,
            True,
        )

    return _with_registry_lock(trw_dir, _txn)
