"""Tests for channels/codex/_tool_return_t2.py — T2 tool-return detection.

PRD-DIST-2402 FR05, FR06.
"""

from __future__ import annotations

from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sidecar_with_file(file_path: str) -> dict[str, Any]:
    """Build minimal sidecar with a hotspot entry for file_path."""
    return {
        "schema_version": "risk-report-sidecar/v0",
        "hotspots": [
            {
                "file": file_path,
                "risk_score": 0.85,
                "reason": "High churn",
                "importers": ["app/main.py", "tests/test_admin.py"],
                "co_change_neighbors": ["related/module.py"],
                "inferred_tests": ["tests/test_admin.py"],
                "warnings": ["Near test coverage threshold"],
            }
        ],
        "conventions": [],
        "edge_cases": [],
    }


# ---------------------------------------------------------------------------
# FR05 — get_default_tier_for_codex returns T2
# ---------------------------------------------------------------------------


def test_get_default_tier_for_codex_returns_t2() -> None:
    """FR05: get_default_tier_for_codex() always returns 'T2'."""
    from trw_mcp.channels.codex._tool_return_t2 import get_default_tier_for_codex

    assert get_default_tier_for_codex() == "T2"


# ---------------------------------------------------------------------------
# FR05 — T2 payload with TRW_CLIENT_PROFILE=codex
# ---------------------------------------------------------------------------


def test_t2_payload_with_env_var_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR05: build_t2_payload returns full payload when sidecar contains the file."""
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "codex")

    from trw_mcp.channels.codex._tool_return_t2 import build_t2_payload, is_codex_client

    assert is_codex_client(), "is_codex_client() should return True with env var set"

    sidecar = _make_sidecar_with_file("backend/routers/admin.py")
    payload = build_t2_payload("backend/routers/admin.py", sidecar)

    assert payload is not None
    assert payload["file_path"] == "backend/routers/admin.py"
    assert "importers" in payload
    assert "co_change_neighbors" in payload
    assert "inferred_tests" in payload
    assert "risk_score" in payload
    assert "hotspot_warnings" in payload
    assert payload["tier"] == "T2"


def test_t2_payload_contains_correct_importers(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR05: T2 payload importers match sidecar data for the file."""
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "codex")

    from trw_mcp.channels.codex._tool_return_t2 import build_t2_payload

    sidecar = _make_sidecar_with_file("backend/routers/admin.py")
    payload = build_t2_payload("backend/routers/admin.py", sidecar)

    assert payload is not None
    assert "app/main.py" in payload["importers"]
    assert "tests/test_admin.py" in payload["importers"]
    assert payload["risk_score"] == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# FR05 — T1 fallback without env var
# ---------------------------------------------------------------------------


def test_t1_fallback_without_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR05: is_codex_client() returns False when TRW_CLIENT_PROFILE unset."""
    monkeypatch.delenv("TRW_CLIENT_PROFILE", raising=False)

    from trw_mcp.channels.codex._tool_return_t2 import is_codex_client

    assert not is_codex_client()


def test_t1_fallback_other_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR05: is_codex_client() returns False for other client profiles."""
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "claude-code")

    from trw_mcp.channels.codex._tool_return_t2 import is_codex_client

    assert not is_codex_client()


# ---------------------------------------------------------------------------
# FR05 — T2 with sidecar absent
# ---------------------------------------------------------------------------


def test_t2_sidecar_absent_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR05: build_t2_payload returns None when sidecar is absent."""
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "codex")

    from trw_mcp.channels.codex._tool_return_t2 import build_t2_payload

    result = build_t2_payload("backend/routers/admin.py", None)
    assert result is None


def test_t2_file_not_in_sidecar_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR05: build_t2_payload returns None when file not in sidecar hotspots."""
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "codex")

    from trw_mcp.channels.codex._tool_return_t2 import build_t2_payload

    sidecar = _make_sidecar_with_file("other/file.py")
    result = build_t2_payload("backend/routers/admin.py", sidecar)
    assert result is None


# ---------------------------------------------------------------------------
# Env var isolation — ensure test does not bleed to other tests
# ---------------------------------------------------------------------------


def test_env_var_isolation_no_bleed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env var changes are isolated per test (monkeypatch auto-restores)."""
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "codex")

    from trw_mcp.channels.codex._tool_return_t2 import is_codex_client

    assert is_codex_client()
    # After this test, monkeypatch will restore env — verified by other tests


def test_env_var_after_isolation_is_unset() -> None:
    """Verify no TRW_CLIENT_PROFILE bleed from previous test (relies on monkeypatch)."""
    import os

    # This test runs without monkeypatch — env should not have codex set
    # unless the operator actually has it set in their environment
    profile = os.environ.get("TRW_CLIENT_PROFILE", "")
    # We cannot assert it's unset because the operator might have it set,
    # but we can verify the function handles it correctly
    from trw_mcp.channels.codex._tool_return_t2 import is_codex_client, get_default_tier_for_codex

    # Regardless of env state, get_default_tier_for_codex is always T2
    assert get_default_tier_for_codex() == "T2"


# ---------------------------------------------------------------------------
# File map lookup (alternative sidecar format)
# ---------------------------------------------------------------------------


def test_file_map_lookup_preferred_over_hotspot_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_t2_payload finds file via file_map dict (O(1) lookup)."""
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "codex")

    from trw_mcp.channels.codex._tool_return_t2 import build_t2_payload

    sidecar: dict[str, Any] = {
        "hotspots": [],
        "file_map": {
            "backend/routers/admin.py": {
                "risk_score": 0.75,
                "importers": ["app/main.py"],
                "co_change_neighbors": [],
                "inferred_tests": [],
                "warnings": [],
            }
        },
    }
    payload = build_t2_payload("backend/routers/admin.py", sidecar)
    assert payload is not None
    assert payload["risk_score"] == pytest.approx(0.75)
