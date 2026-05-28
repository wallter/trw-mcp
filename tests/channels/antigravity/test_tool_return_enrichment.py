"""Tests for channels/antigravity/_tool_return_enrichment.py.

PRD-DIST-2404 FR19.
"""

from __future__ import annotations

import pytest


def test_default_tier_for_antigravity_is_t1() -> None:
    """FR19: AG-04 default tier for antigravity-cli is T1."""
    from trw_mcp.channels.antigravity._tool_return_enrichment import (
        get_default_tier_for_antigravity,
    )

    assert get_default_tier_for_antigravity() == "T1"


def test_should_emit_enrichment_true_for_antigravity() -> None:
    """FR19: should_emit_enrichment returns True for antigravity-cli."""
    from trw_mcp.channels.antigravity._tool_return_enrichment import should_emit_enrichment

    assert should_emit_enrichment("antigravity-cli") is True


def test_should_emit_enrichment_false_for_other_client() -> None:
    """FR19: should_emit_enrichment returns False for non-antigravity client."""
    from trw_mcp.channels.antigravity._tool_return_enrichment import should_emit_enrichment

    assert should_emit_enrichment("codex") is False
    assert should_emit_enrichment("claude-code") is False
    assert should_emit_enrichment("unknown") is False


def test_should_emit_enrichment_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR19: should_emit_enrichment auto-detects from TRW_CLIENT_PROFILE env var."""
    from trw_mcp.channels.antigravity._tool_return_enrichment import should_emit_enrichment

    monkeypatch.setenv("TRW_CLIENT_PROFILE", "antigravity-cli")
    result = should_emit_enrichment()  # no explicit client
    assert result is True


def test_emit_ag04_tool_return_is_fail_open(tmp_path: object) -> None:
    """FR19: emit_ag04_tool_return never raises even on I/O error."""
    from trw_mcp.channels.antigravity._tool_return_enrichment import emit_ag04_tool_return

    # Should not raise regardless of environment.
    emit_ag04_tool_return(tool_name="trw_before_edit_hint", client="antigravity-cli")
    emit_ag04_tool_return(tool_name="trw_codebase_risk_report")
    emit_ag04_tool_return(tool_name="trw_entity_risk_map", client="unknown")


def test_ag04_channel_id_constant() -> None:
    """FR19: AG04_CHANNEL_ID has expected value."""
    from trw_mcp.channels.antigravity._tool_return_enrichment import AG04_CHANNEL_ID

    assert AG04_CHANNEL_ID == "ag-04-tool-return-enrichment"
