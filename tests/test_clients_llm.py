"""Tests for LLM client abstraction (Anthropic SDK)."""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trw_mcp.clients.llm import LLMClient, _resolve_model

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(
    mock_async_client: MagicMock,
    *,
    model: str = "haiku",
    system_prompt: str = "",
) -> LLMClient:
    """Build a pre-wired LLMClient without calling __init__."""
    client = LLMClient.__new__(LLMClient)
    client._model = model
    client._max_turns = 1
    client._system_prompt = system_prompt
    client._available = True
    client._client = MagicMock()
    client._async_client = mock_async_client
    return client


def _make_response(*texts: str) -> MagicMock:
    """Build a mock Anthropic response with text content blocks."""
    response = MagicMock()
    response.content = [MagicMock(text=t) for t in texts]
    return response


def _make_wired_client(
    *texts: str,
    system_prompt: str = "",
    model: str = "haiku",
) -> tuple[MagicMock, LLMClient]:
    """Build a mock async client and pre-wired LLMClient returning the given texts."""
    ac = MagicMock()
    ac.messages.create = AsyncMock(return_value=_make_response(*texts))
    return ac, _make_client(ac, system_prompt=system_prompt, model=model)


def _make_unavailable_client() -> LLMClient:
    """Reload llm module with anthropic absent and return an LLMClient."""
    with patch.dict("sys.modules", {"anthropic": None}):
        import trw_mcp.clients.llm as llm_mod

        importlib.reload(llm_mod)
        return llm_mod.LLMClient()


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


class TestLLMClientAvailability:
    """Tests for LLMClient availability detection."""

    def test_unavailable_when_no_sdk(self) -> None:
        assert _make_unavailable_client().available is False

    def test_ask_sync_returns_none_when_unavailable(self) -> None:
        """ask_sync always returns None when SDK is not installed."""
        client = _make_unavailable_client()
        assert client.available is False
        assert client.ask_sync("test prompt") is None
        assert client.ask_sync("test prompt", system="sys") is None

    @pytest.mark.asyncio
    async def test_ask_returns_none_when_unavailable(self) -> None:
        """ask() always returns None when SDK is not installed."""
        client = _make_unavailable_client()
        assert client.available is False
        assert await client.ask("test prompt") is None

    def test_default_model_is_haiku(self) -> None:
        client = LLMClient()
        assert client._model == "haiku"

    def test_custom_model(self) -> None:
        client = LLMClient(model="sonnet")
        assert client._model == "sonnet"

    def test_custom_max_turns_and_system_prompt(self) -> None:
        client = LLMClient(max_turns=3, system_prompt="You are helpful.")
        assert client._max_turns == 3
        assert client._system_prompt == "You are helpful."

    def test_available_when_sdk_present(self) -> None:
        """Test that client reports available when SDK can be imported."""
        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = MagicMock()
        mock_anthropic.AsyncAnthropic.return_value = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            import trw_mcp.clients.llm as llm_mod

            importlib.reload(llm_mod)

            client = llm_mod.LLMClient()
            assert client.available is True
            assert client._client is not None
            assert client._async_client is not None


# ---------------------------------------------------------------------------
# Model alias resolution
# ---------------------------------------------------------------------------


class TestModelAliasResolution:
    """Tests for model alias -> full model ID resolution."""

    def test_haiku_alias(self) -> None:
        assert _resolve_model("haiku") == "claude-haiku-4-5-20251001"

    def test_sonnet_alias(self) -> None:
        assert _resolve_model("sonnet") == "claude-sonnet-4-6"

    def test_opus_alias(self) -> None:
        assert _resolve_model("opus") == "claude-opus-4-6"

    def test_custom_model_passthrough(self) -> None:
        assert _resolve_model("claude-custom-123") == "claude-custom-123"


# ---------------------------------------------------------------------------
# ask() behaviour
# ---------------------------------------------------------------------------


