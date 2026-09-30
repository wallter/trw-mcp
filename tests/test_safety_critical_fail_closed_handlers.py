"""FR04 fail-closed exception handlers on the delivery-gate path (coverage-audit gaps).

Each test drives a handler in ``_delivery_safety_critical_gate`` that the full-suite
coverage audit found never executed, through ``check_delivery_gates`` /
``safety_critical_gate_result`` where the handler is reachable that way. A receipt
whose review plan cannot be read, a receipts directory that cannot be listed, or an
unreadable task type must leave a safety-critical run BLOCKED, never delivered.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.test_safety_critical_adversarial_gate import (
    _BLOCK_KEY,
    _record_adversarial_review,
    _seed_run,
    _write_prd,
)
from trw_mcp.models._evidence_plans import ReviewVerdict
from trw_mcp.models._evidence_records import ReviewReceipt
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._delivery_helpers import check_delivery_gates
from trw_mcp.tools._delivery_safety_critical_gate import (
    _read_task_type,
    _receipt_is_verified_adversarial,
    find_satisfying_adversarial_receipt,
    safety_critical_gate_result,
)


@pytest.fixture(autouse=True)
def _project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))


def _satisfied_run(tmp_path: Path) -> tuple[Path, str]:
    """A safety-critical run holding one genuinely satisfying adversarial receipt."""
    _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
    run = _seed_run(tmp_path, scope="[PRD-SEC-900]")
    review = _record_adversarial_review(tmp_path, run, prd_id="PRD-SEC-900")
    receipt_id = str(review["review_receipt_id"])
    # Precondition: the un-sabotaged run is satisfied, so every block below is caused by the sabotage.
    assert find_satisfying_adversarial_receipt(run) == receipt_id
    assert _BLOCK_KEY not in check_delivery_gates(run, FileStateReader(), tmp_path / ".trw")
    return run, receipt_id


def _only_receipt(run: Path) -> ReviewReceipt:
    (path,) = sorted((run / "meta" / "receipts" / "review").glob("*.json"))
    return ReviewReceipt.model_validate_json(path.read_bytes())


def _plan_path(run: Path) -> Path:
    return run / "meta" / "plans" / "review" / f"{_only_receipt(run).review_plan_id}.json"


class TestUnreadablePlanIsNotSubstantive:
    @pytest.mark.parametrize(
        "sabotage",
        [
            pytest.param(lambda p: p.write_text("{not json", encoding="utf-8"), id="malformed"),
            pytest.param(lambda p: p.write_text("", encoding="utf-8"), id="empty"),
            pytest.param(lambda p: p.unlink(), id="missing"),
        ],
    )
    def test_receipt_with_unreadable_plan_does_not_satisfy_and_delivery_stays_blocked(
        self, tmp_path: Path, sabotage: Callable[[Path], Any]
    ) -> None:
        run, _ = _satisfied_run(tmp_path)
        plan = _plan_path(run)
        assert plan.is_file(), "precondition: a real review wrote a plan"

        sabotage(plan)

        assert _receipt_is_verified_adversarial(_only_receipt(run), run) is False
        assert find_satisfying_adversarial_receipt(run) is None
        gates = check_delivery_gates(run, FileStateReader(), tmp_path / ".trw")
        assert "safety_critical_adversarial_audit_missing" in gates[_BLOCK_KEY]
        assert safety_critical_gate_result(run).should_block is True


class TestReceiptDirectoryUnlistable:
    def test_oserror_on_receipt_stat_yields_no_evidence_and_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run, _ = _satisfied_run(tmp_path)
        real_stat = Path.stat

        def _stat(self: Path, *args: Any, **kwargs: Any) -> os.stat_result:
            if self.name.endswith(".json") and run in self.parents:
                raise PermissionError(13, "denied")
            return real_stat(self, *args, **kwargs)

        with monkeypatch.context() as scoped:
            scoped.setattr(Path, "stat", _stat)
            assert find_satisfying_adversarial_receipt(run) is None
            gates = check_delivery_gates(run, FileStateReader(), tmp_path / ".trw")
        assert "safety_critical_adversarial_audit_missing" in gates[_BLOCK_KEY]


class TestMalformedReceiptIsSkippedNotFatal:
    @pytest.mark.parametrize("corrupt_is_newer", [True, False], ids=["scanned-first", "scanned-last"])
    def test_valid_receipt_still_satisfies_alongside_a_malformed_one(
        self, tmp_path: Path, corrupt_is_newer: bool
    ) -> None:
        run, receipt_id = _satisfied_run(tmp_path)
        directory = run / "meta" / "receipts" / "review"
        (valid,) = list(directory.glob("*.json"))
        corrupt = directory / "0-corrupt.json"
        corrupt.write_text("{not json", encoding="utf-8")
        base = valid.stat().st_mtime
        os.utime(corrupt, (base + (100 if corrupt_is_newer else -100),) * 2)

        # The scan order is newest-first: prove the malformed file really precedes the valid one when it should.
        order = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        assert (order[0] == corrupt) is corrupt_is_newer

        assert find_satisfying_adversarial_receipt(run) == receipt_id

    def test_only_malformed_receipts_yield_no_evidence(self, tmp_path: Path) -> None:
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")
        directory = run / "meta" / "receipts" / "review"
        directory.mkdir(parents=True)
        (directory / "a.json").write_text("{not json", encoding="utf-8")

        assert find_satisfying_adversarial_receipt(run) is None
        assert safety_critical_gate_result(run).should_block is True


class TestUnreadableTaskTypeSelectsBlockingMode:
    def test_unreadable_run_yaml_reads_as_unknown(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        run = _seed_run(tmp_path, scope="[]")

        def _boom(self: FileStateReader, path: Path) -> dict[str, Any]:
            raise OSError("unreadable")

        monkeypatch.setattr(FileStateReader, "read_yaml", _boom)

        assert _read_task_type(run) == "unknown"

    def test_gate_mode_for_the_unreadable_task_type_is_the_blocking_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only ``unknown`` maps to a blocking mode here; every other task type is advisory.

        So the block below can only come from the handler returning ``unknown`` -- not from the
        scope, the config default, or some other task type.
        """
        from trw_mcp.tools import _deliver_gate_mode

        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]", task_type="docs")
        config = TRWConfig().model_copy(
            update={"deliver_gate_mode": "advisory", "deliver_gate_task_type_overrides": {"unknown": "block_all"}}
        )
        monkeypatch.setattr(_deliver_gate_mode, "get_config", lambda: config)

        # Control: the readable task type ("docs") is advisory -> reports, does not block.
        control = safety_critical_gate_result(run)
        assert control.should_block is False
        assert "Delivery advisory" in control.advisory

        # The FIRST run.yaml read (the task type) fails; the later scope read succeeds.
        real_read = FileStateReader.read_yaml
        calls = {"n": 0}

        def _first_call_fails(self: FileStateReader, path: Path) -> dict[str, Any]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("unreadable")
            return real_read(self, path)

        monkeypatch.setattr(FileStateReader, "read_yaml", _first_call_fails)

        outcome = safety_critical_gate_result(run)

        assert outcome.should_block is True
        assert "safety_critical_adversarial_audit_missing" in outcome.message


class TestNonSubstantiveReceiptEarlyReturns:
    @pytest.mark.parametrize(
        "update",
        [
            # The plan requires the other two rubrics, so only the adversarial early return can deny this.
            pytest.param(
                {"realized_rubric_ids": ("finding_schema_validation", "verdict_derivation")},
                id="no-adversarial-rubric",
            ),
            pytest.param({"verdict": ReviewVerdict.WARN}, id="verdict-warn"),
            pytest.param({"completed_at": ""}, id="not-structurally-substantive"),
            pytest.param({"findings": [], "adversarial_pass": False}, id="no-findings-no-adversarial-pass"),
        ],
    )
    def test_each_condition_alone_denies_an_otherwise_satisfying_receipt(
        self, tmp_path: Path, update: dict[str, Any]
    ) -> None:
        run, _ = _satisfied_run(tmp_path)
        receipt = _only_receipt(run)
        assert _receipt_is_verified_adversarial(receipt, run) is True, "control: the unmodified receipt satisfies"

        assert _receipt_is_verified_adversarial(receipt.model_copy(update=update), run) is False
