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
  a terminal transition after it (crash-safe: a killed deliver leaves a
  ``started`` step for FR04 recovery, never ``not_started``).
- **FR06** — records the deferred batch digest so a later different-ID delivery
  attaches or durably FIFO-queues.
- **NFR01** — a legacy no-``delivery_id`` call generates a fresh server-side
  UUIDv7 each time, so it is journaled but reports ``caller_recoverable=false``
  (a lost response can never be recovered by caller identity).

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

from trw_mcp.models._evidence_core import domain_digest
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import DeliverResultDict
from trw_mcp.tools._delivery_models import ClaimResult, ClaimStatus, OperationState, StepState
from trw_mcp.tools._delivery_operations import DeliveryCoordinator

logger = structlog.get_logger(__name__)

_JOURNAL_OWNER = "trw_deliver"
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
    ) -> None:
        self._coordinator = coordinator
        self.operation_id = operation_id
        self.caller_recoverable = caller_recoverable
        self.mode = mode
        self.journaled_effects: set[str] = set()

    @property
    def enabled(self) -> bool:
        return self._coordinator is not None and bool(self.operation_id)

    @contextmanager
    def step(self, effect_id: str) -> Iterator[None]:
        """Commit ``started`` before the wrapped effect, terminal after (FR02)."""
        started = self._begin(effect_id)
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            if started:
                self._finalize(effect_id, StepState.FAILED if failed else StepState.SUCCEEDED)

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

    def _finalize(self, effect_id: str, state: StepState) -> None:
        if self._coordinator is None:
            return
        try:
            self._coordinator.finalize_step(self.operation_id, effect_id, state=state)
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

    if result.status is ClaimStatus.EXISTING and mode == "enforce":
        return DeliverJournal(mode=mode), _existing_operation_result(result, delivery_id)
    if result.status in (ClaimStatus.CLAIMED, ClaimStatus.EXISTING):
        return (
            DeliverJournal(
                coordinator=coordinator,
                operation_id=result.operation_id,
                caller_recoverable=caller_recoverable,
                mode=mode,
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
    NON_REPLAYABLE trust step ``started`` for FR04 recovery, never lost. Fully
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
    return DeliverJournal(coordinator=coordinator, operation_id=operation_id, caller_recoverable=False, mode=mode)


def _conflict_result(result: ClaimResult, delivery_id: str) -> DeliverResultDict:
    """Build the zero-effect explicit-ID refusal result (FR01 acceptance)."""
    return _blocked_result(delivery_id, result.status.value, result.reason_code, status=result.status.value)


def _existing_operation_result(result: ClaimResult, delivery_id: str) -> DeliverResultDict:
    """Return a zero-effect idempotent projection for an already-claimed ID."""
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
            "delivery operation already exists; query trw_delivery_status and use an authorized "
            "recovery action instead of replaying effects"
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
