"""Tests for trw_mcp.tools._client_detection (PRD-DIST-2400 Phase D2)."""

from __future__ import annotations

import pytest

from trw_mcp.tools._client_detection import (
    _CLIENT_DEFAULT_TIER,
    _ENV_VAR,
    _UNKNOWN_CLIENT,
    resolve_client_profile,
    resolve_tier_for_client,
)

# ---------------------------------------------------------------------------
# resolve_client_profile
# ---------------------------------------------------------------------------


class TestResolveClientProfile:
    def test_returns_provided_arg(self) -> None:
        result = resolve_client_profile("claude-code")
        assert result == "claude-code"

    def test_strips_whitespace_from_arg(self) -> None:
        result = resolve_client_profile("  codex  ")
        assert result == "codex"

    def test_env_var_used_when_arg_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV_VAR, "cursor-ide")
        result = resolve_client_profile(None)
        assert result == "cursor-ide"

    def test_env_var_used_when_arg_is_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV_VAR, "opencode")
        result = resolve_client_profile("")
        assert result == "opencode"

    def test_fallback_to_unknown_when_no_arg_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV_VAR, raising=False)
        result = resolve_client_profile(None)
        assert result == _UNKNOWN_CLIENT

    def test_fallback_to_unknown_when_env_is_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV_VAR, "   ")
        result = resolve_client_profile(None)
        assert result == _UNKNOWN_CLIENT

    def test_arg_takes_precedence_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV_VAR, "cursor-ide")
        result = resolve_client_profile("copilot")
        assert result == "copilot"

    def test_default_arg_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV_VAR, "aider")
        # Call with no arguments — default is None → reads from env
        result = resolve_client_profile()
        assert result == "aider"


# ---------------------------------------------------------------------------
# resolve_tier_for_client
# ---------------------------------------------------------------------------


class TestResolveTierForClient:
    @pytest.mark.parametrize(
        "client,expected_tier",
        [
            ("codex", "T2"),
            ("opencode", "T2"),
            ("cursor-ide", "T2"),
            ("cursor-cli", "T2"),
            ("claude-code", "T1"),
            ("antigravity", "T1"),
            ("antigravity-cli", "T1"),
            ("gemini", "T1"),
            ("aider", "T1"),
            ("copilot", "T0"),
        ],
    )
    def test_known_clients(self, client: str, expected_tier: str) -> None:
        result = resolve_tier_for_client(client)
        assert result == expected_tier, f"Expected {expected_tier!r} for {client!r}"

    def test_unknown_client_returns_default_tier(self) -> None:
        result = resolve_tier_for_client("some-unknown-client")
        assert result == "T1"

    def test_unknown_client_uses_explicit_default(self) -> None:
        result = resolve_tier_for_client("unknown-client", default_tier="T3")
        assert result == "T3"

    def test_unknown_client_returns_t1_default(self) -> None:
        result = resolve_tier_for_client("", default_tier="T1")
        assert result == "T1"

    def test_mapping_is_accessible(self) -> None:
        # Verify the module-level dict is exposed correctly.
        assert "copilot" in _CLIENT_DEFAULT_TIER
        assert _CLIENT_DEFAULT_TIER["copilot"] == "T0"
        assert "codex" in _CLIENT_DEFAULT_TIER
        assert _CLIENT_DEFAULT_TIER["codex"] == "T2"
