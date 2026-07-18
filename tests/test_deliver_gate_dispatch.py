"""Behavioral tests for the single-table deliver-gate dispatch.

These exercise :func:`evaluate_delivery_gates` directly with hand-built
``gate_result`` mappings — no filesystem, no FastMCP server — so every gate key,
precedence rule, and override policy is asserted at the value level.

The dispatch table has two NO_ESCAPE hard gates, two STRUCTURED
(AcceptableFailureRecord) hard gates, and one ADVISORY warning. The warning
must stay advisory after task-type/config policy resolution.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from trw_mcp.tools import _deliver_gate_dispatch as gd
from trw_mcp.tools._deliver_gate_dispatch import (
    GateDescriptor,
    OverridePolicy,
    evaluate_delivery_gates,
)


def _run(
    gate_result: dict[str, object], *, allow_unverified: bool = False, reason: str = ""
) -> tuple[bool, dict[str, Any], list[str]]:
    results: dict[str, Any] = {}
    errors: list[str] = []
    blocked = evaluate_delivery_gates(
        gate_result,
        cast("Any", results),
        errors,
        None,  # resolved_run=None → no event logging
        cast("Any", "/tmp/trw"),
        allow_unverified,
        reason,
    )
    return blocked, results, errors


# --------------------------------------------------------------------------- #
# Table shape / precedence
# --------------------------------------------------------------------------- #


def test_gate_table_has_five_descriptors_in_precedence_order() -> None:
    keys = [d.key for d in gd._GATE_TABLE]
    assert keys == [
        "integration_review_block",
        "review_scope_block",
        "review_block",
        "delivery_blocked",
        "build_gate_warning",
    ]


def test_gate_table_policies_map_each_key_to_its_override_class() -> None:
    by_key = {d.key: d.policy for d in gd._GATE_TABLE}
    assert by_key["integration_review_block"] is OverridePolicy.NO_ESCAPE
    assert by_key["review_scope_block"] is OverridePolicy.NO_ESCAPE
    assert by_key["review_block"] is OverridePolicy.STRUCTURED
    assert by_key["delivery_blocked"] is OverridePolicy.STRUCTURED
    assert by_key["build_gate_warning"] is OverridePolicy.ADVISORY


def test_build_gate_warning_descriptor_is_advisory() -> None:
    desc = next(d for d in gd._GATE_TABLE if d.key == "build_gate_warning")
    assert isinstance(desc, GateDescriptor)
    assert desc.result_block_key == "build_gate_warning"


# --------------------------------------------------------------------------- #
# No gate fired
# --------------------------------------------------------------------------- #


def test_no_gates_fired_does_not_block() -> None:
    blocked, results, errors = _run({})
    assert blocked is False
    assert errors == []
    assert "success" not in results  # nothing touched the terminal keys


# --------------------------------------------------------------------------- #
# NO_ESCAPE phase
# --------------------------------------------------------------------------- #


def test_integration_review_block_hard_blocks_with_no_escape() -> None:
    blocked, results, errors = _run(
        {"integration_review_block": "integration review verdict=block"},
        allow_unverified=True,
        reason="please let me through",
    )
    assert blocked is True
    assert results["success"] is False
    assert errors == ["integration review verdict=block"]


def test_review_scope_block_hard_blocks_with_no_escape() -> None:
    blocked, results, errors = _run({"review_scope_block": "6 files, no review (R-01)"})
    assert blocked is True
    assert results["success"] is False
    assert errors == ["6 files, no review (R-01)"]


def test_no_escape_surfaces_cofiring_review_block_into_errors() -> None:
    # F4: a co-firing review_block message must not be silently dropped, but it is
    # NOT routed through its structured override (moot while a hard gate blocks).
    blocked, results, errors = _run(
        {
            "integration_review_block": "hard-A",
            "review_scope_block": "hard-B",
            "review_block": "human-review-block",
        },
        allow_unverified=True,
        reason="{}",
    )
    assert blocked is True
    assert errors == ["hard-A", "hard-B", "human-review-block"]
    # review_block was surfaced but NOT overridden here
    assert "acceptable_failure_record" not in results


# --------------------------------------------------------------------------- #
# STRUCTURED phase — review_block
# --------------------------------------------------------------------------- #


def test_review_block_without_override_hard_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    blocked, results, errors = _run({"review_block": "reviewer said block"})
    assert blocked is True
    assert results["review_block"] == "reviewer said block"
    assert results["success"] is False
    assert errors == ["reviewer said block"]


def test_review_block_valid_structured_override_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def _fake_apply(**kwargs: object) -> tuple[bool, str | None]:
        seen.update(kwargs)
        return True, None

    monkeypatch.setattr("trw_mcp.tools._acceptable_failure_validation.apply_structured_override", _fake_apply)
    blocked, results, errors = _run(
        {"review_block": "reviewer said block"},
        allow_unverified=True,
        reason='{"failed_command": "x"}',
    )
    assert blocked is False
    assert seen["gate_type"] == "review_block"
    assert errors == []


def test_review_block_invalid_record_blocks_with_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_apply(**kwargs: object) -> tuple[bool, str | None]:
        return False, "expiry_iso is in the past"

    monkeypatch.setattr("trw_mcp.tools._acceptable_failure_validation.apply_structured_override", _fake_apply)
    blocked, results, errors = _run(
        {"review_block": "reviewer said block"},
        allow_unverified=True,
        reason='{"expiry_iso": "2000-01-01"}',
    )
    assert blocked is True
    assert results["review_block"] == "reviewer said block"
    assert errors == ["expiry_iso is in the past"]


def test_review_block_ledger_persistence_failure_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid record is not an accepted exception until its ledger write succeeds."""

    def _fake_apply(**kwargs: object) -> tuple[bool, str | None]:
        return False, "acceptable-failure ledger persistence failed: disk full"

    monkeypatch.setattr("trw_mcp.tools._acceptable_failure_validation.apply_structured_override", _fake_apply)
    blocked, results, errors = _run(
        {"review_block": "reviewer said block"},
        allow_unverified=True,
        reason='{"failed_command": "pytest"}',
    )
    assert blocked is True
    assert results["review_block"] == "reviewer said block"
    assert results["success"] is False
    assert errors == ["acceptable-failure ledger persistence failed: disk full"]


