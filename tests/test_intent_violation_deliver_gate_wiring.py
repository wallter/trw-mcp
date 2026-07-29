"""PRD-SEC-013-FR07 lead-integration test: the open-violation marker reaches the deliver gate.

The implementer shipped ``intent_violation_gate_block()`` as a seam (no gate
extension point existed); the lead wired it into ``check_delivery_gates`` and
``_GATE_TABLE``. This test proves the wiring end-to-end: a persisted open
violation surfaces as ``intent_violation_block`` and the dispatch table treats
it as a STRUCTURED-override gate positioned before the advisory build gate.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.security.intent_contract.violations import record_open_violation
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._deliver_gate_dispatch import _GATE_TABLE, OverridePolicy
from trw_mcp.tools._delivery_helpers import check_delivery_gates


def _gates(trw_dir: Path) -> dict[str, object]:
    return dict(check_delivery_gates(None, FileStateReader(), trw_dir=trw_dir))


def test_open_violation_surfaces_as_gate_key(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    assert "intent_violation_block" not in _gates(trw_dir)

    record_open_violation(
        tmp_path,
        claim_id="INTENT-TEST-001",
        file_path="src/example.py",
        claim_text="No dual-submission window during migration",
        falsifier="pytest tests/test_example.py::test_guard",
        detail="falsifier failed post-edit",
    )
    fired = _gates(trw_dir)
    assert "intent_violation_block" in fired
    message = str(fired["intent_violation_block"])
    assert "INTENT-TEST-001" in message
    assert "BLOCKED" in message


def test_gate_table_row_is_structured_and_precedes_advisory_build_gate() -> None:
    keys = [g.key for g in _GATE_TABLE]
    assert "intent_violation_block" in keys
    row = next(g for g in _GATE_TABLE if g.key == "intent_violation_block")
    assert row.policy is OverridePolicy.STRUCTURED
    assert keys.index("intent_violation_block") < keys.index("build_gate_warning")
