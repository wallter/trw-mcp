"""Public and compatibility contracts for the validation facade."""

from __future__ import annotations

from trw_mcp.state import validation
from trw_mcp.state.validation import _prd_scoring_wiring


def test_validation_facade_exports_only_public_names() -> None:
    assert not {name for name in validation.__all__ if name.startswith("_")}
    assert "extract_wiring_warnings" in validation.__all__


def test_validation_facade_keeps_private_compatibility_attributes() -> None:
    for name in ("_CHECKBOX_RE", "_best_effort_build_check", "_check_prd_enforcement"):
        assert hasattr(validation, name)


def test_validation_facade_public_export_preserves_identity() -> None:
    assert validation.extract_wiring_warnings is _prd_scoring_wiring.extract_wiring_warnings