# --------------------------------------------------------------------------- #
# STRUCTURED phase — delivery_blocked (deliver_gate_mode)
# --------------------------------------------------------------------------- #


def test_delivery_blocked_without_override_blocks_and_sets_missing_gate() -> None:
    blocked, results, errors = _run(
        {
            "delivery_blocked": "coding task requires build_check",
            "missing_gate": "build_check",
            "blocked_task_type": "coding",
        }
    )
    assert blocked is True
    assert results["delivery_blocked"] == "coding task requires build_check"
    assert results["missing_gate"] == "build_check"
    assert results["blocked_task_type"] == "coding"
    assert results["success"] is False
    assert errors == ["coding task requires build_check"]


def test_delivery_blocked_valid_override_proceeds_and_still_sets_missing_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "trw_mcp.tools._acceptable_failure_validation.apply_structured_override",
        lambda **kw: (True, None),
    )
    blocked, results, errors = _run(
        {"delivery_blocked": "blocked", "missing_gate": "build_check"},
        allow_unverified=True,
        reason='{"failed_command": "pytest"}',
    )
    assert blocked is False
    assert results["missing_gate"] == "build_check"


def test_delivery_blocked_ledger_persistence_failure_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trw_mcp.tools._acceptable_failure_validation.apply_structured_override",
        lambda **kw: (False, "acceptable-failure ledger persistence failed: permission denied"),
    )
    blocked, results, errors = _run(
        {"delivery_blocked": "coding task requires build_check", "missing_gate": "build_check"},
        allow_unverified=True,
        reason='{"failed_command": "pytest"}',
    )
    assert blocked is True
    assert results["delivery_blocked"] == "coding task requires build_check"
    assert results["success"] is False
    assert errors == ["acceptable-failure ledger persistence failed: permission denied"]


# --------------------------------------------------------------------------- #
# ADVISORY phase — build_gate_warning (soft)
# --------------------------------------------------------------------------- #


def test_build_gate_warning_without_override_remains_advisory() -> None:
    blocked, results, errors = _run({"build_gate_warning": "no successful build check found"})
    assert blocked is False
    assert results == {}
    assert errors == []


def test_build_gate_warning_ignores_free_text_override_arguments() -> None:
    blocked, results, errors = _run(
        {"build_gate_warning": "no build check"},
        allow_unverified=True,
        reason="doc-only change validated by source inspection",
    )
    assert blocked is False
    assert results == {}
    assert errors == []


def test_build_gate_warning_does_not_ledger_unneeded_structured_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "trw_mcp.tools._acceptable_failure_validation.apply_structured_override",
        lambda **kwargs: pytest.fail("advisory warnings must not consume override records"),
    )
    blocked, results, errors = _run(
        {"build_gate_warning": "no build check"},
        allow_unverified=True,
        reason='{"failed_command": "pytest", "residual_risk": "low", "owner": "me", "expiry_iso": "2099-01-01"}',
    )
    assert blocked is False
    assert results == {}
    assert errors == []


# --------------------------------------------------------------------------- #
# Invariants: ADVISORY skipped when delivery_blocked present; precedence
# --------------------------------------------------------------------------- #


def test_advisory_phase_skipped_when_delivery_blocked_present(monkeypatch: pytest.MonkeyPatch) -> None:
    # delivery_blocked + build_gate_warning co-fire (delivery_blocked is the hard
    # promotion of the same warning). A valid delivery_blocked override must NOT
    # then re-process build_gate_warning — else the same condition double-fires.
    monkeypatch.setattr(
        "trw_mcp.tools._acceptable_failure_validation.apply_structured_override",
        lambda **kw: (True, None),
    )
    blocked, results, errors = _run(
        {"delivery_blocked": "blocked", "build_gate_warning": "warn"},
        allow_unverified=True,
        reason='{"failed_command": "x"}',
    )
    assert blocked is False
    assert "build_gate_block" not in results
    assert "build_gate_override" not in results


def test_no_escape_takes_precedence_over_soft_build_gate() -> None:
    blocked, results, errors = _run(
        {"integration_review_block": "hard", "build_gate_warning": "soft"},
        allow_unverified=True,
        reason="whatever",
    )
    assert blocked is True
    assert errors == ["hard"]
    assert "build_gate_block" not in results  # soft gate never evaluated
    assert "build_gate_override" not in results


def test_review_block_takes_precedence_over_build_gate_warning() -> None:
    blocked, results, errors = _run({"review_block": "human block", "build_gate_warning": "soft"})
    assert blocked is True
    assert results["review_block"] == "human block"
    assert "build_gate_block" not in results
