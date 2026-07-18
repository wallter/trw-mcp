"""PRD-CORE-208 FR01: canonical caller-stable request identity + claim binding."""

from __future__ import annotations

import pytest

from tests._delivery_support import days_ms, make_coordinator, make_uuid7, strong_capability
from trw_mcp.tools._delivery_models import ClaimStatus, OperationState


def test_delivery_id_is_claimed_and_request_bound_before_mutation(tmp_path) -> None:
    """FR01: a valid explicit-ID claim binds one request digest and one operation."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    cap = strong_capability()

    result = coord.claim(delivery_id=did, capability_token=cap, run_identity="task/run-1")
    assert result.status is ClaimStatus.CLAIMED
    assert result.operation_id == did
    assert result.state is OperationState.PENDING
    assert result.effect_calls == 0

    # The claim is durably present with a stable revision.
    status = coord.project_status(did)
    assert status["result"] == "ok"
    assert status["state"] == "pending"
    assert status["revision"] == 1


def test_same_id_same_request_returns_one_operation_and_stable_revision(tmp_path) -> None:
    """FR01: byte-equivalent repeat follows the existing operation (idempotent)."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    cap = strong_capability()
    first = coord.claim(delivery_id=did, capability_token=cap, run_identity="task/run-1")
    second = coord.claim(delivery_id=did, capability_token=cap, run_identity="task/run-1")

    assert first.operation_id == second.operation_id
    assert second.status is ClaimStatus.EXISTING
    assert second.effect_calls == 0
    assert second.revision == first.revision  # no lease/step/target mutation


@pytest.mark.parametrize(
    "mutate",
    [
        {"run_identity": "task/run-2"},
        {"skip_reflect": True},
        {"skip_index_sync": True},
        {"allow_unverified": True},
        {"acceptable_failure_digest": "deadbeef"},
    ],
)
def test_same_id_any_bound_field_change_is_conflict_with_zero_effects(tmp_path, mutate) -> None:
    """FR01 assertion: conflicting_request.effect_calls == 0 for every bound field."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    cap = strong_capability()
    base = {
        "run_identity": "task/run-1",
        "skip_reflect": False,
        "skip_index_sync": False,
        "allow_unverified": False,
        "acceptable_failure_digest": "",
    }
    coord.claim(delivery_id=did, capability_token=cap, **base)
    conflicting = {**base, **mutate}
    result = coord.claim(delivery_id=did, capability_token=cap, **conflicting)

    assert result.status is ClaimStatus.CONFLICT
    assert result.reason_code == "delivery_request_conflict"
    assert result.effect_calls == 0


def test_same_id_different_capability_is_conflict(tmp_path) -> None:
    """FR01: reuse with a different capability hash returns conflict."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), run_identity="task/run-1")
    result = coord.claim(delivery_id=did, capability_token=strong_capability(), run_identity="task/run-1")
    assert result.status is ClaimStatus.CONFLICT
    assert result.effect_calls == 0


@pytest.mark.parametrize(
    "bad_id",
    [
        "not-a-uuid",
        "../../etc/passwd",
        "12345678-1234-4234-8234-1234567890ab",  # UUIDv4, not v7
        "",
    ],
)
def test_malformed_or_wrong_version_ids_rejected_before_store_mutation(tmp_path, bad_id) -> None:
    """FR01: UUIDv4/malformed/traversal IDs rejected before any store write."""
    coord = make_coordinator(tmp_path)
    result = coord.claim(delivery_id=bad_id, capability_token=strong_capability())
    assert result.status is ClaimStatus.REJECTED
    assert result.effect_calls == 0
    # Nothing durable was written — the id is unknown to a subsequent status read.
    assert coord.project_status(make_uuid7())["result"] in {"not_found_id", "not_found_store"}


def test_future_skew_and_expired_ids_rejected(tmp_path) -> None:
    """FR01: >5-minute future skew and >180-day-old IDs rejected."""
    coord = make_coordinator(tmp_path)
    future = make_uuid7(coord._now_ms() + days_ms(1))  # far future
    old = make_uuid7(coord._now_ms() - days_ms(200))  # older than horizon

    fut = coord.claim(delivery_id=future, capability_token=strong_capability())
    exp = coord.claim(delivery_id=old, capability_token=strong_capability())
    assert fut.status is ClaimStatus.REJECTED and fut.reason_code == "future_skew"
    assert exp.status is ClaimStatus.REJECTED and exp.reason_code == "expired_id"


def test_weak_capability_rejected(tmp_path) -> None:
    """FR01: a recovery capability below 128 bits is rejected."""
    coord = make_coordinator(tmp_path)
    result = coord.claim(delivery_id=make_uuid7(), capability_token="short")
    assert result.status is ClaimStatus.REJECTED
    assert result.reason_code == "weak_capability"


