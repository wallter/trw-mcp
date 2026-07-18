"""PRD-CORE-205 FR04/FR05 — BuildReceipt outcome derivation + content freshness."""

from __future__ import annotations

import os
from pathlib import Path

from trw_mcp.models._evidence_core import ReceiptState
from trw_mcp.tools._evidence_gates import validate_build_receipt

from ._evidence_factories import (
    build_command,
    build_receipt,
    project_with_binding,
    validation_plan,
)


class TestBuildReceiptDerivesOutcomeAndRejectsContradiction:
    def test_complete_coverage_all_zero_exits_pass(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = validation_plan(binding, required_command_ids=("pytest", "mypy"))
        receipt = build_receipt(binding, plan)
        result = validate_build_receipt(receipt, plan, project)
        assert result.is_positive and result.state is ReceiptState.VALID

    def test_missing_required_command_is_incomplete(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = validation_plan(binding, required_command_ids=("pytest", "mypy"))
        # Only pytest realized; mypy missing.
        receipt = build_receipt(binding, plan, command_results=(build_command("pytest"),))
        result = validate_build_receipt(receipt, plan, project)
        assert not result.is_positive
        assert result.state is ReceiptState.PLAN_INCOMPLETE

    def test_arbitrary_command_does_not_substitute(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = validation_plan(binding, required_command_ids=("pytest", "mypy"))
        # pytest + an unrelated passing command, but mypy still missing.
        receipt = build_receipt(
            binding,
            plan,
            command_results=(build_command("pytest"), build_command("lint-extra")),
        )
        assert validate_build_receipt(receipt, plan, project).state is ReceiptState.PLAN_INCOMPLETE

    def test_nonzero_required_exit_fails(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = validation_plan(binding, required_command_ids=("pytest", "mypy"))
        receipt = build_receipt(
            binding,
            plan,
            command_results=(build_command("pytest", exit_code=1), build_command("mypy")),
        )
        result = validate_build_receipt(receipt, plan, project)
        assert not result.is_positive
        assert result.state is ReceiptState.INVALID

    def test_contradictory_legacy_boolean_invalidates(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = validation_plan(binding, required_command_ids=("pytest",))
        # All required pass (derived pass=True) but legacy says tests failed.
        receipt = build_receipt(binding, plan, legacy_tests_passed=False)
        result = validate_build_receipt(receipt, plan, project)
        assert not result.is_positive
        assert result.state is ReceiptState.INVALID

    def test_below_threshold_coverage_fails(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = validation_plan(binding, required_command_ids=("pytest",), coverage_threshold=90.0)
        receipt = build_receipt(binding, plan, coverage_pct=80.0)
        assert validate_build_receipt(receipt, plan, project).state is ReceiptState.INVALID

    def test_changed_plan_digest_invalid(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = validation_plan(binding, required_command_ids=("pytest",))
        receipt = build_receipt(binding, plan)
        other_plan = validation_plan(binding, required_command_ids=("pytest", "mypy"))
        assert validate_build_receipt(receipt, other_plan, project).state is ReceiptState.INVALID


class TestDeliveryRejectsContentStaleBuildReceipt:
    def test_delivery_rejects_content_stale_build_receipt(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code", "src/b.py": "more"})
        plan = validation_plan(binding, required_command_ids=("pytest",))
        receipt = build_receipt(binding, plan)
        assert validate_build_receipt(receipt, plan, project).is_positive

        # Byte mutation of a bound file -> stale, delivery rejects.
        (project / "src" / "a.py").write_text("MUTATED", encoding="utf-8")
        assert validate_build_receipt(receipt, plan, project).state is ReceiptState.STALE_CONTENT

    def test_unrelated_out_of_scope_change_does_not_invalidate(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code"})
        plan = validation_plan(binding, required_command_ids=("pytest",))
        receipt = build_receipt(binding, plan)
        # An unrelated file NOT in the run-owned scope changes.
        (project / "src" / "unrelated.py").write_text("other agent", encoding="utf-8")
        assert validate_build_receipt(receipt, plan, project).is_positive

    def test_deleted_bound_path_is_stale(self, tmp_path: Path) -> None:
        project, binding, _ = project_with_binding(tmp_path, {"src/a.py": "code", "src/b.py": "more"})
        plan = validation_plan(binding, required_command_ids=("pytest",))
        receipt = build_receipt(binding, plan)
        os.remove(project / "src" / "b.py")
        assert validate_build_receipt(receipt, plan, project).state is ReceiptState.STALE_CONTENT
