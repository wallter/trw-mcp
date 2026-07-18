"""Typed Git commit-transaction contracts (PRD-CORE-219).

Pure schemas — the crash-safe transaction implementation lives in
``state/git_commit_transaction.py``. A commit transaction never mutates the
shared index, worktree, HEAD, or checked-out branch: it builds an isolated
candidate commit from an ownership manifest, publishes it to a namespaced
candidate ref by compare-and-swap, and emits a content-bound native-integration
handoff. Automatic integration into the checked-out branch is permanently
unsupported (a framework-private lease cannot exclude ordinary Git writers).
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

CANDIDATE_REF_NAMESPACE = "refs/trw/commit-candidates"
JOURNALS_RELATIVE_DIR = ".trw/git-transactions/journals"
PREPARED_MANIFESTS_RELATIVE_DIR = ".trw/git-transactions/prepared-manifests"

# Retention policy (bounded, typed — PRD-CORE-219-FR05).
CANDIDATE_RETENTION_DAYS = 30
TOMBSTONE_RETENTION_DAYS = 365


class TransactionState(str, Enum):
    """Journal states for one commit transaction (exact, closed set)."""

    PREPARED = "prepared"
    REVIEWED = "reviewed"
    HOOKS_PASSED = "hooks_passed"
    CANDIDATE_PUBLISHED = "candidate_published"
    HANDOFF_READY = "handoff_ready"
    CANDIDATE_ONLY = "candidate_only"
    FAILED = "failed"
    RECOVERED = "recovered"


REASON_AUTO_INTEGRATION_UNSUPPORTED = "automatic_integration_unsupported"


class OwnershipManifest(BaseModel):
    """Content-bound claim over the exact paths a transaction may commit (FR01).

    Binds ownership (paths + per-path content digests at claim time), the
    reviewed parent commit, the owning run, and evidence receipts. Validation
    fails preparation on unowned, overlapping, traversal, escape, or
    changed-since-claim paths — and is read-only by construction.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    transaction_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    repository_identity: str = ""
    checked_out_ref: str = ""
    parent_oid: str = Field(min_length=7)
    owned_paths: tuple[str, ...] = Field(min_length=1)
    pre_edit_digests: dict[str, str] = Field(default_factory=dict)
    path_digests: dict[str, str] = Field(default_factory=dict)  # "" digest = declared-absent
    evidence_receipt_ids: tuple[str, ...] = ()
    run_event_offset: int = Field(default=0, ge=0)
    claimed_at: str = ""  # observation metadata; excluded from the canonical digest

    def canonical_digest(self) -> str:
        payload = self.model_dump(mode="json", exclude={"claimed_at"})
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def candidate_ref(self) -> str:
        sanitized = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in self.run_id)
        return f"{CANDIDATE_REF_NAMESPACE}/{sanitized}/{self.transaction_id}"


class SharedStateSnapshot(BaseModel):
    """Semantic shared-checkout state captured before/after a transaction step.

    Equality of two snapshots is the NFR01 no-op proof: branch, HEAD, shared
    index digest, and porcelain status are unchanged by the helper.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    checked_out_ref: str
    head_oid: str
    shared_index_digest: str
    porcelain_status: str


class TransactionJournal(BaseModel):
    """Atomic journal record for one transaction (crash recovery, NFR03)."""

    model_config = ConfigDict(strict=True, use_enum_values=True)

    transaction_id: str
    state: TransactionState
    manifest_digest: str = ""
    parent_oid: str = ""
    candidate_oid: str = ""
    candidate_ref: str = ""
    checked_out_ref: str = ""
    shared_index_digest: str = ""
    reason: str = ""
    recovery_action: str = ""
    tombstoned: bool = False


class NativeIntegrationHandoff(BaseModel):
    """Content-bound handoff for LATER native integration under quiescence (FR06).

    The helper never integrates: it returns the immutable candidate plus the
    explicit preconditions a human/native-Git integrator must satisfy. Parent
    drift invalidates the handoff and forces a new candidate + review.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    transaction_id: str
    candidate_ref: str
    candidate_oid: str
    reviewed_parent_oid: str
    owned_paths: tuple[str, ...]
    handoff_digest: str = ""
    preconditions: tuple[str, ...] = (
        "repository writers are quiescent",
        "integration uses native git in a dedicated clean checkout",
        "candidate parent still matches the target branch tip (drift forces re-review)",
    )