class TestAsk:
    """Tests for LLMClient.ask() with mocked Anthropic SDK."""

    @pytest.mark.asyncio
    async def test_ask_returns_text(self) -> None:
        """ask() extracts text from Anthropic response."""
        mock_async_client, client = _make_wired_client("Hello from Claude")

        result = await client.ask("Say hello")
        assert result == "Hello from Claude"

        mock_async_client.messages.create.assert_called_once()
        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
        assert call_kwargs["messages"] == [{"role": "user", "content": "Say hello"}]

    @pytest.mark.asyncio
    async def test_ask_with_system_prompt(self) -> None:
        """ask() passes system prompt to API."""
        mock_async_client, client = _make_wired_client("Response")

        await client.ask("test", system="Be concise")

        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert call_kwargs["system"] == "Be concise"

    @pytest.mark.asyncio
    async def test_ask_with_default_system_prompt(self) -> None:
        """ask() uses default system_prompt when no override given."""
        mock_async_client, client = _make_wired_client("Response", system_prompt="Default system")

        await client.ask("test")

        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert call_kwargs["system"] == "Default system"

    @pytest.mark.asyncio
    async def test_ask_no_system_prompt(self) -> None:
        """ask() omits system key when no system prompt configured."""
        mock_async_client, client = _make_wired_client("Response")

        await client.ask("test")

        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert "system" not in call_kwargs

    @pytest.mark.asyncio
    async def test_ask_with_model_override(self) -> None:
        """ask() resolves model override alias."""
        mock_async_client, client = _make_wired_client("Response")

        await client.ask("test", model="opus")

        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_ask_returns_none_on_empty_content(self) -> None:
        """ask() returns None when response has no content blocks."""
        ac = MagicMock()
        mock_response = MagicMock()
        mock_response.content = []
        ac.messages.create = AsyncMock(return_value=mock_response)
        assert await _make_client(ac).ask("test") is None

    @pytest.mark.asyncio
    async def test_ask_returns_none_on_api_failure(self) -> None:
        """ask() returns None when the API call raises an exception."""
        ac = MagicMock()
        ac.messages.create = AsyncMock(side_effect=RuntimeError("API error"))
        assert await _make_client(ac).ask("test") is None

    @pytest.mark.asyncio
    async def test_ask_returns_none_when_block_has_no_text(self) -> None:
        """ask() returns None when content block has no text attribute."""
        ac = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(spec=[])]  # No attributes at all
        ac.messages.create = AsyncMock(return_value=mock_response)
        assert await _make_client(ac).ask("test") is None


# ---------------------------------------------------------------------------
# PRD-QUAL-072: Opus 4.7 migration — alias bump + backward compat (FR01, FR09)
# ---------------------------------------------------------------------------


class TestOpus47Migration:
    """PRD-QUAL-072 FR01 + FR09 — opus alias resolves to 4.7; 4.6 still works."""

    def test_opus_alias_resolves_to_47(self) -> None:
        """FR01: short alias ``opus`` resolves to claude-opus-4-7."""
        from trw_mcp.clients.llm import _MODEL_MAP

        assert _MODEL_MAP["opus"] == "claude-opus-4-7"
        assert _resolve_model("opus") == "claude-opus-4-7"

    def test_sonnet_and_haiku_aliases_unchanged(self) -> None:
        """FR01: sonnet/haiku aliases are NOT bumped by this PRD."""
        from trw_mcp.clients.llm import _MODEL_MAP

        assert _MODEL_MAP["sonnet"] == "claude-sonnet-4-6"
        assert _MODEL_MAP["haiku"] == "claude-haiku-4-5-20251001"

    def test_opus_46_explicit_id_still_resolves(self) -> None:
        """FR09: explicit ``claude-opus-4-6`` passes through unchanged."""
        assert _resolve_model("claude-opus-4-6") == "claude-opus-4-6"

    def test_unknown_model_id_passes_through(self) -> None:
        """FR09: arbitrary model strings pass through untouched."""
        assert _resolve_model("claude-opus-4-7") == "claude-opus-4-7"
        assert _resolve_model("some-future-model-5-0") == "some-future-model-5-0"
