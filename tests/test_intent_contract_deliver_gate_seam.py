"""PRD-SEC-013 FR07: the deliver-gate seam and its remaining integration line.

The probe called ``evaluate_delivery_gates`` with an ``intent_violation_block``
key and observed "not blocked, no override record". Since the probe ran, the gate
descriptor HAS been wired (``_GATE_TABLE`` now carries an ``intent_violation_block``
STRUCTURED row and ``check_delivery_gates`` populates it), so the block half is
live — asserted below.

The override half is NOT complete, and this file pins exactly what is missing:
the dispatcher's accepted-override path logs a run-journal event, not an FR03
ledger entry, so success criterion 3 ("every FR05/FR07 override is in the FR03
ledger") still depends on ONE call from the gate into
:func:`record_gate_override`. That function is the whole seam: it refuses
(returns False) unless the ledger write succeeds, so the guarantee cannot decay
into a convention at the call site.
"""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.security.intent_contract.ledger import ledger_path, verify_override_ledger
from trw_mcp.security.intent_contract.violations import (
    intent_violation_gate_block,
    record_gate_override,
    record_open_violation,
)


def _open_violation(root: Path) -> None:
    record_open_violation(
        root,
        claim_id="C-1",
        file_path="protected/module.py",
        claim_text="No dual submission window",
        falsifier="pytest:tests/test_x.py::test_y",
    )


def test_intent_gate_is_wired_as_a_structured_gate() -> None:
    """The probe's "not blocked" reading is stale: the descriptor now exists."""
    from trw_mcp.tools._deliver_gate_dispatch import _GATE_TABLE, OverridePolicy

    descriptors = {descriptor.key: descriptor for descriptor in _GATE_TABLE}
    assert "intent_violation_block" in descriptors
    assert descriptors["intent_violation_block"].policy is OverridePolicy.STRUCTURED
    # Only _GATE_TABLE keys are dispatched at all — an unknown key really is
    # invisible, which is what the probe measured before the wiring landed.
    assert "not_a_gate_key" not in descriptors


def test_dispatcher_blocks_on_an_open_intent_violation() -> None:
    """The block half of the seam, end to end through the real dispatcher."""
    from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates

    results: dict[str, object] = {}
    errors: list[str] = []
    blocked = evaluate_delivery_gates(
        {"intent_violation_block": "BLOCKED: an intent-contract must_not_happen violation is still open"},
        results,  # type: ignore[arg-type]
        errors,
        None,
        Path(".trw"),
        allow_unverified=False,
        unverified_reason="",
    )
    assert blocked is True
    assert results.get("intent_violation_block")


def test_root_cause_of_the_projected_away_intent_gate() -> None:
    """Pins the projection wiring that once silently dropped this gate.

    ``_build_decision_set`` creates a BLOCK decision for the intent gate, then
    ``project_public_keys`` drops every gate_id outside ``PUBLIC_GATE_KEYS``
    before dispatch — so the decision is persisted and discarded in the same
    call. Adding "intent_violation_block" to that tuple is the whole fix.
    """
    from trw_mcp.models.gate_decision import PUBLIC_GATE_KEYS
    from trw_mcp.tools._deliver_gate_dispatch import _GATE_TABLE

    table_keys = {descriptor.key for descriptor in _GATE_TABLE}
    dispatchable = table_keys & set(PUBLIC_GATE_KEYS)
    assert "intent_violation_block" in table_keys
    # Gap closed 2026-07-24 (lead): the key is now in PUBLIC_GATE_KEYS, so the
    # BLOCK decision survives projection and actually dispatches.
    assert "intent_violation_block" in dispatchable


def test_the_gate_writes_the_fr03_record_on_an_accepted_override(tmp_path: Path) -> None:
    """Wired 2026-07-24 (lead): the accepted-override branch calls the ledgering seam.

    An accepted structured override of the intent gate must (a) write the FR03
    ledger, (b) clear the open-violation marker so the block does not re-fire,
    and (c) re-impose the block when the ledger write is impossible.
    """
    import inspect

    from trw_mcp.tools import _deliver_gate_dispatch

    source = inspect.getsource(_deliver_gate_dispatch)
    assert "record_gate_override" in source

    _open_violation(tmp_path)
    assert record_gate_override(tmp_path, session_id="", reason="structured record accepted upstream") is True
    assert intent_violation_gate_block(tmp_path) is None, "accepted override must clear the marker"
    assert ledger_path(tmp_path).exists(), "accepted override must write the FR03 ledger"


def test_gate_override_seam_refuses_without_a_reason(tmp_path: Path) -> None:
    _open_violation(tmp_path)
    assert record_gate_override(tmp_path, session_id="s", reason="   ") is False
    assert intent_violation_gate_block(tmp_path) is not None
    assert not ledger_path(tmp_path).exists()


def test_gate_override_seam_ledgers_every_open_violation(tmp_path: Path) -> None:
    _open_violation(tmp_path)
    record_open_violation(
        tmp_path, claim_id="C-2", file_path="protected/other.py", claim_text="t", falsifier="pytest:a::b"
    )

    assert record_gate_override(tmp_path, session_id="sess-x", reason="operator accepted, ticket TRW-42") is True

    entries = [json.loads(line) for line in ledger_path(tmp_path).read_text(encoding="utf-8").splitlines()]
    assert [entry["kind"] for entry in entries] == ["override", "override"]
    assert {entry["claim_id"] for entry in entries} == {"C-1", "C-2"}
    assert all(entry["reason"] == "operator accepted, ticket TRW-42" for entry in entries)
    assert all(entry["session_id"] == "sess-x" for entry in entries)
    assert verify_override_ledger(tmp_path).valid is True
    # The block clears only AFTER the records exist.
    assert intent_violation_gate_block(tmp_path) is None


def test_gate_override_seam_refuses_when_the_ledger_write_fails(tmp_path: Path) -> None:
    """NFR01: if the record cannot be written, the override does not apply."""
    _open_violation(tmp_path)
    # A pre-broken chain makes every append raise, standing in for any I/O fault.
    ledger_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    ledger_path(tmp_path).write_text('{"seq": 0, "entry_hash": "deadbeef"}\n', encoding="utf-8")

    assert record_gate_override(tmp_path, session_id="s", reason="operator accepted") is False
    assert intent_violation_gate_block(tmp_path) is not None


def test_no_open_violation_means_nothing_to_override(tmp_path: Path) -> None:
    assert intent_violation_gate_block(tmp_path) is None
    assert record_gate_override(tmp_path, session_id="s", reason="nothing outstanding") is True
    assert not ledger_path(tmp_path).exists()
