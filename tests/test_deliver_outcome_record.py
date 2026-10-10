"""PRD-CORE-345 FR01: one durable deliver-outcome record per gate evaluation.

The first half is a CHARACTERIZATION of ``evaluate_delivery_gates`` written before the outcome
record existed: for each of its eight exits it pins the returned bool, the ``results`` keys and the
``errors`` list. The record must never change any of them (NFR01, and the operator rule that a
record-write fault leaves the gate decision unchanged).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from trw_mcp.tools import _deliver_gate_dispatch as gd

pytestmark = pytest.mark.repo_scan

_SELF_COMPUTED = (
    "_evaluate_build_authority",
    "_evaluate_acceptance_integrity",
    "_evaluate_plan_acceptance",
    "_evaluate_formation",
    "apply_requirement_drift_gate",
)
_VALID_RECORD = json.dumps({"failed_command": "pytest", "residual_risk": "r", "owner": "o", "expiry_iso": "2099-01-01"})

#: exit_site -> (gate_result, the self-computed evaluator that blocks, allow_unverified, reason)
_EXITS: dict[str, tuple[dict[str, object], str | None, bool, str]] = {
    "no_escape": ({"integration_review_block": "integration review missing"}, None, False, ""),
    "structured": ({"review_block": "review blocked"}, None, False, ""),
    "structured_refused": ({"review_block": "review blocked"}, None, True, "free text is not a record"),
    "build_authority": ({}, "_evaluate_build_authority", False, ""),
    "acceptance_integrity": ({}, "_evaluate_acceptance_integrity", False, ""),
    "plan_acceptance": ({}, "_evaluate_plan_acceptance", False, ""),
    "formation": ({}, "_evaluate_formation", False, ""),
    "requirement_drift": ({}, "apply_requirement_drift_gate", False, ""),
    "advisory": ({"build_gate_warning": "no build check"}, None, False, ""),
    "advisory_clean": ({}, None, False, ""),
    "structured_overridden": ({"review_block": "review blocked"}, None, True, _VALID_RECORD),
}

#: The pre-change oracle: (blocked, sorted results keys, errors). Captured on HEAD before FR01.
_ORACLE: dict[str, tuple[bool, list[str], list[str]]] = {
    "no_escape": (True, ["errors", "requirement_drift", "success"], ["integration review missing"]),
    "structured": (True, ["errors", "requirement_drift", "review_block", "success"], ["review blocked"]),
    "structured_refused": (
        True,
        ["acceptable_failure_error", "errors", "requirement_drift", "review_block", "success"],
        ["<parse error>"],
    ),
    "build_authority": (True, ["errors", "requirement_drift", "success"], ["blocked by _evaluate_build_authority"]),
    "acceptance_integrity": (
        True,
        ["errors", "requirement_drift", "success"],
        ["blocked by _evaluate_acceptance_integrity"],
    ),
    "plan_acceptance": (True, ["errors", "requirement_drift", "success"], ["blocked by _evaluate_plan_acceptance"]),
    "formation": (True, ["errors", "requirement_drift", "success"], ["blocked by _evaluate_formation"]),
    "requirement_drift": (
        True,
        ["errors", "requirement_drift", "success"],
        ["blocked by apply_requirement_drift_gate"],
    ),
    "advisory": (False, ["requirement_drift"], []),
    "advisory_clean": (False, ["requirement_drift"], []),
    "structured_overridden": (
        False,
        ["acceptable_failure_record", "requirement_drift", "truthfulness_gate_bypassed"],
        [],
    ),
}


def _blocker(name: str) -> Any:
    def block(results: dict[str, Any], errors: list[str], *_a: object, **_k: object) -> bool:
        errors.append(f"blocked by {name}")
        results["errors"] = errors
        results["success"] = False
        return True

    return block


def _run_dir(tmp_path: Path) -> Path:
    run = tmp_path / ".trw" / "runs" / "task" / "20260929T000000Z-abcd1234"
    (run / "meta").mkdir(parents=True)
    return run


def _evaluate(
    exit_name: str, monkeypatch: pytest.MonkeyPatch, resolved_run: Path | None, trw_dir: Path
) -> tuple[bool, dict[str, Any], list[str]]:
    gate_result, blocker, allow, reason = _EXITS[exit_name]
    for name in _SELF_COMPUTED:  # isolate the cascade: every self-computed gate passes unless chosen
        monkeypatch.setattr(gd, name, lambda *_a, **_k: False)
    if blocker == "apply_requirement_drift_gate":
        block = _blocker(blocker)
        monkeypatch.setattr(gd, blocker, lambda _d, _b, results, errors, *_a, **_k: block(results, errors))
    elif blocker is not None:
        monkeypatch.setattr(gd, blocker, _blocker(blocker))
    results: dict[str, Any] = {}
    errors: list[str] = []
    blocked = gd.evaluate_delivery_gates(
        dict(gate_result), cast("Any", results), errors, resolved_run, trw_dir, allow, reason
    )
    return blocked, results, errors


def _observed(
    blocked: bool, results: dict[str, Any], errors: list[str], exit_name: str
) -> tuple[bool, list[str], list[str]]:
    shown = ["<parse error>"] if exit_name == "structured_refused" and errors else list(errors)
    # outcome_record (PRD-CORE-345 B1) is additive; its own tests below pin it, the oracle pins the rest
    return blocked, sorted(k for k in results if k != "outcome_record"), shown


@pytest.mark.parametrize("exit_name", sorted(_EXITS))
@pytest.mark.parametrize("with_run", [False, True])
def test_characterization_every_exit_keeps_its_decision(
    exit_name: str, with_run: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _run_dir(tmp_path) if with_run else None
    observed = _observed(*_evaluate(exit_name, monkeypatch, run, tmp_path / ".trw"), exit_name)
    assert observed == _ORACLE[exit_name]


# --------------------------------------------------------------------------- #
# FR01: the record itself
# --------------------------------------------------------------------------- #

_EXPECTED: dict[str, tuple[str, str]] = {
    "no_escape": ("block", "no_escape"),
    "structured": ("block", "structured"),
    "structured_refused": ("block", "structured"),
    "build_authority": ("block", "build_authority"),
    "acceptance_integrity": ("block", "acceptance_integrity"),
    "plan_acceptance": ("block", "plan_acceptance"),
    "formation": ("block", "formation"),
    "requirement_drift": ("block", "requirement_drift"),
    "advisory": ("pass", "advisory"),
    "advisory_clean": ("pass", "advisory"),
    "structured_overridden": ("pass_with_exception", "advisory"),
}


def _records(run: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted((run / "meta" / "decisions").glob("outcome-*.json"))]


@pytest.mark.parametrize("exit_name", sorted(_EXITS))
def test_every_exit_writes_one_outcome_record(exit_name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = _run_dir(tmp_path)
    blocked, _results, _errors = _evaluate(exit_name, monkeypatch, run, tmp_path / ".trw")
    (record,) = _records(run)
    assert (record["decision"], record["exit_site"]) == _EXPECTED[exit_name]
    assert record["kind"] == "deliver_outcome" and record["run_id"] == run.name
    assert (record["decision"] == "block") is blocked
    assert not any(p.name.startswith("gate-outcome") for p in (run / "meta" / "decisions").iterdir())


def test_block_and_override_details_are_recorded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = _run_dir(tmp_path)
    _evaluate("structured_refused", monkeypatch, run, tmp_path / ".trw")
    (refused,) = _records(run)
    assert refused["blocked_gate_types"] == ["review_block"] and refused["override_refused"] is True
    run2 = _run_dir(tmp_path / "second")
    _evaluate("structured_overridden", monkeypatch, run2, tmp_path / ".trw")
    (overridden,) = _records(run2)
    assert overridden["overridden_gate_types"] == ["review_block"]
    assert overridden["exception_expires_at"] == "2099-01-01"
    text = json.dumps(overridden)
    for free_text in ("pytest", '"r"', '"o"'):  # failed_command / residual_risk / owner never copied
        assert free_text not in text


def test_available_receipts_are_listed_by_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = _run_dir(tmp_path)
    receipts = run / "meta" / "receipts" / "build"
    receipts.mkdir(parents=True)
    (receipts / "br-1.json").write_text("{}")
    _evaluate("advisory_clean", monkeypatch, run, tmp_path / ".trw")
    (record,) = _records(run)
    assert record["available_receipt_ids"] == ["br-1"]
    assert record["config_version_id"].startswith("cfg-")


@pytest.mark.parametrize("exit_name", ["no_escape", "structured_overridden", "advisory_clean", "formation"])
def test_a_record_write_fault_never_changes_the_decision(
    exit_name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from structlog.testing import capture_logs

    import trw_mcp._checkout_write as checkout_write

    real = checkout_write.write_checkout_file

    def boom(root: Path, path: Path, *a: object, **k: object) -> None:
        if Path(path).name.startswith("outcome-"):  # only the outcome record fails; S23 etc. still write
            raise OSError("disk full")
        real(root, path, *a, **k)  # type: ignore[arg-type]

    run = _run_dir(tmp_path)
    monkeypatch.setattr(checkout_write, "write_checkout_file", boom)
    with capture_logs() as logs:
        observed = _observed(*_evaluate(exit_name, monkeypatch, run, tmp_path / ".trw"), exit_name)
    assert observed == _ORACLE[exit_name]
    assert any(entry["event"] == "deliver_outcome_record_failed" for entry in logs)


def test_no_run_writes_no_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import trw_mcp.tools._deliver_outcome as outcome

    calls: list[object] = []
    monkeypatch.setattr(outcome, "_build", lambda *a: calls.append(a))
    _evaluate("advisory_clean", monkeypatch, None, tmp_path / ".trw")
    assert calls == []


def test_a_raise_out_of_the_cascade_writes_no_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = _run_dir(tmp_path)

    def boom(*_a: object, **_k: object) -> bool:
        raise RuntimeError("gate evaluator crashed")

    monkeypatch.setattr(gd, "_evaluate_plan_acceptance", boom)
    with pytest.raises(RuntimeError):
        gd.evaluate_delivery_gates({}, cast("Any", {}), [], run, tmp_path / ".trw", False, "")
    assert _records(run) == []


def test_census_every_cascade_return_names_a_closed_exit_site() -> None:
    """A new ``return`` in the cascade without a closed-table exit site fails here."""
    import ast
    import inspect
    import textwrap

    from trw_mcp.tools._deliver_outcome import EXIT_SITES

    tree = ast.parse(textwrap.dedent(inspect.getsource(gd._run_cascade)))
    returns = [node for node in ast.walk(tree) if isinstance(node, ast.Return)]
    sites = []
    for node in returns:
        call = node.value
        assert isinstance(call, ast.Call) and getattr(call.func, "id", "") == "exit_at", ast.unparse(node)
        first = call.args[0]
        assert isinstance(first, ast.Constant)
        sites.append(first.value)
    assert sorted(sites) == sorted(EXIT_SITES)


def test_the_outcome_record_is_never_read_back() -> None:
    """Audit, never authority: no src module but the writer and the span projector (which names the path
    as raw_ref) mentions outcome-*.json."""
    src = Path(gd.__file__).resolve().parents[1]
    import re

    pattern = re.compile(r"outcome-\*|outcome-\{|DeliverOutcome\.model_validate")
    readers = [
        p.name
        for p in src.rglob("*.py")
        if pattern.search(p.read_text(encoding="utf-8")) and p.name not in ("_deliver_outcome.py", "otel_verify.py")
    ]
    assert readers == []


@pytest.mark.parametrize("fault", ["config_version", "list_receipt_ids", "bad_exit_site"])
def test_other_record_faults_never_change_the_decision(
    fault: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Audit A4: every fault inside the record path is swallowed; the oracle holds."""
    import trw_mcp.state._evidence_persistence as ep
    import trw_mcp.tools._deliver_outcome as outcome

    def boom(*_a: object, **_k: object) -> object:
        raise RuntimeError(fault)

    if fault == "config_version":
        monkeypatch.setattr(outcome, "_config_version_id", boom)
    elif fault == "list_receipt_ids":
        monkeypatch.setattr(ep, "list_receipt_ids", boom)
    else:
        monkeypatch.setattr(outcome, "exit_at", lambda _site, blocked: blocked)  # no site -> strict model refuses
        monkeypatch.setattr(gd, "exit_at", lambda _site, blocked: blocked)
    run = _run_dir(tmp_path)
    for exit_name in ("no_escape", "advisory_clean", "structured_overridden"):
        observed = _observed(*_evaluate(exit_name, monkeypatch, run, tmp_path / ".trw"), exit_name)
        assert observed == _ORACLE[exit_name]
    assert _records(run) == []


