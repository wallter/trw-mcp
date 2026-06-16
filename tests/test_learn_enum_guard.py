"""core185-ENUM-UNGUARDED-3: trw_learn must validate enum args.

An invalid ``type`` / ``confidence`` / ``protection_tier`` passed to ``trw_learn``
previously reached ``_learning_to_memory_entry`` and raised a raw ``ValueError``
from the unconditional ``MemoryType(...)`` / ``Confidence(...)`` /
``ProtectionTier(...)`` construction -- escaping ``store_learning`` to the MCP
caller as an unhandled exception and breaking the stable ``LearnResultDict``
return-shape contract. ``trw_learn`` now guards these like ``trw_learn_update``.
"""

from __future__ import annotations

import pytest

from trw_mcp.tools._learning_module_helpers import _validate_learn_enums


def test_valid_enums_returns_none() -> None:
    assert _validate_learn_enums(type="pattern", confidence="verified", protection_tier="normal") is None


def test_invalid_type_returns_rejection() -> None:
    out = _validate_learn_enums(type="unknown", confidence="unverified", protection_tier="normal")
    assert out is not None
    assert out["status"] == "rejected"
    assert out["reason"] == "invalid_type"
    assert "unknown" in out["message"]


def test_invalid_confidence_returns_rejection() -> None:
    out = _validate_learn_enums(type="pattern", confidence="trusted", protection_tier="normal")
    assert out is not None
    assert out["status"] == "rejected"
    assert out["reason"] == "invalid_confidence"


def test_invalid_protection_tier_returns_rejection() -> None:
    out = _validate_learn_enums(type="pattern", confidence="unverified", protection_tier="top-secret")
    assert out is not None
    assert out["status"] == "rejected"
    assert out["reason"] == "invalid_protection_tier"


@pytest.mark.parametrize(
    ("bad_kwargs", "reason"),
    [
        ({"type": "unknown"}, "invalid_type"),
        ({"confidence": "trusted"}, "invalid_confidence"),
        ({"protection_tier": "top-secret"}, "invalid_protection_tier"),
    ],
)
def test_trw_learn_tool_returns_structured_rejection_not_exception(bad_kwargs: dict[str, str], reason: str) -> None:
    """The MCP tool returns a stable LearnResultDict, never a raw ValueError."""
    from tests.conftest import extract_tool_fn, make_test_server

    learn_fn = extract_tool_fn(make_test_server("learning"), "trw_learn")
    result = learn_fn(summary="a real durable gotcha", detail="why it matters", **bad_kwargs)
    assert isinstance(result, dict)
    assert result["status"] == "rejected"
    assert result["reason"] == reason
