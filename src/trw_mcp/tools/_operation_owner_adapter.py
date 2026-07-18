"""Operation-owner registry + lossless CORE-208 envelope adapter — PRD-CORE-215 FR03/FR05.

Belongs to no facade — imported directly by ``middleware/ceremony.py`` (the FR03
production consumption point) and ``tools/delivery_ops.py`` (the FR05 status
projection). Two responsibilities:

FR03 — request identity delegated to operation owners
    A registry maps each side-effecting tool to its owning persistence adapter.
    Delivery tools delegate idempotency/collision handling to the EXISTING
    PRD-CORE-208 operation journal (``DeliveryOperationOwner`` wraps the
    coordinator — it creates NO second journal). A tool with no registered owner
    adapter CANNOT claim operation-backed behavior: :func:`require_operation_backed`
    raises :class:`UnownedOperationError` and
    :func:`validate_operation_backed_claim` returns ``"unowned_claim"``.

FR05 — CORE-208 delivery adapter and status routing
    :func:`claim_envelope`/:func:`status_envelope`/:func:`recover_envelope`/
    :func:`hard_budget_stop_envelope` project CORE-208 results into the common
    :class:`ToolResultEnvelope` WITHOUT changing their semantics — every state
    keeps its exact CORE-208 label in ``diagnostics`` so the projection is
    lossless. :func:`route_status_query` routes a delivery status read only to
    the declared operation owner and refuses (typed) a non-owner tool. No second
    delivery status or recovery authority is created here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from trw_mcp.models.tool_result import (
    CeremonyExecutionClass,
    Outcome,
    RetrySafety,
    ToolResultEnvelope,
    TruncationState,
)
from trw_mcp.tools._delivery_models import (
    ClaimResult,
    ClaimStatus,
    OperationState,
    RecoverResult,
    RecoverStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from trw_mcp.tools._delivery_operations import DeliveryCoordinator

__all__ = [
    "DELIVERY_TOOL_NAMES",
    "DeliveryOperationOwner",
    "RequestOwner",
    "UnownedOperationError",
    "claim_envelope",
    "get_owner",
    "hard_budget_stop_envelope",
    "recover_envelope",
    "register_owner",
    "require_operation_backed",
    "reset_registry",
    "route_status_query",
    "status_envelope",
    "validate_operation_backed_claim",
]

#: The exact delivery tools that PRD-CORE-208 owns (§4 inventory).
DELIVERY_TOOL_NAMES: tuple[str, ...] = ("trw_deliver", "trw_delivery_status", "trw_delivery_recover")


class UnownedOperationError(RuntimeError):
    """Raised when a tool claims operation-backed behavior without a registered owner."""

    code = "unowned_operation"

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"tool '{tool_name}' has no registered operation-backed owner adapter")
        self.tool_name = tool_name


@runtime_checkable
class RequestOwner(Protocol):
    """A tool's authoritative persistence adapter (its execution class + identity)."""

    execution_class: CeremonyExecutionClass
    owner_id: str


class DeliveryOperationOwner:
    """Delegates delivery idempotency/collision handling to the CORE-208 journal.

    Wraps :class:`DeliveryCoordinator`; it never opens a second journal. A
    ``coordinator_factory`` is injectable for tests — production resolves the same
    coordinator ``trw_delivery_status`` uses via ``delivery_ops._coordinator``.
    """

    execution_class: CeremonyExecutionClass = CeremonyExecutionClass.OPERATION_BACKED
    owner_id = "prd-core-208-delivery-journal"

    def __init__(self, coordinator_factory: Callable[[], DeliveryCoordinator] | None = None) -> None:
        self._factory = coordinator_factory

    def coordinator(self) -> DeliveryCoordinator:
        if self._factory is not None:
            return self._factory()
        from trw_mcp.tools.delivery_ops import _coordinator

        return _coordinator()

    def resolve_claim(self, **claim_kwargs: object) -> ClaimResult:
        """Delegate to the CORE-208 claim: exact repeat dedupes, changed field conflicts."""
        return self.coordinator().claim(**claim_kwargs)  # type: ignore[arg-type]


# --- Registry -------------------------------------------------------------

