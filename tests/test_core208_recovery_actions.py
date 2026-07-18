"""CORE-208 explicit reconciliation, cancellation, and compensation actions."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._delivery_support import make_coordinator, make_uuid7, strong_capability
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._delivery_journal_wiring import open_delivery_journal
from trw_mcp.tools._delivery_models import OperationState, RecoverStatus, StepState
from trw_mcp.tools._delivery_operations import DeliveryCoordinator
from trw_mcp.tools._delivery_request import DeliveryRequestError


def _revision(coord: DeliveryCoordinator, operation_id: str) -> int:
    status = coord.project_status(operation_id)
    revision = status["revision"]
    assert isinstance(revision, int)
    return revision


def test_delivery_operations_default_to_enforce() -> None:
    assert TRWConfig().delivery_operations_mode == "enforce"


def test_nonreplayable_effect_requires_capability_bound_operator_reconciliation(tmp_path: Path) -> None:
    coord = make_coordinator(tmp_path)
    delivery_id = make_uuid7()
    capability = strong_capability()
    coord.claim(delivery_id=delivery_id, capability_token=capability)
    coord.begin_step(delivery_id, "D16")
    recovered = coord.recover_after_crash(delivery_id)
    assert recovered.indeterminate_effect_ids == ("D16",)

    unauthorized = coord.reconcile_effect(
        operation_id=delivery_id,
        effect_id="D16",
        applied=True,
        capability_token="wrong",
        expected_revision=_revision(coord, delivery_id),
        reason="operator verified the atomic trust ledger",
        evidence_ref="audit/trust-ledger#row-1",
    )
    assert unauthorized.status is RecoverStatus.UNAUTHORIZED

    reconciled = coord.reconcile_effect(
        operation_id=delivery_id,
        effect_id="D16",
        applied=True,
        capability_token=capability,
        expected_revision=_revision(coord, delivery_id),
        reason="operator verified the atomic trust ledger",
        evidence_ref="audit/trust-ledger#row-1",
    )
    assert reconciled.status is RecoverStatus.OK
    status = coord.project_status(delivery_id)
    assert status["steps"]["D16"]["state"] == "succeeded"


def test_confirmed_not_applied_allows_a_new_attempt_without_erasing_history(tmp_path: Path) -> None:
    coord = make_coordinator(tmp_path)
    delivery_id = make_uuid7()
    capability = strong_capability()
    coord.claim(delivery_id=delivery_id, capability_token=capability)
    coord.begin_step(delivery_id, "D16")
    coord.recover_after_crash(delivery_id)
    result = coord.reconcile_effect(
        operation_id=delivery_id,
        effect_id="D16",
        applied=False,
        capability_token=capability,
        expected_revision=_revision(coord, delivery_id),
        reason="transaction ledger proves no increment committed",
        evidence_ref="audit/trust-ledger#absent-1",
    )
    assert result.reason_code == "confirmed_not_applied"
    retry = coord.begin_step(delivery_id, "D16")
    assert retry.attempt == 2


def test_cancel_prevents_new_effects_and_finishes_after_active_step(tmp_path: Path) -> None:
    coord = make_coordinator(tmp_path)
    delivery_id = make_uuid7()
    capability = strong_capability()
    coord.claim(delivery_id=delivery_id, capability_token=capability)
    coord.begin_step(delivery_id, "S01")
    result = coord.request_cancel(
        operation_id=delivery_id,
        capability_token=capability,
        expected_revision=_revision(coord, delivery_id),
        reason="operator cancelled timed-out delivery",
    )
    assert result.state is OperationState.CANCEL_REQUESTED
    with pytest.raises(DeliveryRequestError, match="cancellation prevents"):
        coord.begin_step(delivery_id, "S05")
    coord.finalize_step(delivery_id, "S01", state=StepState.SUCCEEDED, proof_digest="a" * 64)
    assert coord.project_status(delivery_id)["state"] == "cancelled"


def test_compensation_fails_closed_when_no_compensator_is_registered(tmp_path: Path) -> None:
    coord = make_coordinator(tmp_path)
    delivery_id = make_uuid7()
    capability = strong_capability()
    coord.claim(delivery_id=delivery_id, capability_token=capability)
    before = coord.project_status(delivery_id)
    revision = before["revision"]
    assert isinstance(revision, int)
    result = coord.run_compensation(
        operation_id=delivery_id,
        effect_id="S01",
        capability_token=capability,
        expected_revision=revision,
        reason="operator requested rollback",
        evidence_ref="audit/request#1",
    )
    assert result.status is RecoverStatus.REJECTED
    assert result.reason_code == "no_registered_compensation"
    after = coord.project_status(delivery_id)
    assert after["revision"] == before["revision"]
    assert after["steps"] == before["steps"]


def test_public_recovery_tool_routes_every_advertised_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp import FastMCP

    from tests.conftest import extract_tool_fn
    from trw_mcp.tools.delivery_ops import register_delivery_tools

    coord = make_coordinator(tmp_path)
    delivery_id = make_uuid7()
    capability = strong_capability()
    coord.claim(delivery_id=delivery_id, capability_token=capability)
    server = FastMCP("delivery-recovery")
    register_delivery_tools(server)
    recover = extract_tool_fn(server, "trw_delivery_recover")
    monkeypatch.setattr("trw_mcp.tools.delivery_ops._coordinator", lambda: coord)

    result = recover(
        delivery_id=delivery_id,
        action="request_cancel",
        capability_token=capability,
        expected_revision=_revision(coord, delivery_id),
        reason="operator cancel",
    )
    assert result["status"] == "ok"
    unknown = recover(delivery_id=delivery_id, action="unknown")
    assert unknown["supported"] == [
        "takeover_pending",
        "reconcile_applied",
        "reconcile_not_applied",
        "request_cancel",
        "run_compensation",
    ]


def test_enforce_retry_returns_existing_operation_without_replaying_started_effect(tmp_path: Path) -> None:
    coord = DeliveryCoordinator(tmp_path / ".trw", config=TRWConfig())
    delivery_id = make_uuid7()
    capability = strong_capability()
    coord.claim(delivery_id=delivery_id, capability_token=capability)
    coord.begin_step(delivery_id, "D16")

    journal, refusal = open_delivery_journal(
        tmp_path / ".trw",
        TRWConfig(),
        run_identity="",
        skip_reflect=False,
        skip_index_sync=False,
        allow_unverified=False,
        delivery_id=delivery_id,
        capability_token=capability,
    )
    assert journal.enabled is False
    assert refusal is not None
    assert refusal["delivery_operation"]["effect_calls"] == 0
    assert refusal["delivery_operation"]["operation_id"] == delivery_id
    conn = coord.store.connect()
    try:
        step = coord.store.get_step(conn, delivery_id, "D16")
    finally:
        conn.close()
    assert step is not None and step.attempt == 1
