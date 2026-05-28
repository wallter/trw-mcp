"""Tests for channels/opencode/_tool_return_enrichment.py.

PRD-DIST-2403 FR16-FR19 / audit P0-13 / P1-11.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch


def _make_sidecar(file_path: str = "src/app.py") -> dict[str, Any]:
    return {
        "hotspots": [
            {
                "file": file_path,
                "risk_score": 0.85,
                "importers": ["src/main.py", "src/api.py"],
                "co_change_neighbors": ["src/utils.py"],
                "inferred_tests": ["tests/test_app.py"],
                "warnings": ["High churn module"],
            }
        ],
        "file_map": {
            file_path: {
                "risk_score": 0.85,
                "importers": ["src/main.py", "src/api.py"],
                "co_change_neighbors": ["src/utils.py"],
                "inferred_tests": ["tests/test_app.py"],
                "warnings": ["High churn module"],
            }
        },
    }


# ---------------------------------------------------------------------------
# FR16 — default tier is T2
# ---------------------------------------------------------------------------


def test_default_tier_is_t2() -> None:
    """FR16: get_default_tier_for_opencode returns T2."""
    from trw_mcp.channels.opencode._tool_return_enrichment import (
        get_default_tier_for_opencode,
    )

    assert get_default_tier_for_opencode() == "T2"


def test_t2_payload_excludes_t3_fields() -> None:
    """FR16: T2 payload does NOT include edge_cases or rationale_records."""
    from trw_mcp.channels.opencode._tool_return_enrichment import build_t2_payload

    sidecar = _make_sidecar()
    payload = build_t2_payload("src/app.py", sidecar)
    assert payload is not None
    assert "edge_cases" not in payload
    assert "rationale_records" not in payload


def test_t2_payload_includes_core_fields() -> None:
    """FR16: T2 payload includes importers, inferred_tests, risk_score, etc."""
    from trw_mcp.channels.opencode._tool_return_enrichment import build_t2_payload

    sidecar = _make_sidecar()
    payload = build_t2_payload("src/app.py", sidecar)
    assert payload is not None
    assert "importers" in payload
    assert "inferred_tests" in payload
    assert "risk_score" in payload
    assert "co_change_neighbors" in payload
    assert "hotspot_warnings" in payload
    assert payload["tier"] == "T2"


# ---------------------------------------------------------------------------
# FR18 — transport field uses env vars (P0-13)
# ---------------------------------------------------------------------------


def test_transport_field_opencode_remote_http(monkeypatch: Any) -> None:
    """FR18: TRW_CLIENT_PROFILE=opencode + TRW_MCP_TRANSPORT=remote_http → remote_http."""
    import importlib

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


# ---------------------------------------------------------------------------
# build_t2_payload — None sidecar / file not found
# ---------------------------------------------------------------------------


def test_build_t2_payload_none_sidecar() -> None:
    """build_t2_payload returns None when sidecar_data is None."""
    from trw_mcp.channels.opencode._tool_return_enrichment import build_t2_payload

    assert build_t2_payload("src/app.py", None) is None


def test_build_t2_payload_file_not_in_sidecar() -> None:
    """build_t2_payload returns None when file_path is not in sidecar."""
    from trw_mcp.channels.opencode._tool_return_enrichment import build_t2_payload

    sidecar: dict[str, Any] = {"hotspots": [], "file_map": {}}
    assert build_t2_payload("nonexistent.py", sidecar) is None