@pytest.mark.skipif(not hasattr(__import__("os"), "symlink"), reason="needs symlinks")
@pytest.mark.parametrize("exit_name", ["no_escape", "advisory_clean"])
def test_a_refused_unsafe_write_lands_in_the_logged_branch_and_never_changes_the_decision(
    exit_name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run's ``meta`` dir was swapped for a symlink to an outside dir: the safe writer refuses, the fault is logged, nothing lands outside."""
    import shutil

    from structlog.testing import capture_logs

    run = _run_dir(tmp_path)
    reference = _observed(*_evaluate(exit_name, monkeypatch, _run_dir(tmp_path / "ref"), tmp_path / ".trw"), exit_name)
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.rmtree(run / "meta")
    (run / "meta").symlink_to(outside, target_is_directory=True)

    with capture_logs() as logs:
        observed = _observed(*_evaluate(exit_name, monkeypatch, run, tmp_path / ".trw"), exit_name)

    assert observed == reference == _ORACLE[exit_name]
    assert any(
        entry["event"] == "deliver_outcome_record_failed" and entry["error_type"] == "UnsafeWriteError"
        for entry in logs
    )
    assert list(outside.rglob("outcome-*.json")) == []  # nothing was written through the link


# --------------------------------------------------------------------------- #
# PRD-CORE-345 B1 (operator-confirmed 2026-09-30): a record fault is surfaced in the response
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("exit_name", ["no_escape", "advisory_clean", "structured_overridden"])
def test_a_written_record_is_reported_as_written(
    exit_name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _blocked, results, _errors = _evaluate(exit_name, monkeypatch, _run_dir(tmp_path), tmp_path / ".trw")
    assert results["outcome_record"] == "written"


@pytest.mark.parametrize("exit_name", ["no_escape", "advisory_clean"])
def test_no_run_is_reported_as_skipped(exit_name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _blocked, results, _errors = _evaluate(exit_name, monkeypatch, None, tmp_path / ".trw")
    assert results["outcome_record"] == "skipped:no-run"


@pytest.mark.parametrize("exit_name", ["no_escape", "structured_overridden", "advisory_clean", "formation"])
@pytest.mark.parametrize("step", ["write", "build"])
def test_a_record_fault_is_surfaced_in_the_response_and_the_decision_stands(
    exit_name: str, step: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import trw_mcp._checkout_write as checkout_write
    import trw_mcp.tools._deliver_outcome as outcome

    real = checkout_write.write_checkout_file

    def boom_write(root: Path, path: Path, *a: object, **k: object) -> None:
        if Path(path).name.startswith("outcome-"):
            raise OSError("disk full")
        real(root, path, *a, **k)  # type: ignore[arg-type]

    def boom_build(*_a: object, **_k: object) -> object:
        raise ValueError("cannot build")

    if step == "write":
        monkeypatch.setattr(checkout_write, "write_checkout_file", boom_write)
        expected = "failed:OSError"
    else:
        monkeypatch.setattr(outcome, "_build", boom_build)
        expected = "failed:ValueError"
    blocked, results, errors = _evaluate(exit_name, monkeypatch, _run_dir(tmp_path), tmp_path / ".trw")
    assert results["outcome_record"] == expected
    assert _observed(blocked, results, errors, exit_name) == _ORACLE[exit_name]
