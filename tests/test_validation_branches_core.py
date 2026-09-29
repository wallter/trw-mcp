"""Extra coverage tests for trw_mcp/state/validation.py."""

from __future__ import annotations

from trw_mcp.state.validation import (
    _is_validate_pass,
)


class TestIsValidatePass:
    """Unit tests for _is_validate_pass predicate."""

    def test_returns_true_for_phase_check_validate_valid(self) -> None:
        event = {"event": "phase_check", "data": {"phase": "validate", "valid": True}}
        assert _is_validate_pass(event) is True

    def test_returns_false_for_wrong_event_name(self) -> None:
        event = {"event": "run_init", "data": {"phase": "validate", "valid": True}}
        assert _is_validate_pass(event) is False

    def test_returns_false_when_data_not_dict(self) -> None:
        event = {"event": "phase_check", "data": "not_a_dict"}
        assert _is_validate_pass(event) is False

    def test_returns_false_when_data_is_none(self) -> None:
        event: dict[str, object] = {"event": "phase_check", "data": None}
        assert _is_validate_pass(event) is False

    def test_returns_false_for_different_phase(self) -> None:
        event = {"event": "phase_check", "data": {"phase": "implement", "valid": True}}
        assert _is_validate_pass(event) is False

    def test_returns_false_when_valid_is_false(self) -> None:
        event = {"event": "phase_check", "data": {"phase": "validate", "valid": False}}
        assert _is_validate_pass(event) is False

    def test_returns_false_when_no_data_key(self) -> None:
        event: dict[str, object] = {"event": "phase_check"}
        assert _is_validate_pass(event) is False
