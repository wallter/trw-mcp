"""CORE-205 GateDecision production dispatch and persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from trw_mcp.models.gate_decision import GateDecision, GateOverridePolicy, GateStatus
from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates


def test_typed_decision_drives_public_projection_and_persists(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    results: dict[str, Any] = {}
    errors: list[str] = []

    blocked = evaluate_delivery_gates(
        {
            "delivery_blocked": "typed build evidence is missing",
            "missing_gate": "build_check",
            "blocked_task_type": "coding",
        },
        cast("Any", results),
        errors,
        run,
        tmp_path / ".trw",
        False,
        "",
    )

    assert blocked is True
    assert results["delivery_blocked"] == "typed build evidence is missing"
    assert results["missing_gate"] == "build_check"
    files = list((run / "meta" / "decisions").glob("gate-*.json"))
    assert len(files) == 1
    decision = GateDecision.model_validate_json(files[0].read_bytes())
    assert decision.gate_id == "delivery_blocked"
    assert decision.status is GateStatus.BLOCK
    assert decision.override_policy is GateOverridePolicy.STRUCTURED
    assert decision.missing_evidence == ("build_check",)
    assert decision.task_type == "coding"
