"""PRD-CORE-208 NFR04: fixed bounded lifecycle with no identifier-reuse hole."""

from __future__ import annotations

import pytest

from tests._delivery_support import days_ms, make_coordinator, make_uuid7, strong_capability
from trw_mcp.tools._delivery_models import ClaimStatus, OperationState
from trw_mcp.tools._delivery_request import DeliveryLimits


def _age_operation(coord, did: str, *, created_delta_ms: int = 0, terminal_delta_ms: int = 0) -> None:
    # Direct SQL: created_utc_ms is immutable in production (replace_operation omits
    # it by design), so tests must backdate it out-of-band to exercise retention.
    conn = coord.store.connect()
    now = coord._now_ms()
    with coord.store.immediate(conn):
        if created_delta_ms:
            conn.execute(
                "UPDATE operations SET created_utc_ms=? WHERE operation_id=?",
                (now - created_delta_ms, did),
            )
        if terminal_delta_ms:
            conn.execute(
                "UPDATE operations SET terminal_utc_ms=? WHERE operation_id=?",
                (now - terminal_delta_ms, did),
            )
    conn.close()


def test_terminal_full_record_compacts_to_tombstone_at_30_days(tmp_path) -> None:
    """NFR04: terminal full records compact to a digest-only tombstone at 30 days."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), run_identity="t/r")
    coord.mark_operation_state(did, OperationState.SUCCEEDED)
    _age_operation(coord, did, terminal_delta_ms=days_ms(31))

    counts = coord.run_maintenance()
    assert counts["compacted_terminal"] == 1
    status = coord.project_status(did)
    assert status["result"] == "tombstone"
    assert status["terminal_state"] == "succeeded"


def test_unresolved_record_tombstones_at_90_days(tmp_path) -> None:
    """NFR04: an unresolved record compacts to expired_indeterminate at 90 days."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability())  # stays pending
    _age_operation(coord, did, created_delta_ms=days_ms(91))

    counts = coord.run_maintenance()
    assert counts["tombstoned_unresolved"] == 1
    assert coord.project_status(did)["terminal_state"] == "expired_indeterminate"


def test_tombstone_deleted_at_180_day_horizon_but_old_id_still_rejected(tmp_path, monkeypatch) -> None:
    """NFR04: the reuse hole is closed — deleting a tombstone cannot reopen an old ID."""
    coord = make_coordinator(tmp_path)
    base = coord._now_ms()
    old_id = make_uuid7(base)  # embedded timestamp = now (valid at claim)
    coord.claim(delivery_id=old_id, capability_token=strong_capability())
    _age_operation(coord, old_id, created_delta_ms=days_ms(200))  # force retention eligibility

    counts = coord.run_maintenance()  # compacts (unresolved) then deletes the expired tombstone
    assert counts["tombstoned_unresolved"] == 1
    assert counts["expired_tombstones"] == 1

    # Clock advances 181 days beyond the id's embedded timestamp. The tombstone is
    # gone, yet a re-claim of the SAME id is rejected by embedded time — no reuse.
    monkeypatch.setattr(type(coord), "_now_ms", staticmethod(lambda: base + days_ms(181)))
    reclaim = coord.claim(delivery_id=old_id, capability_token=strong_capability())
    assert reclaim.status is ClaimStatus.REJECTED
    assert reclaim.reason_code == "expired_id"


def test_store_full_blocks_new_claim_without_dropping_existing(tmp_path, monkeypatch) -> None:
    """NFR04: over the 64 MiB hard cap, new claims fail closed as delivery_store_full."""
    coord = make_coordinator(tmp_path)
    kept = make_uuid7()
    coord.claim(delivery_id=kept, capability_token=strong_capability())  # existing survives

    monkeypatch.setattr(type(coord.store), "store_bytes", lambda self: DeliveryLimits.STORE_MAX_BYTES + 1)
    blocked = coord.claim(delivery_id=make_uuid7(), capability_token=strong_capability())
    assert blocked.status is ClaimStatus.STORE_FULL
    assert blocked.reason_code == "delivery_store_full"
    # The pre-existing operation is untouched and still readable.
    assert coord.project_status(kept)["result"] == "ok"


def test_high_water_is_monotonic_and_survives_backward_clock(tmp_path, monkeypatch) -> None:
    """NFR04: a backward wall-clock jump cannot reopen an already-expired identifier."""
    coord = make_coordinator(tmp_path)
    # Claim advances the high-water mark to ~now.
    coord.claim(delivery_id=make_uuid7(), capability_token=strong_capability())
    conn = coord.store.connect()
    high = coord.store.get_high_water(conn)
    conn.close()
    assert high > 0

    # Simulate the wall clock jumping ~1 year into the past.
    past = high - days_ms(365)
    monkeypatch.setattr(type(coord), "_now_ms", staticmethod(lambda: past))
    # An id valid under the rewound clock but >180 days before the high-water mark
    # is still rejected, because effective_now = max(wall, high_water).
    resurrected = make_uuid7(past)  # "fresh" under the rewound clock
    result = coord.claim(delivery_id=resurrected, capability_token=strong_capability())
    assert result.status is ClaimStatus.REJECTED
    assert result.reason_code == "expired_id"


def test_reason_and_evidence_bounds_reject_oversize(tmp_path) -> None:
    """NFR04: oversize recovery reason / evidence reference is rejected before write."""
    from trw_mcp.tools._delivery_recovery import enforce_reason_bounds
    from trw_mcp.tools._delivery_request import DeliveryRequestError

    with pytest.raises(DeliveryRequestError):
        enforce_reason_bounds("x" * (DeliveryLimits.MAX_REASON_CHARS + 1), "")
    with pytest.raises(DeliveryRequestError):
        enforce_reason_bounds("ok", "y" * (DeliveryLimits.MAX_EVIDENCE_REF_CHARS + 1))
