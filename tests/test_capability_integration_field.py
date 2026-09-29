"""PRD-CORE-320 FR01/NFR02/NFR03 — capability integration status fields.

Pure model/typing tests, no filesystem I/O — unit tier.
"""

from __future__ import annotations

from typing import cast, get_type_hints

import pytest
from pydantic import ValidationError

from trw_mcp.models._evidence_plans import BuildCommandResult, CommandClass
from trw_mcp.models.typed_dicts._tools import DeliverResultDict

pytestmark = pytest.mark.unit

_BASE_KWARGS = {
    "command_id": "unit-test",
    "label": "unit test command",
    "command_class": CommandClass.TEST,
    "exit_code": 0,
}


def test_integration_defaults_to_not_applicable() -> None:
    result = BuildCommandResult(**_BASE_KWARGS)
    assert result.integration == "not_applicable"
    assert result.call_site == ""


def test_malformed_integration_value_is_rejected_not_coerced_to_wired() -> None:
    with pytest.raises(ValidationError):
        BuildCommandResult(**_BASE_KWARGS, integration="definitely_wired_trust_me")


@pytest.mark.parametrize("bad_value", ["", "Wired", "WIRED", "wired ", None, 1])
def test_every_malformed_integration_variant_is_rejected(bad_value: object) -> None:
    with pytest.raises(ValidationError):
        BuildCommandResult(**_BASE_KWARGS, integration=bad_value)


@pytest.mark.parametrize("value", ["wired", "isolated", "not_applicable"])
def test_valid_integration_values_are_accepted(value: str) -> None:
    result = BuildCommandResult(**_BASE_KWARGS, integration=value)
    assert result.integration == value


def test_old_payload_without_new_fields_still_parses_unchanged() -> None:
    """NFR03: an archived receipt written before this PRD still validates."""
    archived_payload = {
        "command_id": "archived-command",
        "label": "archived label",
        "command_class": CommandClass.TEST,
        "exit_code": 0,
        "test_count": 12,
        "failure_count": 0,
    }
    result = BuildCommandResult.model_validate(archived_payload)
    assert result.integration == "not_applicable"
    assert result.call_site == ""
    assert result.test_count == 12


def test_call_site_is_bounded_text() -> None:
    from trw_mcp.models._evidence_core import EvidenceLimits

    oversized = "x" * (EvidenceLimits.MAX_FREE_TEXT_BYTES + 1)
    with pytest.raises(ValidationError):
        BuildCommandResult(**_BASE_KWARGS, call_site=oversized)


def test_fields_are_frozen() -> None:
    result = BuildCommandResult(**_BASE_KWARGS)
    with pytest.raises(ValidationError):
        result.integration = "wired"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        result.call_site = "somewhere.py:1"  # type: ignore[misc]


def test_wired_result_names_a_call_site() -> None:
    result = BuildCommandResult(**_BASE_KWARGS, integration="wired", call_site="pkg/mod.py:42")
    assert result.integration == "wired"
    assert result.call_site == "pkg/mod.py:42"


def test_deliver_result_dict_gains_integration_isolated_warning_key() -> None:
    hints = get_type_hints(DeliverResultDict)
    assert "integration_isolated_warning" in hints


def test_deliver_result_dict_old_payload_without_the_new_key_still_valid() -> None:
    """NFR03: a caller that never sets the new key sees unchanged behavior."""
    old_payload = cast(
        "DeliverResultDict",
        {"run_path": "/tmp/some-run", "success": True, "critical_steps_completed": 3},
    )
    assert old_payload.get("integration_isolated_warning") is None
    assert old_payload["success"] is True


def test_deliver_result_dict_can_carry_the_new_key() -> None:
    payload = cast(
        "DeliverResultDict",
        {"integration_isolated_warning": "capability X reported isolated"},
    )
    assert payload["integration_isolated_warning"] == "capability X reported isolated"
