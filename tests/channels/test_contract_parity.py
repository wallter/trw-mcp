"""Contract-parity CI test for BeforeYouEditHintPayload vs sidecar fixture.

PRD-DIST-2405 FR03/FR04 (audit P0-11).

Verifies that the ``BeforeYouEditHintPayload`` Pydantic mirror in
``before_edit_hint.py`` remains compatible with the pinned sidecar
fixture at ``tests/fixtures/sample_sidecar_v0.json``.

The test FAILS if:
- Any required field in the fixture is absent from ``BeforeYouEditHintPayload``
- ``schema_version`` is not ``"risk-report-sidecar/v0"``
- ``model_validate(strict=True)`` raises ``ValidationError``

This is the canary for trw-distill schema drift — if trw-distill bumps
a required field without updating the trw-mcp mirror, this test fails.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "sample_sidecar_v0.json"


def test_fixture_file_exists() -> None:
    """Verify the pinned sidecar fixture is present."""
    assert FIXTURE_PATH.exists(), (
        f"Pinned sidecar fixture not found at {FIXTURE_PATH}. "
        "Run: touch tests/fixtures/sample_sidecar_v0.json with valid content."
    )


def test_fixture_schema_version() -> None:
    """Fixture must pin schema_version to 'risk-report-sidecar/v0'."""
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert data["schema_version"] == "risk-report-sidecar/v0", (
        f"Fixture schema_version={data['schema_version']!r}; expected 'risk-report-sidecar/v0'"
    )


def test_sidecar_fixture_parses_via_payload_model() -> None:
    """BeforeYouEditHintPayload.model_validate(strict=True) on fixture payload passes.

    This is the main contract-parity test (FR03). If trw-distill adds a
    new required field without updating the mirror here, this test fails
    with ValidationError.
    """
    from trw_mcp.tools.before_edit_hint import BeforeYouEditHintPayload

    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert data["schema_version"] == "risk-report-sidecar/v0"

    payload_dict = data["payload"]
    assert isinstance(payload_dict, dict), "Fixture 'payload' must be a dict"

    # strict=True: no type coercion — must match exact types
    hint = BeforeYouEditHintPayload.model_validate(payload_dict, strict=True)

    # Assert all declared required fields have non-None values
    assert hint.target_path, "target_path must be non-empty"
    assert isinstance(hint.target_exists_in_map, bool), "target_exists_in_map must be bool"
    assert isinstance(hint.importers, list), "importers must be a list"
    assert isinstance(hint.inferred_tests, list), "inferred_tests must be a list"
    assert isinstance(hint.doc_references, list), "doc_references must be a list"
    assert isinstance(hint.co_change_neighbors, list), "co_change_neighbors must be a list"
    assert isinstance(hint.hotspot_warnings, list), "hotspot_warnings must be a list"


def test_fixture_required_fields_present() -> None:
    """All required BeforeYouEditHintPayload fields are present in fixture payload."""
    from trw_mcp.tools.before_edit_hint import BeforeYouEditHintPayload

    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload_dict = data["payload"]

    # Get required fields (those without defaults or with non-None defaults)
    model_fields = BeforeYouEditHintPayload.model_fields
    required_field_names = {name for name, field in model_fields.items() if field.is_required()}

    for field_name in required_field_names:
        assert field_name in payload_dict, (
            f"Required field '{field_name}' missing from fixture payload. "
            "Update tests/fixtures/sample_sidecar_v0.json to add the field, "
            "or update BeforeYouEditHintPayload to make it optional."
        )


def test_fixture_schema_mismatch_raises_validation_error() -> None:
    """Adding an unknown required field to the fixture triggers ValidationError.

    Confirms the test is correctly catching schema drift. The model uses
    extra='forbid', so unknown fields raise a ValidationError.
    """
    from trw_mcp.tools.before_edit_hint import BeforeYouEditHintPayload

    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload_dict = dict(data["payload"])
    payload_dict["new_required_field_that_does_not_exist"] = "sentinel"

    with pytest.raises(ValidationError):
        BeforeYouEditHintPayload.model_validate(payload_dict, strict=True)


def test_compute_before_edit_hint_importable() -> None:
    """compute_before_edit_hint is importable from trw_mcp.tools.before_edit_hint."""
    from trw_mcp.tools.before_edit_hint import compute_before_edit_hint

    assert callable(compute_before_edit_hint)
    # Verify it has a docstring (FR01)
    assert compute_before_edit_hint.__doc__ is not None


def test_compute_before_edit_hint_importable_from_channel() -> None:
    """compute_before_edit_hint is re-exported from trw_mcp.channels.claude_code."""
    from trw_mcp.channels.claude_code import compute_before_edit_hint

    assert callable(compute_before_edit_hint)
