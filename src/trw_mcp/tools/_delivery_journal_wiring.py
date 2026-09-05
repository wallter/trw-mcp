"""Live delivery-journal wiring for the ``trw_deliver`` path — PRD-CORE-208.

Belongs to the ``tools/_ceremony_deliver_tool.py`` facade. Bridges the durable
:class:`~trw_mcp.tools._delivery_operations.DeliveryCoordinator` substrate into
the REAL ``run_trw_deliver`` critical path so the journal owns delivery, not a
shadow:

- **FR01** — claims a caller-stable operation and binds it to one canonical
  request digest BEFORE the first delivery mutation. An explicit ``delivery_id``
  whose bound request differs from a prior claim returns
  ``delivery_request_conflict`` with zero delivery effects.
- **FR02** — commits a ``started`` transition before each synchronous effect and
  a terminal transition after it, so a killed deliver leaves a ``started`` step
  rather than ``not_started``. PRD-FIX-127 FR01/FR02 gave that row a consumer:
  the ``resume`` recovery action classifies it, and a granted resume reopens this
  journal in RESUME MODE, where :meth:`DeliverJournal.step` yields ``False`` for
  every step already durably ``succeeded`` so only the never-started ones run.
- **FR06** — records the deferred batch digest so a later different-ID delivery
  attaches or durably FIFO-queues.
- **NFR01** — a legacy no-``delivery_id`` call generates a fresh server-side
  UUIDv7 each time, so it is journaled but reports ``caller_recoverable=false``
  (a lost response can never be recovered by caller identity).

A repeat claim under a non-terminal operation is still a zero-effect refusal
UNLESS this process holds a valid resume grant for it; the refusal now names
``resume`` as the action that changes that.

Gated by ``delivery_operations_mode``: ``off`` is an explicit rollback switch;
``observe`` journals diagnostics fail-open; the default ``enforce`` mode blocks
journal/claim failures and request conflicts before any effect. Calls without a
caller ID receive a server ID but remain explicitly non-recoverable by the caller.
"""

from __future__ import annotations

import os
import secrets
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp._delivery_boundary import open_boundary, take_refusal
from trw_mcp.models._evidence_core import domain_digest
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import DeliverResultDict
from trw_mcp.tools._delivery_effect_registry import ALWAYS_REEVALUATE_EFFECTS
from trw_mcp.tools._delivery_models import (
    DELIVERY_JOURNAL_OWNER,
    TERMINAL_OPERATION_STATES,
    ClaimResult,
    ClaimStatus,
    OperationState,
    StepDisposition,
    StepRecord,
    StepState,
)
from trw_mcp.tools._delivery_operations import DeliveryCoordinator
from trw_mcp.tools._delivery_resume_action import latest_resume_grant

logger = structlog.get_logger(__name__)

_JOURNAL_OWNER = DELIVERY_JOURNAL_OWNER
#: Server-generated recovery capability entropy for legacy no-ID claims (NFR01).
#: 32 bytes = 256 bits, well over the 128-bit floor enforced at claim time.
_SERVER_CAPABILITY_BYTES = 32