_OWNER_REGISTRY: dict[str, RequestOwner] = {}


def _install_defaults() -> None:
    owner = DeliveryOperationOwner()
    for tool_name in DELIVERY_TOOL_NAMES:
        _OWNER_REGISTRY[tool_name] = owner


def register_owner(tool_name: str, owner: RequestOwner, *, replace: bool = False) -> None:
    """Register ``owner`` as the authoritative store for ``tool_name``."""
    if not replace and tool_name in _OWNER_REGISTRY and _OWNER_REGISTRY[tool_name] is not owner:
        raise ValueError(f"tool '{tool_name}' already has a registered owner")
    _OWNER_REGISTRY[tool_name] = owner


def get_owner(tool_name: str) -> RequestOwner | None:
    return _OWNER_REGISTRY.get(tool_name)


def require_operation_backed(tool_name: str) -> RequestOwner:
    """Return the operation-backed owner or refuse (typed) an unowned handle claim."""
    owner = _OWNER_REGISTRY.get(tool_name)
    if owner is None or owner.execution_class is not CeremonyExecutionClass.OPERATION_BACKED:
        raise UnownedOperationError(tool_name)
    return owner


def reset_registry() -> None:
    """Restore the built-in default registry (test isolation)."""
    _OWNER_REGISTRY.clear()
    _install_defaults()


_install_defaults()


# --- FR03 middleware consumption -----------------------------------------


def declares_operation_backed(payload: Mapping[str, object]) -> bool:
    """True when a tool result asserts operation-backed behavior (a returned handle)."""
    if payload.get("operation_backed") is True:
        return True
    if payload.get("execution_class") == CeremonyExecutionClass.OPERATION_BACKED.value:
        return True
    return bool(payload.get("operation_id")) and (payload.get("accepted") is True or bool(payload.get("handle")))


def validate_operation_backed_claim(tool_name: str, payload: Mapping[str, object]) -> str:
    """Validate an operation-backed claim against the registry.

    Returns ``"not_a_claim"`` (payload makes no operation-backed claim),
    ``"valid"`` (claim is backed by a registered operation owner), or
    ``"unowned_claim"`` (an unowned tool illegitimately claims a handle).
    """
    if not declares_operation_backed(payload):
        return "not_a_claim"
    owner = _OWNER_REGISTRY.get(tool_name)
    if owner is not None and owner.execution_class is CeremonyExecutionClass.OPERATION_BACKED:
        return "valid"
    return "unowned_claim"


# --- FR05 lossless CORE-208 -> envelope projection -----------------------

_OP = CeremonyExecutionClass.OPERATION_BACKED

_CLAIM_OUTCOME: dict[ClaimStatus, tuple[Outcome, RetrySafety]] = {
    ClaimStatus.CLAIMED: (Outcome.ACCEPTED, RetrySafety.SAFE_EXACT_RETRY),
    ClaimStatus.EXISTING: (Outcome.ACCEPTED, RetrySafety.SAFE_EXACT_RETRY),
    ClaimStatus.CONFLICT: (Outcome.REJECTED, RetrySafety.UNSAFE),
    ClaimStatus.STORE_FULL: (Outcome.UNCERTAIN, RetrySafety.UNKNOWN),
    ClaimStatus.REJECTED: (Outcome.REJECTED, RetrySafety.UNSAFE),
}

_STATE_OUTCOME: dict[OperationState, tuple[Outcome, RetrySafety]] = {
    OperationState.SUCCEEDED: (Outcome.COMPLETED, RetrySafety.SAFE_EXACT_RETRY),
    OperationState.FAILED: (Outcome.REJECTED, RetrySafety.UNSAFE),
    OperationState.CANCELLED: (Outcome.REJECTED, RetrySafety.UNSAFE),
    OperationState.INDETERMINATE: (Outcome.UNCERTAIN, RetrySafety.UNKNOWN),
    OperationState.BLOCKED: (Outcome.UNCERTAIN, RetrySafety.UNKNOWN),
    OperationState.PENDING: (Outcome.ACCEPTED, RetrySafety.SAFE_EXACT_RETRY),
    OperationState.RUNNING: (Outcome.ACCEPTED, RetrySafety.SAFE_EXACT_RETRY),
    OperationState.CRITICAL_COMPLETE: (Outcome.ACCEPTED, RetrySafety.SAFE_EXACT_RETRY),
    OperationState.DEFERRED_QUEUED: (Outcome.ACCEPTED, RetrySafety.SAFE_EXACT_RETRY),
    OperationState.DEFERRED_RUNNING: (Outcome.ACCEPTED, RetrySafety.SAFE_EXACT_RETRY),
    OperationState.CANCEL_REQUESTED: (Outcome.ACCEPTED, RetrySafety.UNKNOWN),
}

