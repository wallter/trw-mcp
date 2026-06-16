"""Tests for client_profile propagation via FastMCP ctx (Gap 1 closure).

Proves that resolve_client_profile reads client identity from:
1. Explicit string argument (highest priority)
2. FastMCP ctx.session.client_params.clientInfo.name (MCP initialize handshake)
3. TRW_CLIENT_PROFILE env var fallback
4. "unknown" final fallback

These are unit tests: no filesystem I/O.
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import pytest

from trw_mcp.tools._client_detection import (
    _ENV_VAR,
    _UNKNOWN_CLIENT,
    resolve_client_profile,
)


def _make_ctx(client_name: str | None) -> MagicMock:
    """Return a mock FastMCP Context whose session.client_params.clientInfo.name
    returns *client_name*.
    """
    ctx = MagicMock(spec=["session"])
    session = MagicMock()
    client_info = MagicMock()
    client_info.name = client_name

    client_params = MagicMock()
    client_params.clientInfo = client_info

    session.client_params = client_params
    ctx.session = session
    return ctx


def _make_ctx_no_session() -> MagicMock:
    """Return a ctx whose .session raises RuntimeError (pre-initialize)."""
    ctx = MagicMock(spec=["session"])
    type(ctx).session = PropertyMock(side_effect=RuntimeError("no session"))
    return ctx


def _make_ctx_null_client_params() -> MagicMock:
    """Return a ctx whose session.client_params is None."""
    ctx = MagicMock(spec=["session"])
    session = MagicMock()
    session.client_params = None
    ctx.session = session
    return ctx


# ---------------------------------------------------------------------------
# Priority 1: explicit string arg
# ---------------------------------------------------------------------------


class TestExplicitArgPriority:
    def test_explicit_arg_beats_ctx(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV_VAR, raising=False)
        ctx = _make_ctx("cursor-ide")
        assert resolve_client_profile("codex", ctx=ctx) == "codex"

    def test_explicit_arg_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV_VAR, "copilot")
        assert resolve_client_profile("claude-code") == "claude-code"

    def test_explicit_arg_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV_VAR, raising=False)
        assert resolve_client_profile("  codex  ") == "codex"


# ---------------------------------------------------------------------------
# Priority 2: FastMCP ctx
# ---------------------------------------------------------------------------


class TestCtxPropagation:
    def test_reads_clientinfo_name_from_ctx(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV_VAR, raising=False)
        ctx = _make_ctx("codex")
        result = resolve_client_profile(ctx=ctx)
        assert result == "codex"

    def test_ctx_lowercases_client_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV_VAR, raising=False)
        ctx = _make_ctx("Codex")
        result = resolve_client_profile(ctx=ctx)
        assert result == "codex"

    def test_ctx_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV_VAR, raising=False)
        ctx = _make_ctx("  opencode  ")
        result = resolve_client_profile(ctx=ctx)
        assert result == "opencode"

    def test_ctx_runtime_error_falls_back_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV_VAR, "aider")
        ctx = _make_ctx_no_session()
        result = resolve_client_profile(ctx=ctx)
        assert result == "aider"

    def test_ctx_none_client_params_falls_back_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV_VAR, "gemini")
        ctx = _make_ctx_null_client_params()
        result = resolve_client_profile(ctx=ctx)
        assert result == "gemini"

    def test_ctx_blank_name_falls_back_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV_VAR, "cursor-cli")
        ctx = _make_ctx("")
        result = resolve_client_profile(ctx=ctx)
        assert result == "cursor-cli"

    def test_ctx_none_name_falls_back_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV_VAR, "antigravity")
        ctx = _make_ctx(None)
        result = resolve_client_profile(ctx=ctx)
        assert result == "antigravity"

    def test_ctx_beats_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV_VAR, "copilot")
        ctx = _make_ctx("claude-code")
        result = resolve_client_profile(ctx=ctx)
        assert result == "claude-code"


# ---------------------------------------------------------------------------
# Priority 3: env var
# ---------------------------------------------------------------------------


class TestEnvVarFallback:
    def test_env_var_used_when_no_ctx_no_arg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV_VAR, "cursor-ide")
        result = resolve_client_profile()
        assert result == "cursor-ide"

    def test_env_blank_falls_back_to_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_ENV_VAR, "   ")
        result = resolve_client_profile()
        assert result == _UNKNOWN_CLIENT


# ---------------------------------------------------------------------------
# Priority 4: unknown fallback
# ---------------------------------------------------------------------------


class TestUnknownFallback:
    def test_all_absent_returns_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV_VAR, raising=False)
        result = resolve_client_profile()
        assert result == _UNKNOWN_CLIENT

    def test_ctx_none_env_absent_returns_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(_ENV_VAR, raising=False)
        result = resolve_client_profile(ctx=None)
        assert result == _UNKNOWN_CLIENT


# ---------------------------------------------------------------------------
# Tier resolution integration (ctx -> tier)
# ---------------------------------------------------------------------------


class TestCtxToTierIntegration:
    """Prove the ctx -> client_name -> tier pipeline works end-to-end."""

    def test_ctx_codex_resolves_t2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.tools._client_detection import resolve_tier_for_client

        monkeypatch.delenv(_ENV_VAR, raising=False)
        ctx = _make_ctx("codex")
        client = resolve_client_profile(ctx=ctx)
        assert resolve_tier_for_client(client) == "T2"

    def test_ctx_copilot_resolves_t0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.tools._client_detection import resolve_tier_for_client

        monkeypatch.delenv(_ENV_VAR, raising=False)
        ctx = _make_ctx("copilot")
        client = resolve_client_profile(ctx=ctx)
        assert resolve_tier_for_client(client) == "T0"

    def test_ctx_claude_code_resolves_t1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.tools._client_detection import resolve_tier_for_client

        monkeypatch.delenv(_ENV_VAR, raising=False)
        ctx = _make_ctx("claude-code")
        client = resolve_client_profile(ctx=ctx)
        assert resolve_tier_for_client(client) == "T1"

    def test_ctx_runtime_error_env_fallback_resolves_tier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.tools._client_detection import resolve_tier_for_client

        monkeypatch.setenv(_ENV_VAR, "opencode")
        ctx = _make_ctx_no_session()
        client = resolve_client_profile(ctx=ctx)
        assert resolve_tier_for_client(client) == "T2"