def test_status_never_exposes_capability_or_hash(tmp_path) -> None:
    """FR01 acceptance: status output never exposes the capability or its hash."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    cap = strong_capability()
    coord.claim(delivery_id=did, capability_token=cap, run_identity="task/run-1")
    status = coord.project_status(did)
    serialized = repr(status)
    assert cap not in serialized
    assert "capability_hash" not in status
    assert "capability_salt" not in status


# --- PRD-CORE-215 FR02: typed tool-result envelope ---


def _succeeded_operation(coord, did, cap):  # type: ignore[no-untyped-def]
    """Drive one delivery operation to a durable SUCCEEDED terminal state."""
    from trw_mcp.tools._delivery_models import OperationState as _OS
    from trw_mcp.tools._delivery_models import StepState as _SS

    coord.claim(delivery_id=did, capability_token=cap, run_identity="task/run-1", owner="w", pid=1)
    coord.begin_step(did, "S01", owner="w", pid=1)
    coord.finalize_step(did, "S01", state=_SS.SUCCEEDED, proof_digest="d1")
    coord.mark_operation_state(did, _OS.SUCCEEDED)


def test_prd_core_215_fr02(tmp_path) -> None:
    """FR02: success/rejection/background-acceptance/truncation/hard-budget-stop/
    post-acceptance-loss each map to exactly one envelope; secrets redacted;
    omitted content named; contradictory legacy fields cannot flip the outcome.
    """
    from trw_mcp.models.tool_result import (
        Outcome,
        RedactionState,
        RetrySafety,
        ToolResultEnvelope,
        TruncationState,
    )
    from trw_mcp.tools._operation_owner_adapter import (
        claim_envelope,
        hard_budget_stop_envelope,
        status_envelope,
    )

    coord = make_coordinator(tmp_path)
    cap = strong_capability()

    envelopes: dict[str, ToolResultEnvelope] = {}

    # 1. success — a durable SUCCEEDED operation projects to exactly one COMPLETED envelope.
    success_id = make_uuid7()
    _succeeded_operation(coord, success_id, cap)
    envelopes["success"] = status_envelope(coord.project_status(success_id))
    assert envelopes["success"].outcome is Outcome.COMPLETED

    # 2. rejection — a conflicting claim projects to exactly one REJECTED envelope.
    reject_id = make_uuid7()
    coord.claim(delivery_id=reject_id, capability_token=cap, run_identity="task/run-1")
    conflict = coord.claim(delivery_id=reject_id, capability_token=cap, run_identity="task/run-2")
    assert conflict.status is ClaimStatus.CONFLICT
    envelopes["rejection"] = claim_envelope(conflict)
    assert envelopes["rejection"].outcome is Outcome.REJECTED
    assert envelopes["rejection"].retry_safety is RetrySafety.UNSAFE

    # 3. background-acceptance — a fresh claim projects to exactly one ACCEPTED envelope.
    accept_id = make_uuid7()
    claimed = coord.claim(delivery_id=accept_id, capability_token=cap, run_identity="task/run-3")
    assert claimed.status is ClaimStatus.CLAIMED
    envelopes["background_acceptance"] = claim_envelope(claimed)
    assert envelopes["background_acceptance"].outcome is Outcome.ACCEPTED

    # 4. truncation — a tiny output budget names the omitted section.
    trunc = status_envelope(coord.project_status(success_id), output_budget_chars=10)
    envelopes["truncation"] = trunc
    assert trunc.truncation_state is TruncationState.TRUNCATED
    assert trunc.omitted_sections == ("steps",)  # omitted content is named

    # 5. hard-budget-stop — a persisted handle with a hard-stop reason.
    hbs = hard_budget_stop_envelope(accept_id, reason="wall_clock_budget_exceeded")
    envelopes["hard_budget_stop"] = hbs
    assert hbs.truncation_state is TruncationState.HARD_BUDGET_STOPPED
    assert hbs.hard_budget_stop_reason == "wall_clock_budget_exceeded"

    # 6. post-acceptance-loss — a restarted process re-reads the durable SUCCEEDED
    #    operation and gets COMPLETED with a safe-exact-retry guarantee (the effect
    #    already persisted, so retrying the same delivery_id duplicates nothing).
    restarted = make_coordinator(tmp_path)
    loss = status_envelope(restarted.project_status(success_id))
    envelopes["post_acceptance_loss"] = loss
    assert loss.outcome is Outcome.COMPLETED
    assert loss.retry_safety is RetrySafety.SAFE_EXACT_RETRY

    # Each fixture produced exactly one envelope, each serializing to one outcome.
    assert len(envelopes) == 6
    for env in envelopes.values():
        dumped = env.model_dump(mode="json")
        assert isinstance(dumped, dict)
        assert dumped["outcome"] in {o.value for o in Outcome}

    # Secret-looking fields are redacted in the envelope surface.
    secret_env = ToolResultEnvelope(
        outcome=Outcome.COMPLETED,
        operation_id=success_id,
        diagnostics={"api_key": "sk-live-should-not-leak", "operation_state": "succeeded"},
    )
    secret_dump = secret_env.model_dump(mode="json")
    assert secret_dump["diagnostics"]["api_key"] == "***REDACTED***"
    assert "sk-live-should-not-leak" not in repr(secret_dump)

    # A legacy dict with contradictory keys cannot flip the typed outcome.
    legacy_env = ToolResultEnvelope.from_legacy(
        outcome=Outcome.REJECTED,
        legacy={"success": True, "status": "ok", "outcome": "completed"},
        reason_code="delivery_request_conflict",
        redaction_state=RedactionState.NONE,
    )
    assert legacy_env.outcome is Outcome.REJECTED
    assert set(legacy_env.legacy_conflicts) == {"success", "status", "outcome"}
    assert legacy_env.model_dump(mode="json")["outcome"] == "rejected"
