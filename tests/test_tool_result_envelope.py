"""PRD-CORE-215 FR02/FR03/FR05: typed tool-result envelope + owner-registry matrices.

Created by the FR02 wave (schema-negative + redaction + legacy-conflict matrix);
extended by FR03 (owner registration / typed refusal) and FR05 (lossless CORE-208
projection parity). These are the schema-negative counterpart to the behavioral
acceptance tests in ``test_delivery_operations.py`` (FR02),
``test_delivery_operations_concurrency.py`` (FR03), and
``test_delivery_status_tool.py`` (FR05).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.models.tool_result import (
    MAX_DIAGNOSTIC_ENTRIES,
    MAX_DIAGNOSTIC_VALUE_CHARS,
    REDACTED_PLACEHOLDER,
    CeremonyExecutionClass,
    Outcome,
    RedactionState,
    RetrySafety,
    ToolResultEnvelope,
    TruncationState,
    redact_mapping,
)

# --- FR02: closed-vocabulary + schema-negative matrix ---


def test_outcome_vocabulary_is_closed() -> None:
    """The outcome vocabulary is exactly the four FR02 states."""
    assert {o.value for o in Outcome} == {"completed", "rejected", "accepted", "uncertain"}


@pytest.mark.parametrize("bad_outcome", ["success", "failed", "done", "", "COMPLETED"])
def test_unknown_outcome_string_is_rejected(bad_outcome: str) -> None:
    """A value outside the closed vocabulary cannot construct an envelope."""
    with pytest.raises(ValidationError):
        ToolResultEnvelope(outcome=bad_outcome)  # type: ignore[arg-type]


def test_extra_legacy_key_is_forbidden_at_construction() -> None:
    """extra='forbid' blocks a smuggled legacy outcome key from ever attaching."""
    with pytest.raises(ValidationError):
        ToolResultEnvelope(outcome=Outcome.REJECTED, success=True)  # type: ignore[call-arg]


def test_envelope_is_frozen() -> None:
    """The envelope is immutable once built."""
    env = ToolResultEnvelope(outcome=Outcome.COMPLETED)
    with pytest.raises(ValidationError):
        env.outcome = Outcome.REJECTED  # type: ignore[misc]


def test_reason_code_over_bound_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolResultEnvelope(outcome=Outcome.COMPLETED, reason_code="x" * 129)


def test_diagnostics_entry_count_is_bounded() -> None:
    too_many = {f"k{i}": "v" for i in range(MAX_DIAGNOSTIC_ENTRIES + 1)}
    with pytest.raises(ValidationError):
        ToolResultEnvelope(outcome=Outcome.COMPLETED, diagnostics=too_many)


def test_diagnostics_value_length_is_bounded() -> None:
    with pytest.raises(ValidationError):
        ToolResultEnvelope(
            outcome=Outcome.COMPLETED,
            diagnostics={"note": "x" * (MAX_DIAGNOSTIC_VALUE_CHARS + 1)},
        )


def test_negative_output_estimate_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolResultEnvelope(outcome=Outcome.COMPLETED, output_estimate=-1)


# --- FR02 + NFR03: secret redaction in serialization ---


@pytest.mark.parametrize(
    "secret_key",
    ["api_key", "password", "auth_token", "client_secret", "X-Api-Key", "access_key"],
)
def test_secret_diagnostic_keys_are_redacted_on_dump(secret_key: str) -> None:
    """Any secret-looking diagnostic key is redacted in the serialized envelope."""
    env = ToolResultEnvelope(
        outcome=Outcome.COMPLETED,
        diagnostics={secret_key: "hunter2", "safe_note": "kept"},
    )
    dumped = env.model_dump(mode="json")
    assert dumped["diagnostics"][secret_key] == REDACTED_PLACEHOLDER
    assert dumped["diagnostics"]["safe_note"] == "kept"
    # The raw secret value never appears anywhere in the serialized payload.
    assert "hunter2" not in repr(dumped)


def test_redact_mapping_leaves_non_secret_values() -> None:
    out = redact_mapping({"token": "abc", "count": "3"})
    assert out == {"token": REDACTED_PLACEHOLDER, "count": "3"}


def test_serialization_is_one_envelope_per_state() -> None:
    """A single outcome serializes to a single object (no dual-state payload)."""
    env = ToolResultEnvelope(outcome=Outcome.ACCEPTED, operation_id="op-1")
    dumped = env.model_dump(mode="json")
    assert dumped["outcome"] == "accepted"
    assert isinstance(dumped, dict)
    assert dumped["operation_id"] == "op-1"


# --- FR02: contradictory legacy keys cannot flip the typed outcome ---


@pytest.mark.parametrize(
    "legacy",
    [
        {"success": True},
        {"status": "success"},
        {"outcome": "completed"},
        {"result": "ok"},
        {"success": True, "status": "success", "outcome": "completed"},
    ],
)
def test_positive_legacy_cannot_flip_rejected_outcome(legacy: dict[str, object]) -> None:
    """A legacy dict claiming success cannot override a typed REJECTED outcome."""
    env = ToolResultEnvelope.from_legacy(outcome=Outcome.REJECTED, legacy=legacy, reason_code="conflict")
    assert env.outcome is Outcome.REJECTED
    assert env.legacy_conflicts  # every contradicting key was recorded
    # The recorded conflict names appear only as diagnostics, never as outcome.
    assert env.model_dump(mode="json")["outcome"] == "rejected"


def test_negative_legacy_cannot_flip_completed_outcome() -> None:
    env = ToolResultEnvelope.from_legacy(outcome=Outcome.COMPLETED, legacy={"success": False, "status": "error"})
    assert env.outcome is Outcome.COMPLETED
    assert set(env.legacy_conflicts) == {"success", "status"}


def test_agreeing_legacy_records_no_conflict() -> None:
    env = ToolResultEnvelope.from_legacy(outcome=Outcome.COMPLETED, legacy={"success": True, "status": "completed"})
    assert env.legacy_conflicts == ()


def test_accepted_and_completed_are_both_positive_no_false_conflict() -> None:
    """A generic positive legacy flag must not falsely conflict with ACCEPTED."""
    env = ToolResultEnvelope.from_legacy(outcome=Outcome.ACCEPTED, legacy={"success": True})
    assert env.legacy_conflicts == ()


def test_from_legacy_redacts_secret_legacy_diagnostics() -> None:
    env = ToolResultEnvelope.from_legacy(
        outcome=Outcome.COMPLETED,
        legacy={"status": "completed"},
        diagnostics={"api_key": "leak-me", "note": "fine"},
    )
    assert env.redaction_state is RedactionState.REDACTED
    dumped = env.model_dump(mode="json")
    assert dumped["diagnostics"]["api_key"] == REDACTED_PLACEHOLDER
    assert "leak-me" not in repr(dumped)


def test_execution_class_defaults_to_synchronous_only() -> None:
    env = ToolResultEnvelope(outcome=Outcome.COMPLETED)
    assert env.execution_class is CeremonyExecutionClass.SYNCHRONOUS_ONLY
    assert env.retry_safety is RetrySafety.UNKNOWN
    assert env.truncation_state is TruncationState.NONE
