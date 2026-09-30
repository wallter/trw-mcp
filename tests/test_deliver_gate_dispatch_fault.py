"""E2E-DELIVER-GATE-DISPATCH-FAIL-CLOSED (b1): a fault inside a self-computing deliver gate is never a pass.

CONSTITUTION §1.a outranks the old NFR02 "degrade to no-block" clause: an unexpected exception in the
CORE-213 transition gate, the CORE-249 plan-acceptance gate, or the FIX-140 build-authority gate BLOCKS
under a blocking mode and is a NAMED warning under warn/advisory -- never a new hard block there, never
silent. The verdict is set before any diagnostics, so a failing logger cannot undo it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from trw_mcp.tools import _deliver_gate_selfcomputed as sc

_SC = "trw_mcp.tools._deliver_gate_selfcomputed"


def _boom(*_a: object, **_k: object) -> Any:
    raise RuntimeError("injected fault")


def _run(tmp_path: Path, run_yaml: str | None = "task_type: coding\n") -> Path:
    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    if run_yaml is not None:
        (run / "meta" / "run.yaml").write_text(run_yaml, encoding="utf-8")
    return run


def _call(gate: Any, run: Path | None, tmp_path: Path) -> tuple[bool, dict[str, Any], list[str]]:
    results: dict[str, Any] = {}
    errors: list[str] = []
    blocked = gate(cast("Any", results), errors, run, tmp_path / ".trw", False, "")
    return blocked, results, errors


def _mode(monkeypatch: pytest.MonkeyPatch, deliver: str, transition: str = "block") -> None:
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.resolve_gate_mode", lambda _t: deliver)
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.resolve_gate_mode_with_source", lambda _t: (deliver, False))

    class _Cfg:
        prd_transition_gate = transition

    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: _Cfg())


# --- CORE-213 transition gate (:66) ----------------------------------------------------------------------------


def test_transition_gate_fault_blocks_in_block_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mode(monkeypatch, "block_coding", "block")
    monkeypatch.setattr("trw_mcp.tools._prd_transition_gate.evaluate_transition_gate", _boom)
    blocked, results, errors = _call(sc.evaluate_acceptance_integrity, _run(tmp_path), tmp_path)
    assert blocked is True and results["success"] is False
    assert "could not be evaluated" in results["acceptance_integrity_block"] and "RuntimeError" in errors[0]


def test_transition_gate_fault_is_a_named_warning_in_warn_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mode(monkeypatch, "block_coding", "warn")
    monkeypatch.setattr("trw_mcp.tools._prd_transition_gate.evaluate_transition_gate", _boom)
    blocked, results, errors = _call(sc.evaluate_acceptance_integrity, _run(tmp_path), tmp_path)
    assert blocked is False and errors == []
    assert "could not be evaluated" in results["acceptance_integrity_warning"]


# --- CORE-249 plan-acceptance gate (:128) ----------------------------------------------------------------------


def test_plan_acceptance_fault_blocks_in_block_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mode(monkeypatch, "block_coding")
    monkeypatch.setattr("trw_mcp.tools._plan_acceptance_gate.evaluate_plan_acceptance", _boom)
    blocked, results, _ = _call(sc.evaluate_plan_acceptance, _run(tmp_path), tmp_path)
    assert blocked is True and "could not be evaluated" in results["plan_acceptance_block"]


@pytest.mark.parametrize("bad", ["task_type: [unclosed\n", "null\n", "symlink"], ids=["invalid", "null", "symlink"])
def test_an_unreadable_run_yaml_no_longer_skips_the_plan_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad: str
) -> None:
    """The LIVE trigger: an unparseable (or null, or symlinked) run.yaml skipped the gate entirely."""
    _mode(monkeypatch, "block_coding")
    run = _run(tmp_path, None if bad == "symlink" else bad)
    if bad == "symlink":
        target = tmp_path / "elsewhere.yaml"
        target.write_text("task_type: docs\n", encoding="utf-8")
        (run / "meta" / "run.yaml").symlink_to(target)
    blocked, results, _ = _call(sc.evaluate_plan_acceptance, run, tmp_path)
    assert blocked is True and "run.yaml" in results["plan_acceptance_block"]


def test_plan_acceptance_fault_is_a_named_warning_when_advisory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mode(monkeypatch, "advisory")
    blocked, results, errors = _call(sc.evaluate_plan_acceptance, _run(tmp_path, "task_type: [unclosed\n"), tmp_path)
    assert blocked is False and errors == []
    assert "could not be evaluated" in results["plan_acceptance_warning"]


def test_an_absent_run_yaml_is_still_evaluated_as_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mode(monkeypatch, "block_coding")
    seen: list[dict[str, object]] = []

    def _record(_run: Path, data: dict[str, object]) -> Any:
        from trw_mcp.tools._plan_acceptance_gate import PlanAcceptanceOutcome

        seen.append(data)
        return PlanAcceptanceOutcome()

    monkeypatch.setattr("trw_mcp.tools._plan_acceptance_gate.evaluate_plan_acceptance", _record)
    blocked, _, _ = _call(sc.evaluate_plan_acceptance, _run(tmp_path, None), tmp_path)
    assert blocked is False and seen == [{}]


# --- FIX-140 build authority (:333) ----------------------------------------------------------------------------


def test_pinned_build_authority_fault_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mode(monkeypatch, "advisory")  # FR05 is mode-independent on a pinned run: a fault blocks there too
    monkeypatch.setattr("trw_mcp.tools._delivery_event_checks.latest_build_check_failed_for_run", _boom)
    blocked, results, _ = _call(sc.evaluate_build_authority, _run(tmp_path), tmp_path)
    assert blocked is True and "could not be evaluated" in results["delivery_blocked"]
    assert results["missing_gate"] == "build_check"


@pytest.mark.parametrize(("mode", "expect_block"), [("block_coding", True), ("advisory", False)])
def test_unpinned_build_authority_fault_follows_the_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, expect_block: bool
) -> None:
    _mode(monkeypatch, mode)
    monkeypatch.setattr("trw_mcp.tools._delivery_event_checks.unpinned_build_failure_recorded", _boom)
    blocked, results, errors = _call(sc.evaluate_build_authority, None, tmp_path)
    assert blocked is expect_block
    if expect_block:
        assert "could not be evaluated" in results["delivery_blocked"]
    else:
        assert errors == [] and "could not be evaluated" in results["build_authority_warning"]


# --- ordering: the verdict first, diagnostics after ------------------------------------------------------------


@pytest.mark.parametrize(
    ("gate", "target", "key"),
    [
        (sc.evaluate_acceptance_integrity, "trw_mcp.tools._prd_transition_gate.evaluate_transition_gate",
         "acceptance_integrity_block"),
        (sc.evaluate_plan_acceptance, "trw_mcp.tools._plan_acceptance_gate.evaluate_plan_acceptance",
         "plan_acceptance_block"),
        (sc.evaluate_build_authority, "trw_mcp.tools._delivery_event_checks.latest_build_check_failed_for_run",
         "delivery_blocked"),
    ],
    ids=["core213", "core249", "fix140"],
)  # fmt: skip
def test_a_failing_logger_cannot_undo_the_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gate: Any, target: str, key: str
) -> None:
    _mode(monkeypatch, "block_coding", "block")
    monkeypatch.setattr(target, _boom)

    class _BrokenLogger:
        def __getattr__(self, _name: str) -> Any:
            return _boom

    monkeypatch.setattr(f"{_SC}.logger", _BrokenLogger())
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_dispatch.logger", _BrokenLogger())
    blocked, results, _ = _call(gate, _run(tmp_path), tmp_path)
    assert blocked is True and results[key]


# --- the posture itself cannot fail open -----------------------------------------------------------------------


def test_an_unresolvable_deliver_mode_is_enforced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _mode(monkeypatch, "block_coding")
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.resolve_gate_mode", _boom)
    monkeypatch.setattr("trw_mcp.tools._plan_acceptance_gate.evaluate_plan_acceptance", _boom)
    blocked, _, _ = _call(sc.evaluate_plan_acceptance, _run(tmp_path), tmp_path)
    assert blocked is True


def test_an_unreadable_config_uses_the_transition_gates_declared_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mode(monkeypatch, "block_coding")
    monkeypatch.setattr("trw_mcp.models.config.get_config", _boom)
    monkeypatch.setattr("trw_mcp.tools._prd_transition_gate.evaluate_transition_gate", _boom)
    blocked, _, _ = _call(sc.evaluate_acceptance_integrity, _run(tmp_path), tmp_path)
    assert blocked is True  # prd_transition_gate declares "block"


# --- codex r1 KIs: an accepted record clears the block; the verdict never waits on str(exc) ----------------------


def _accepted_record() -> str:
    import json
    from datetime import datetime, timedelta, timezone

    return json.dumps({"failed_command": "trw_deliver gate", "residual_risk": "gate fault, tracked follow-up",
                       "owner": "census", "expiry_iso": (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()})  # fmt: skip


_GATES = [
    (sc.evaluate_acceptance_integrity, "trw_mcp.tools._prd_transition_gate.evaluate_transition_gate",
     "acceptance_integrity_block"),
    (sc.evaluate_plan_acceptance, "trw_mcp.tools._plan_acceptance_gate.evaluate_plan_acceptance",
     "plan_acceptance_block"),
    (sc.evaluate_build_authority, "trw_mcp.tools._delivery_event_checks.latest_build_check_failed_for_run",
     "delivery_blocked"),
]  # fmt: skip


@pytest.mark.parametrize(("gate", "target", "key"), _GATES, ids=["core213", "core249", "fix140"])
def test_an_accepted_record_releases_a_fault_block_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gate: Any, target: str, key: str
) -> None:
    """codex r1 KI: an accepted acceptable-failure record leaves no provisional block key and no success=False."""
    _mode(monkeypatch, "block_coding", "block")
    monkeypatch.setattr(target, _boom)
    run = _run(tmp_path)
    (tmp_path / ".trw").mkdir(exist_ok=True)
    results: dict[str, Any] = {}
    errors: list[str] = []
    blocked = gate(cast("Any", results), errors, run, tmp_path / ".trw", True, _accepted_record())
    assert blocked is False, results
    assert key not in results and results.get("success") is not False and errors == []


class _HostileStr(Exception):
    def __str__(self) -> str:
        raise RuntimeError("__str__ explodes")


@pytest.mark.parametrize(("gate", "target", "key"), _GATES, ids=["core213", "core249", "fix140"])
def test_a_hostile_exception_str_cannot_escape_before_the_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gate: Any, target: str, key: str
) -> None:
    def _raise_hostile(*_a: object, **_k: object) -> Any:
        raise _HostileStr

    _mode(monkeypatch, "block_coding", "block")
    monkeypatch.setattr(target, _raise_hostile)
    blocked, results, _ = _call(gate, _run(tmp_path), tmp_path)
    assert blocked is True and "_HostileStr" in results[key]