def _gen_uuid7(now_ms: int | None = None) -> str:
    """Generate a canonical UUIDv7 (stdlib has none on 3.12) for legacy claims."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    ts = now_ms & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (ts << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))


def compute_deferred_digest(*, run_identity: str, skip_index_sync: bool, deferred_steps: tuple[str, ...]) -> str:
    """Canonical FR06 deferred-work digest (selected steps + run + skip flags).

    Two deliveries attach only when this digest is equal; a different selected
    step set / run / skip flag yields a distinct digest and a durable FIFO queue
    entry instead of a false attach.
    """
    return domain_digest(
        "core208.deferred",
        {
            "run_identity": run_identity or "",
            "skip_index_sync": bool(skip_index_sync),
            "steps": list(deferred_steps),
        },
    )


class DeliverJournal:
    """A mode-aware per-delivery journal handle over one claimed operation.

    When :attr:`enabled` is ``False`` every method is a no-op, so the caller
    wraps effects unconditionally without a mode/branch at each call site. All
    In ``observe`` mode journal I/O logs and remains fail-open. In ``enforce``
    mode the same errors raise or return a zero-effect blocked result.
    """

    def __init__(
        self,
        *,
        coordinator: DeliveryCoordinator | None = None,
        operation_id: str = "",
        caller_recoverable: bool = True,
        mode: str = "off",
        resume_steps: dict[str, StepRecord] | None = None,
    ) -> None:
        self._coordinator = coordinator
        self.operation_id = operation_id
        self.caller_recoverable = caller_recoverable
        self.mode = mode
        self.journaled_effects: set[str] = set()
        self.skipped_effects: set[str] = set()
        #: Pre-resume durable step rows, captured once under the grant lock. Empty
        #: (and ``resume_mode`` False) on a normal first delivery.
        self._resume_steps: dict[str, StepRecord] = resume_steps or {}
        self.resume_mode = resume_steps is not None

    @property
    def enabled(self) -> bool:
        return self._coordinator is not None and bool(self.operation_id)

    @contextmanager
    def step(self, effect_id: str) -> Iterator[bool]:
        """Open a crash boundary; yield whether the wrapped effect must RUN (FR02).

        ``False`` means resume mode found the step already durably ``succeeded``:
        it is recorded with disposition ``skipped_no_work``, no ``begin_step`` runs
        and the attempt counter is untouched, so a resumed delivery duplicates no
        effect. ``True`` (always, outside resume mode) means run it.
        An empty ``effect_id`` is an unjournaled call site and always runs.
        """
        if not effect_id:
            yield True
            return
        if self._skip_completed(effect_id):
            yield False
            return
        started = self._begin(effect_id)
        failed = False
        try:
            with open_boundary(effect_id):
                yield True
        except BaseException:
            failed = True
            raise
        finally:
            # A decision-shaped effect reports refusal by RETURN VALUE, so
            # "the call did not raise" is not evidence that it succeeded.
            refusal = take_refusal(effect_id)
            if started:
                self._finalize(
                    effect_id,
                    StepState.FAILED if (failed or refusal) else StepState.SUCCEEDED,
                    finding_code=refusal,
                )

    def _skip_completed(self, effect_id: str) -> bool:
        """True iff resume mode proves this step already succeeded (FR02).

        A ``failed`` step is NOT re-run either — it keeps the operation on a failed
        terminal path and is settled by explicit reconciliation (OQ-002) — but it
        is also not recorded as skipped work, so it stays visible as a failure.
        """
        if effect_id in ALWAYS_REEVALUATE_EFFECTS:
            # A governance verdict is re-decided on every attempt. Inheriting a
            # prior `succeeded` row here would let a resumed delivery act on an
            # override that was never re-validated and never re-ledgered.
            return False
        prior = self._resume_steps.get(effect_id)
        if prior is None or self._coordinator is None:
            return False
        if prior.state is StepState.FAILED:
            # `reconcile_not_applied` is the operator's PROOF that the effect did
            # not land (PRD-CORE-208 FR04); re-running it is the whole point of
            # that action, and `begin_step` already records it as attempt 2. Any
            # other failure means the wrapped statement raised, so the effect may
            # have partially applied and is not re-run (OQ-002).
            return prior.finding_code != "confirmed_not_applied"
        if prior.state is not StepState.SUCCEEDED:
            return False
        self.skipped_effects.add(effect_id)
        try:
            # proof_ref/proof_digest are no longer explicit args here: finalize_step
            # (PRD-FIX-127 OQ-005) now carries forward whatever is already durably
            # recorded for this step -- exactly `prior`'s values, since `prior` IS
            # the record finalize_step re-reads -- so forwarding them by hand here
            # was redundant plumbing that only worked as long as every call site
            # remembered to do it.
            self._coordinator.finalize_step(
                self.operation_id,
                effect_id,
                state=StepState.SUCCEEDED,
                disposition=StepDisposition.SKIPPED_NO_WORK,
                finding_code=prior.finding_code,
            )
        except Exception:
            logger.debug("delivery_journal_skip_record_failed", effect_id=effect_id, exc_info=True)
            if self.mode == "enforce":
                raise
        return True

    def _begin(self, effect_id: str) -> bool:
        if not self.enabled or self._coordinator is None:
            return False
        try:
            self._coordinator.begin_step(self.operation_id, effect_id, owner=_JOURNAL_OWNER, pid=os.getpid())
            self.journaled_effects.add(effect_id)
            return True
        except Exception:
            logger.debug("delivery_journal_begin_failed", effect_id=effect_id, exc_info=True)
            if self.mode == "enforce":
                raise
            return False

    def _finalize(self, effect_id: str, state: StepState, finding_code: str = "") -> None:
        if self._coordinator is None:
            return
        try:
            self._coordinator.finalize_step(self.operation_id, effect_id, state=state, finding_code=finding_code)
        except Exception:
            logger.debug("delivery_journal_finalize_failed", effect_id=effect_id, exc_info=True)
            if self.mode == "enforce":
                raise

    def mark_state(self, state: OperationState) -> None:
        """Commit an operation-level milestone/terminal transition (FR02)."""
        if not self.enabled or self._coordinator is None:
            return
        try:
            self._coordinator.mark_operation_state(self.operation_id, state)
        except Exception:
            logger.debug("delivery_journal_state_failed", state=state.value, exc_info=True)
            if self.mode == "enforce":
                raise

    def enqueue_deferred(self, digest: str) -> None:
        """Attach-or-FIFO-queue the deferred batch for this operation (FR06)."""
        if not self.enabled or self._coordinator is None:
            return
        try:
            self._coordinator.enqueue_deferred(self.operation_id, digest)
        except Exception:
            logger.debug("delivery_journal_enqueue_failed", exc_info=True)
            if self.mode == "enforce":
                raise

    def wait_for_step_terminal(self, effect_id: str, *, timeout_seconds: float = 30.0) -> bool:
        """Wait until a post-launch synchronous effect is durably terminal.

        The deferred worker starts before S18/S20 complete so it can report a
        real launch result in the response.  It must not terminalize the whole
        operation first.  This bounded rendezvous preserves that ordering
        without holding a database transaction or delivery lock.
        """
        if not self.enabled or self._coordinator is None:
            return True
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = self._coordinator.project_status(self.operation_id)
            steps = status.get("steps")
            row = steps.get(effect_id) if isinstance(steps, dict) else None
            state = row.get("state") if isinstance(row, dict) else None
            if state not in (None, StepState.NOT_STARTED.value, StepState.STARTED.value):
                return True
            time.sleep(0.01)
        return False

    def summary(self) -> dict[str, object]:
        """Compact, redaction-safe projection for the deliver result payload."""
        return {
            "operation_id": self.operation_id,
            "caller_recoverable": self.caller_recoverable,
            "mode": self.mode,
            "enabled": self.enabled,
            "journaled_effect_count": len(self.journaled_effects),
            "resume_mode": self.resume_mode,
            "skipped_effect_count": len(self.skipped_effects),
        }


def open_delivery_journal(
    trw_dir: Path,
    config: TRWConfig,
    *,
    run_identity: str,
    skip_reflect: bool,
    skip_index_sync: bool,
    allow_unverified: bool,
    acceptable_failure_digest: str = "",
    delivery_id: str = "",
    capability_token: str = "",
) -> tuple[DeliverJournal, DeliverResultDict | None]:
    """Claim a caller-stable operation before the first delivery mutation (FR01).

    Returns ``(journal, block_result)``. In enforce mode any claim conflict,
    rejection, or journal failure returns a zero-effect block. In observe mode,
    only an explicit caller-ID conflict blocks; diagnostic failures remain open.
    """
    # getattr-guarded test doubles still resolve to the production-safe default.
    mode = getattr(config, "delivery_operations_mode", "enforce")
    if mode == "off":
        return DeliverJournal(mode="off"), None

    explicit = bool(delivery_id)
    try:
        coordinator = DeliveryCoordinator(trw_dir, config=config)
    except Exception:
        logger.debug("delivery_journal_open_failed", exc_info=True)
        if mode == "enforce":
            return DeliverJournal(mode=mode), _blocked_result(
                delivery_id, "delivery_journal_error", "delivery journal unavailable"
            )
        return DeliverJournal(mode=mode), None

    if explicit:
        capability = capability_token
        caller_recoverable = True
    else:
        delivery_id = _gen_uuid7()
        capability = secrets.token_hex(_SERVER_CAPABILITY_BYTES)
        caller_recoverable = False

    try:
        result = coordinator.claim(
            delivery_id=delivery_id,
            capability_token=capability,
            run_identity=run_identity,
            skip_reflect=skip_reflect,
            skip_index_sync=skip_index_sync,
            allow_unverified=allow_unverified,
            acceptable_failure_digest=acceptable_failure_digest,
            owner=_JOURNAL_OWNER,
            pid=os.getpid(),
        )
    except Exception:  # observe is diagnostic; enforce returns a zero-effect block
        logger.debug("delivery_journal_claim_failed", exc_info=True)
        if explicit:
            return DeliverJournal(mode=mode), _blocked_result(
                delivery_id, "delivery_journal_error", "delivery journal claim failed"
            )
        return DeliverJournal(mode=mode), None

    resume_steps: dict[str, StepRecord] | None = None
    if result.status is ClaimStatus.EXISTING and mode == "enforce":
        resume_steps = read_resume_grant(coordinator, result.operation_id)
        if resume_steps is None:
            return DeliverJournal(mode=mode), _existing_operation_refusal(result, delivery_id)
        logger.info(
            "delivery_resume_mode_entered",
            operation_id=result.operation_id,
            already_succeeded=sorted(eid for eid, row in resume_steps.items() if row.state is StepState.SUCCEEDED),
        )
    if result.status in (ClaimStatus.CLAIMED, ClaimStatus.EXISTING):
        return (
            DeliverJournal(
                coordinator=coordinator,
                operation_id=result.operation_id,
                caller_recoverable=caller_recoverable,
                mode=mode,
                resume_steps=resume_steps,
            ),
            None,
        )

    # Conflict / rejected / store_full. Explicit IDs always see the refusal;
    # enforce mode also refuses server-ID calls rather than running unjournaled.
    if explicit or mode == "enforce":
        return DeliverJournal(mode=mode), _conflict_result(result, delivery_id)
    logger.debug("delivery_journal_legacy_claim_non_success", status=result.status.value)
    return DeliverJournal(mode=mode), None


def open_deferred_journal(trw_dir: Path, operation_id: str) -> DeliverJournal:
    """Open a journal handle for the background deferred batch (FR02/FR06).

    Runs in the deferred daemon thread over the SAME already-claimed operation, so
    each roster step commits a ``started`` transition before it runs and a
    terminal transition after — a process death mid-batch leaves e.g. the
    NON_REPLAYABLE trust step ``started``, which the ``resume`` action classifies
    as ``indeterminate`` and refuses to replay until an operator reconciles it.
    When this process already holds a resume grant the handle opens in resume mode,
    so the roster skips the steps a prior attempt already completed. Fully
    fail-open: any failure returns a disabled (no-op) handle.
    """
    try:
        from trw_mcp.models.config import get_config

        config = get_config()
    except Exception:
        raise RuntimeError("delivery operations config unavailable during deferred enforcement") from None
    mode = getattr(config, "delivery_operations_mode", "enforce")
    if not operation_id or mode == "off":
        return DeliverJournal(mode=mode)
    try:
        coordinator = DeliveryCoordinator(trw_dir, config=config)
    except Exception:
        logger.debug("deferred_journal_open_failed", exc_info=True)
        if mode == "enforce":
            raise
        return DeliverJournal(mode=mode)
    return DeliverJournal(
        coordinator=coordinator,
        operation_id=operation_id,
        caller_recoverable=False,
        mode=mode,
        resume_steps=read_resume_grant(coordinator, operation_id),
    )


def read_resume_grant(coordinator: DeliveryCoordinator, operation_id: str) -> dict[str, StepRecord] | None:
    """Return the pre-resume step rows iff THIS process holds a live resume grant.

    FR02/NFR01: the operation, its latest recovery event, and its steps are read in
    ONE ``BEGIN IMMEDIATE`` transaction, so a concurrent grant/terminal transition
    cannot be observed half-applied. ``None`` means "no grant" and keeps today's
    zero-effect refusal. The grant is bound to the delivery journal owner AND the
    calling process id AND an unexpired lease, so it cannot be consumed by another
    caller and cannot be banked indefinitely.
    """
    try:
        conn = coordinator.store.connect()
    except Exception:
        logger.debug("delivery_resume_grant_unreadable", exc_info=True)
        return None
    try:
        now = int(time.time() * 1000)
        with coordinator.store.immediate(conn):
            op = coordinator.store.get_operation(conn, operation_id)
            if op is None or op.state in TERMINAL_OPERATION_STATES:
                return None
            if latest_resume_grant(coordinator.store, conn, operation_id) is None:
                return None
            if op.lease_owner != DELIVERY_JOURNAL_OWNER or op.lease_pid != os.getpid():
                return None
            if not op.lease_expiry_utc_ms or now >= op.lease_expiry_utc_ms:
                return None
            return {step.effect_id: step for step in coordinator.store.get_steps(conn, operation_id)}
    except Exception:
        logger.debug("delivery_resume_grant_unreadable", exc_info=True)
        return None
    finally:
        conn.close()


def _conflict_result(result: ClaimResult, delivery_id: str) -> DeliverResultDict:
    """Build the zero-effect explicit-ID refusal result (FR01 acceptance)."""
    return _blocked_result(delivery_id, result.status.value, result.reason_code, status=result.status.value)


def _existing_operation_refusal(result: ClaimResult, delivery_id: str) -> DeliverResultDict:
    """Zero-effect projection for an already-claimed ID with no resume grant."""
    terminal_success = result.state is OperationState.SUCCEEDED
    out: DeliverResultDict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": terminal_success,
        "delivery_operation": {
            "operation_id": delivery_id,
            "status": "existing",
            "reason_code": "already_succeeded" if terminal_success else "operation_already_claimed",
            "state": result.state.value,
            "revision": result.revision,
            "effect_calls": 0,
            "caller_recoverable": True,
        },
    }
    if not terminal_success:
        message = (
            "delivery operation already exists and this process holds no resume grant; query "
            "trw_delivery_status, then call trw_delivery_recover with action='resume' to finish "
            "it under the same delivery_id instead of replaying effects"
        )
        out["delivery_blocked"] = message
        out["errors"] = [message]
    return out


def _blocked_result(
    delivery_id: str, blocked_code: str, reason_code: str, *, status: str = "rejected"
) -> DeliverResultDict:
    message = f"{blocked_code}: {reason_code}"
    out: DeliverResultDict = {"timestamp": datetime.now(timezone.utc).isoformat()}
    out["success"] = False
    out["delivery_blocked"] = message
    out["errors"] = [message]
    out["delivery_operation"] = {
        "operation_id": delivery_id,
        "status": status,
        "reason_code": reason_code,
        "effect_calls": 0,
        "caller_recoverable": True,
    }
    return out
