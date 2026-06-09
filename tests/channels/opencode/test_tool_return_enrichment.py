"""Tests for channels/opencode/_tool_return_enrichment.py.

PRD-DIST-2403 FR16-FR19 / audit P0-13 / P1-11.

Note: T2 tool-return payload construction is handled by the shared substrate
``channels/_tool_return_tiers.py::enrich_response()``, called directly from
``tools/before_edit_hint.py``, ``tools/entity_risk_map.py``, and
``tools/codebase_risk_report.py``.  The per-client ``build_t2_payload``
helper that previously lived here was dead code (never called from tools/)
and was removed.  The substrate ``enrich_response`` path covers FR16.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# FR16 — default tier is T2
# ---------------------------------------------------------------------------


def test_default_tier_is_t2() -> None:
    """FR16: get_default_tier_for_opencode returns T2."""
    from trw_mcp.channels.opencode._tool_return_enrichment import (
        get_default_tier_for_opencode,
    )

    assert get_default_tier_for_opencode() == "T2"


def test_substrate_enrich_response_is_wired_in_tools() -> None:
    """FR16: T2 payload construction uses the shared substrate enrich_response.

    Verifies that tools/before_edit_hint.py imports the substrate path, not
    a per-client builder.  This is the integration check that build_t2_payload
    was never wired in production.
    """
    import pathlib

    tool_file = (
        pathlib.Path(__file__).parent.parent.parent.parent
        / "src" / "trw_mcp" / "tools" / "before_edit_hint.py"
    )
    if not tool_file.exists():
        return  # substrate file not in this checkout; skip gracefully

    source = tool_file.read_text(encoding="utf-8")
    # The substrate path must be present
    assert "enrich_response" in source, "enrich_response substrate not found in before_edit_hint.py"
    # The dead per-client builder must NOT be present
    assert "build_t2_payload" not in source, "Dead build_t2_payload found in before_edit_hint.py"


# ---------------------------------------------------------------------------
# FR18 — transport field uses env vars (P0-13)
# ---------------------------------------------------------------------------


def test_transport_field_opencode_remote_http(monkeypatch: Any) -> None:
    """FR18: TRW_CLIENT_PROFILE=opencode + TRW_MCP_TRANSPORT=remote_http → remote_http."""

    monkeypatch.setenv("TRW_CLIENT_PROFILE", "opencode")
    monkeypatch.setenv("TRW_MCP_TRANSPORT", "remote_http")

    # Re-import to get fresh module state
    import trw_mcp.channels.opencode._tool_return_enrichment as mod

    # Reset the warned flag for this test
    mod._unknown_client_warned = False

    from trw_mcp.channels.opencode._tool_return_enrichment import resolve_transport

    assert resolve_transport() == "remote_http"


def test_transport_field_opencode_stdio(monkeypatch: Any) -> None:
    """FR18: TRW_CLIENT_PROFILE=opencode without TRW_MCP_TRANSPORT → stdio (default)."""
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "opencode")
    monkeypatch.delenv("TRW_MCP_TRANSPORT", raising=False)

    from trw_mcp.channels.opencode._tool_return_enrichment import resolve_transport

    assert resolve_transport() == "stdio"


def test_transport_unknown_when_no_client_profile(monkeypatch: Any) -> None:
    """FR18: Missing TRW_CLIENT_PROFILE → transport=unknown."""
    import trw_mcp.channels.opencode._tool_return_enrichment as mod

    monkeypatch.delenv("TRW_CLIENT_PROFILE", raising=False)
    mod._unknown_client_warned = False  # reset rate-limit flag

    from trw_mcp.channels.opencode._tool_return_enrichment import resolve_transport

    result = resolve_transport()
    assert result == "unknown"


def test_transport_unknown_emits_warning(monkeypatch: Any) -> None:
    """FR18: unknown client emits a structlog warning."""
    import trw_mcp.channels.opencode._tool_return_enrichment as mod

    monkeypatch.delenv("TRW_CLIENT_PROFILE", raising=False)
    mod._unknown_client_warned = False

    import structlog.testing

    from trw_mcp.channels.opencode._tool_return_enrichment import resolve_transport

    with structlog.testing.capture_logs() as cap:
        resolve_transport()

    warning_events = [e for e in cap if e.get("log_level") == "warning"]
    assert warning_events, "Expected a warning log event for unknown client"


# ---------------------------------------------------------------------------
# FR19 — client field distinguishes opencode
# ---------------------------------------------------------------------------


def test_is_opencode_client_true(monkeypatch: Any) -> None:
    """FR19: is_opencode_client() returns True when TRW_CLIENT_PROFILE=opencode."""
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "opencode")

    from trw_mcp.channels.opencode._tool_return_enrichment import is_opencode_client

    assert is_opencode_client() is True


def test_is_opencode_client_false_claude_code(monkeypatch: Any) -> None:
    """FR19: is_opencode_client() returns False for claude-code."""
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "claude-code")

    from trw_mcp.channels.opencode._tool_return_enrichment import is_opencode_client

    assert is_opencode_client() is False


def test_is_opencode_client_false_unknown(monkeypatch: Any) -> None:
    """FR19: is_opencode_client() returns False when env var absent."""
    import os as _os

    if "TRW_CLIENT_PROFILE" in _os.environ:
        monkeypatch.delenv("TRW_CLIENT_PROFILE")

    from trw_mcp.channels.opencode._tool_return_enrichment import is_opencode_client

    assert is_opencode_client() is False



@pytest.fixture(autouse=True)
def _structlog_defaults_for_capture() -> object:
    """File-scoped: reset structlog to defaults so ``capture_logs()`` sees WARN.

    A prior test's ``configure_logging()`` (server import / init_project) installs
    a filtering wrapper that drops WARN before ``capture_logs``'s processor, so
    these warning-assertion tests fail only in full-suite ordering. Save+restore
    (file-scoped, never a global reset — avoids the alphabetical-leak hazard).
    """
    import structlog

    _saved = structlog.get_config()
    structlog.reset_defaults()
    yield
    structlog.configure(**_saved)
