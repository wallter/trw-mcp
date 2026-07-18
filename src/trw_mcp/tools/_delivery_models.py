"""Typed delivery-operation state machine models — PRD-CORE-208 FR02.

Belongs to the ``tools/_delivery_operations.py`` facade. Closed state enums plus
frozen Pydantic records for the operation, step, queue link, recovery event, and
tombstone rows, and the closed result domains the journal API returns. All models
are strict + frozen to match the CORE-205 receipt substrate; every string field
that reaches the store is length-bounded per NFR02/NFR04.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.tools._delivery_effect_registry import ReplayClass
from trw_mcp.tools._delivery_request import DeliveryLimits


class OperationState(str, Enum):
    """Closed operation-lifecycle domain (FR02). ``succeeded`` requires proof."""

    PENDING = "pending"
    RUNNING = "running"
    CRITICAL_COMPLETE = "critical_complete"
    DEFERRED_QUEUED = "deferred_queued"
    DEFERRED_RUNNING = "deferred_running"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    INDETERMINATE = "indeterminate"
    SUCCEEDED = "succeeded"


#: Terminal operation states (§6.3): no further effect may start.
TERMINAL_OPERATION_STATES: frozenset[OperationState] = frozenset(
    {
        OperationState.SUCCEEDED,
        OperationState.FAILED,
        OperationState.CANCELLED,
    }
)

#: Positive terminal state — the only one that asserts aggregate success.
POSITIVE_TERMINAL_STATE: OperationState = OperationState.SUCCEEDED


class StepState(str, Enum):
    """Exactly the five legal step states (FR02). Skip/attach/queue are separate."""

    NOT_STARTED = "not_started"
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class StepDisposition(str, Enum):
    """Non-``succeeded`` truthful dispositions — never serialized as success."""

    NONE = "none"
    SKIPPED_BY_REQUEST = "skipped_by_request"
    SKIPPED_NO_WORK = "skipped_no_work"
    ATTACHED = "attached"
    QUEUED = "queued"
    BLOCKED = "blocked"


class QueueState(str, Enum):
    """Deferred FIFO-queue link states (FR06)."""

    QUEUED = "queued"
    ATTACHED = "attached"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"


class RecoveryAction(str, Enum):
    """Authorized ``trw_delivery_recover`` actions (§6.5)."""

    TAKEOVER_PENDING = "takeover_pending"
    RECONCILE_APPLIED = "reconcile_applied"
    RECONCILE_NOT_APPLIED = "reconcile_not_applied"
    REQUEST_CANCEL = "request_cancel"
    RUN_COMPENSATION = "run_compensation"


# --- Persisted row models ---


class StepRecord(BaseModel):
    """One effect's durable step state (FR02)."""

    model_config = ConfigDict(strict=True, frozen=True)

    effect_id: str
    state: StepState = StepState.NOT_STARTED
    disposition: StepDisposition = StepDisposition.NONE
    replay_class: ReplayClass
    attempt: int = Field(default=0, ge=0)
    proof_ref: str = Field(default="", max_length=DeliveryLimits.MAX_EVIDENCE_REF_CHARS)
    proof_digest: str = ""
    finding_code: str = ""
    updated_utc_ms: int = Field(default=0, ge=0)


class OperationRecord(BaseModel):
    """The durable operation row (FR02). ``capability_hash``/salt never surface."""

    model_config = ConfigDict(strict=True, frozen=True)

    operation_id: str
    project_scope: str
    run_identity: str = ""
    request_digest: str
    capability_salt: str = ""
    capability_hash: str = ""
    state: OperationState = OperationState.PENDING
    revision: int = Field(default=1, ge=1)
    created_utc_ms: int = Field(ge=0)
    updated_utc_ms: int = Field(ge=0)
    expiry_utc_ms: int = Field(ge=0)
    lease_owner: str = ""
    lease_pid: int = 0
    lease_expiry_utc_ms: int = 0
    attached_to_operation_id: str = ""
    terminal_utc_ms: int = 0
    caller_recoverable: bool = True


class QueueLink(BaseModel):
    """A deferred-batch queue/attach link (FR06)."""

    model_config = ConfigDict(strict=True, frozen=True)

    operation_id: str
    deferred_digest: str
    state: QueueState = QueueState.QUEUED
    enqueued_utc_ms: int = Field(ge=0)
    position: int = Field(default=0, ge=0)


class RecoveryEvent(BaseModel):
    """One append-audited recovery decision (FR04)."""

    model_config = ConfigDict(strict=True, frozen=True)

    operation_id: str
    action: RecoveryAction
    reason: str = Field(max_length=DeliveryLimits.MAX_REASON_CHARS)
    evidence_ref: str = Field(default="", max_length=DeliveryLimits.MAX_EVIDENCE_REF_CHARS)
    effect_id: str = ""
    decided_utc_ms: int = Field(ge=0)


class Tombstone(BaseModel):
    """Compacted digest-only terminal record (NFR04). Preserves reuse-reject data."""

    model_config = ConfigDict(strict=True, frozen=True)

    operation_id: str
    project_scope: str
    request_digest: str
    terminal_state: str
    findings: str = ""
    created_utc_ms: int = Field(ge=0)
    expiry_utc_ms: int = Field(ge=0)


# --- Closed API result domains ---


class ClaimStatus(str, Enum):
    CLAIMED = "claimed"
    EXISTING = "existing"
    CONFLICT = "delivery_request_conflict"
    STORE_FULL = "delivery_store_full"
    REJECTED = "rejected"


class ClaimResult(BaseModel):
    """Outcome of an FR01 claim. ``effect_calls`` proves zero-effect on conflict."""

    model_config = ConfigDict(strict=True, frozen=True)

    status: ClaimStatus
    reason_code: str
    operation_id: str = ""
    revision: int = 0
    state: OperationState = OperationState.PENDING
    effect_calls: int = 0


class RecoverStatus(str, Enum):
    OK = "ok"
    UNAUTHORIZED = "unauthorized"
    NOT_STALE = "lease_not_stale"
    CONFLICT = "request_conflict"
    STALE_REVISION = "stale_revision"
    LIVE_OWNER = "live_owner"
    NOT_FOUND = "not_found"
    REJECTED = "rejected"


class RecoverResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    status: RecoverStatus
    reason_code: str
    operation_id: str = ""
    revision: int = 0
    lease_owner: str = ""
    state: OperationState = OperationState.PENDING
    replayed_effect_ids: tuple[str, ...] = ()
    indeterminate_effect_ids: tuple[str, ...] = ()
