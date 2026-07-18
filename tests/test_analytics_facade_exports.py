"""Public and compatibility contracts for the analytics facade."""

from __future__ import annotations

from trw_mcp.state import analytics
from trw_mcp.state.analytics import core


def test_analytics_facade_exports_only_public_names() -> None:
    assert not {name for name in analytics.__all__ if name.startswith("_")}
    assert "normalize_audit_learning_metadata" in analytics.__all__


def test_analytics_facade_keeps_private_compatibility_attributes() -> None:
    for name in ("_safe_float", "_safe_int", "_TOPIC_KEYWORD_MAP"):
        assert hasattr(analytics, name)


def test_analytics_facade_public_export_preserves_identity() -> None:
    assert analytics.normalize_audit_learning_metadata is core.normalize_audit_learning_metadata