_RESULT_OUTCOME: dict[str, tuple[Outcome, RetrySafety]] = {
    "not_found_store": (Outcome.UNCERTAIN, RetrySafety.UNKNOWN),
    "not_found_id": (Outcome.UNCERTAIN, RetrySafety.UNKNOWN),
    "invalid_id": (Outcome.REJECTED, RetrySafety.UNSAFE),
    "corrupt_store": (Outcome.UNCERTAIN, RetrySafety.UNKNOWN),
    "unsupported_schema": (Outcome.UNCERTAIN, RetrySafety.UNKNOWN),
    "legacy_wal_migration_required": (Outcome.UNCERTAIN, RetrySafety.UNKNOWN),
    "error": (Outcome.UNCERTAIN, RetrySafety.UNKNOWN),
}

_RECOVER_OUTCOME: dict[RecoverStatus, tuple[Outcome, RetrySafety]] = {
    RecoverStatus.OK: (Outcome.COMPLETED, RetrySafety.SAFE_EXACT_RETRY),
    RecoverStatus.UNAUTHORIZED: (Outcome.REJECTED, RetrySafety.UNSAFE),
    RecoverStatus.NOT_STALE: (Outcome.REJECTED, RetrySafety.SAFE_EXACT_RETRY),
    RecoverStatus.CONFLICT: (Outcome.REJECTED, RetrySafety.UNSAFE),
    RecoverStatus.STALE_REVISION: (Outcome.REJECTED, RetrySafety.UNSAFE),
    RecoverStatus.LIVE_OWNER: (Outcome.REJECTED, RetrySafety.SAFE_EXACT_RETRY),
    RecoverStatus.NOT_FOUND: (Outcome.UNCERTAIN, RetrySafety.UNKNOWN),
    RecoverStatus.REJECTED: (Outcome.REJECTED, RetrySafety.UNSAFE),
}


def claim_envelope(claim: ClaimResult, *, request_id: str = "", input_digest: str = "") -> ToolResultEnvelope:
    """Project a CORE-208 claim decision losslessly into the common envelope."""
    outcome, retry = _CLAIM_OUTCOME[claim.status]
    return ToolResultEnvelope(
        outcome=outcome,
        reason_code=claim.reason_code or claim.status.value,
        operation_id=claim.operation_id,
        request_id=request_id or claim.operation_id,
        input_digest=input_digest,
        execution_class=_OP,
        retry_safety=retry,
        diagnostics={
            "claim_status": claim.status.value,
            "operation_state": claim.state.value,
            "effect_calls": str(claim.effect_calls),
        },
    )


def status_envelope(
    status: Mapping[str, object],
    *,
    request_id: str = "",
    input_digest: str = "",
    output_budget_chars: int | None = None,
) -> ToolResultEnvelope:
    """Project a CORE-208 status projection losslessly into the common envelope."""
    result = str(status.get("result", "error"))
    operation_id = str(status.get("operation_id", ""))
    diagnostics: dict[str, str] = {"result": result}
    truncation = TruncationState.NONE
    omitted: tuple[str, ...] = ()

    if result == "ok":
        state = OperationState(str(status["state"]))
        outcome, retry = _STATE_OUTCOME[state]
        diagnostics["operation_state"] = state.value
        diagnostics["revision"] = str(status.get("revision", ""))
    elif result == "tombstone":
        terminal = str(status.get("terminal_state", ""))
        outcome, retry = _tombstone_outcome(terminal)
        diagnostics["terminal_state"] = terminal
    else:
        outcome, retry = _RESULT_OUTCOME.get(result, (Outcome.UNCERTAIN, RetrySafety.UNKNOWN))

    estimate = len(json.dumps(status, default=str, sort_keys=True))
    if output_budget_chars is not None and estimate > output_budget_chars:
        truncation = TruncationState.TRUNCATED
        omitted = ("steps",)
        diagnostics["omitted_over_budget_chars"] = str(estimate - output_budget_chars)

    return ToolResultEnvelope(
        outcome=outcome,
        reason_code=str(status.get("reason_code", result))[:128],
        operation_id=operation_id,
        request_id=request_id or operation_id,
        input_digest=input_digest,
        execution_class=_OP,
        retry_safety=retry,
        output_estimate=estimate,
        truncation_state=truncation,
        omitted_sections=omitted,
        diagnostics=diagnostics,
    )


