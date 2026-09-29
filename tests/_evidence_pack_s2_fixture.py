"""Slice S2 fixture helpers for the PRD-CORE-323 evidence-pack tests (decisions and verdict).

Every helper writes through the production writer for its store: ``execute_checkpoint``
(checkpoints.jsonl plus its ``checkpoint`` event), ``FileEventLogger`` (events.jsonl and
its dated mirror), ``write_override_ledger`` (``.trw/overrides``) and
``DeliveryCoordinator`` (the delivery journal). Nothing here imports
``trw_mcp.evidence_pack``.
"""

from __future__ import annotations

from pathlib import Path

from tests._delivery_support import make_coordinator, make_uuid7, strong_capability
from tests._evidence_pack_fixture import Fixture

RUN_IDENTITY = ".trw/runs/task/run-1"


def log_event(fx: Fixture, event_type: str, data: dict[str, object]) -> None:
    from trw_mcp.state.persistence import FileEventLogger

    FileEventLogger().log_event(fx.run / "meta" / "events.jsonl", event_type, data)


def checkpoint(fx: Fixture, message: str) -> None:
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    result = execute_checkpoint(str(fx.run), message, None)
    assert result["recorded"] is True, result


def override_record(fx: Fixture, *, failed_command: str, residual_risk: str, run: Path | None = None) -> None:
    from trw_mcp.models._acceptable_failure import AcceptableFailureRecord
    from trw_mcp.tools._acceptable_failure_validation import ledger_run_id, write_override_ledger

    target = fx.run if run is None else run
    record = AcceptableFailureRecord(
        failed_command=failed_command,
        residual_risk=residual_risk,
        owner="fixture-owner",
        expiry_iso="2099-01-01",
    )
    ok, error = write_override_ledger(
        fx.root / ".trw", ledger_run_id(target), record, gate_type="build_gate", run_path=str(target)
    )
    assert ok, error


def journal_operation(root: Path, *, run_identity: str = RUN_IDENTITY, begin: str | None = "S01") -> str:
    """Claim a non-terminal operation for the run and start one step; return the operation id."""
    coord = make_coordinator(root / ".trw")
    did = make_uuid7()
    claim = coord.claim(delivery_id=did, capability_token=strong_capability(), run_identity=run_identity, pid=1)
    assert claim.status.value == "claimed", claim
    if begin is not None:
        coord.begin_step(did, begin, owner="w", pid=1)
    return did


def journal_proof_ref(root: Path, evidence_ref: str) -> str:
    """An operation whose D16 step carries operator-supplied ``proof_ref`` text (``reconcile_effect``)."""
    coord = make_coordinator(root / ".trw")
    did = make_uuid7()
    capability = strong_capability()
    coord.claim(delivery_id=did, capability_token=capability, run_identity=RUN_IDENTITY)
    coord.begin_step(did, "D16")
    coord.recover_after_crash(did)
    revision = coord.project_status(did)["revision"]
    assert isinstance(revision, int)
    result = coord.reconcile_effect(
        operation_id=did,
        effect_id="D16",
        applied=True,
        capability_token=capability,
        expected_revision=revision,
        reason="operator verified the trust ledger",
        evidence_ref=evidence_ref,
    )
    assert result.status.value == "ok", result
    return did


def compact_journal(root: Path) -> None:
    """Run the journal's own maintenance far in the future, so every operation becomes a tombstone."""
    from tests._delivery_support import days_ms
    from trw_mcp.tools._delivery_recovery import run_maintenance

    coord = make_coordinator(root / ".trw")
    conn = coord.store.connect()
    try:
        run_maintenance(coord.store, conn, coord._now_ms() + days_ms(120))
    finally:
        conn.close()


def section(pack_doc: dict[str, object], name: str) -> dict[str, object]:
    sections = pack_doc["sections"]
    assert isinstance(sections, dict)
    found = sections[name]
    assert isinstance(found, dict)
    return found