def _tombstone_outcome(terminal_state: str) -> tuple[Outcome, RetrySafety]:
    try:
        return _STATE_OUTCOME[OperationState(terminal_state)]
    except ValueError:
        return (Outcome.COMPLETED, RetrySafety.SAFE_EXACT_RETRY)


def recover_envelope(recover: RecoverResult, *, request_id: str = "", input_digest: str = "") -> ToolResultEnvelope:
    """Project a CORE-208 recovery decision losslessly into the common envelope."""
    outcome, retry = _RECOVER_OUTCOME[recover.status]
    diagnostics: dict[str, str] = {
        "recover_status": recover.status.value,
        "operation_state": recover.state.value,
    }
    if recover.indeterminate_effect_ids:
        diagnostics["indeterminate_effect_ids"] = ",".join(recover.indeterminate_effect_ids)
    return ToolResultEnvelope(
        outcome=outcome,
        reason_code=recover.reason_code or recover.status.value,
        operation_id=recover.operation_id,
        request_id=request_id or recover.operation_id,
        input_digest=input_digest,
        execution_class=_OP,
        retry_safety=retry,
        receipt_refs=tuple(recover.replayed_effect_ids),
        diagnostics=diagnostics,
    )


def hard_budget_stop_envelope(
    operation_id: str, *, reason: str, request_id: str = "", input_digest: str = ""
) -> ToolResultEnvelope:
    """Envelope for an operation that hit a hard budget and returned a persisted handle."""
    return ToolResultEnvelope(
        outcome=Outcome.ACCEPTED,
        reason_code="hard_budget_stop",
        operation_id=operation_id,
        request_id=request_id or operation_id,
        input_digest=input_digest,
        execution_class=_OP,
        retry_safety=RetrySafety.SAFE_EXACT_RETRY,
        truncation_state=TruncationState.HARD_BUDGET_STOPPED,
        hard_budget_stop_reason=reason[:128],
        safe_reproduction_hint="retry trw_delivery_status with the same delivery_id",
        diagnostics={"budget": "hard"},
    )


def route_status_query(
    owner_tool: str,
    *,
    delivery_id: str,
    coordinator_factory: Callable[[], DeliveryCoordinator] | None = None,
    verbose: bool = False,
) -> dict[str, object]:
    """Route a delivery status read only to the declared operation owner (FR05).

    A non-owner tool is refused (typed) — no second delivery status authority
    exists. The owner's status projection is returned with an ``envelope`` field.
    """
    owner = _OWNER_REGISTRY.get(owner_tool)
    if owner is None or owner.execution_class is not CeremonyExecutionClass.OPERATION_BACKED:
        return {
            "result": "owner_routing_refused",
            "reason_code": "not_delivery_owner",
            "tool": owner_tool,
        }
    if coordinator_factory is not None:
        coord = coordinator_factory()
    elif isinstance(owner, DeliveryOperationOwner):
        coord = owner.coordinator()
    else:  # pragma: no cover - registry only maps delivery tools to the delivery owner
        from trw_mcp.tools.delivery_ops import _coordinator

        coord = _coordinator()
    status = coord.project_status(delivery_id, verbose=verbose)
    status["envelope"] = status_envelope(status, request_id=delivery_id).model_dump(mode="json")
    return status
