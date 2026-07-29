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

    def test_ask_sync_supports_local_ollama_model(self) -> None:
        """Sync callers honor the same local-model availability as ask()."""
        client = _make_unavailable_client()
        client._model = "ollama/qwen2.5-coder"
        client.ask = AsyncMock(return_value="local response")  # type: ignore[method-assign]

        assert client.ask_sync("test prompt") == "local response"

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
        # Bumped 4-6 -> 5 (2026-07-26).
        assert _resolve_model("sonnet") == "claude-sonnet-5"

    def test_opus_alias(self) -> None:
        # PRD-QUAL-072 FR01 bumped 4-6 -> 4-7 (2026-04-23); bumped again to
        # the Claude 5 generation (2026-07-26).
        assert _resolve_model("opus") == "claude-opus-5"

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
        assert call_kwargs["model"] == "claude-opus-5"

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
# Capability-alias currency + backward compat (PRD-QUAL-072 FR01/FR09 lineage)
# ---------------------------------------------------------------------------


class TestCapabilityAliasCurrency:
    """The three capability aliases resolve to the current model generation.

    Superseded ``TestOpus47Migration``: the aliases have been bumped twice
    since (4.6 -> 4.7 in 2026-04, 4.7 -> Claude 5 in 2026-07). The FR09
    passthrough guarantee is unchanged and still covered below.
    """

    def test_frontier_aliases_resolve_to_opus_5(self) -> None:
        from trw_mcp.clients.llm import _MODEL_MAP

        assert _MODEL_MAP["opus"] == "claude-opus-5"
        assert _MODEL_MAP["frontier"] == "claude-opus-5"

    def test_balanced_aliases_resolve_to_sonnet_5(self) -> None:
        from trw_mcp.clients.llm import _MODEL_MAP

        assert _MODEL_MAP["sonnet"] == "claude-sonnet-5"
        assert _MODEL_MAP["balanced"] == "claude-sonnet-5"

    def test_fast_aliases_unchanged(self) -> None:
        """Haiku is deliberately NOT bumped — 4.5 is the current Haiku."""
        from trw_mcp.clients.llm import _MODEL_MAP

        assert _MODEL_MAP["haiku"] == "claude-haiku-4-5-20251001"
        assert _MODEL_MAP["fast"] == "claude-haiku-4-5-20251001"

    def test_superseded_explicit_ids_still_resolve(self) -> None:
        """FR09: pinning an older generation explicitly keeps working."""
        assert _resolve_model("claude-opus-4-6") == "claude-opus-4-6"
        assert _resolve_model("claude-opus-4-7") == "claude-opus-4-7"
        assert _resolve_model("claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_unknown_model_id_passes_through(self) -> None:
        """FR09: arbitrary model strings pass through untouched."""
        assert _resolve_model("some-future-model-6-0") == "some-future-model-6-0"


# ---------------------------------------------------------------------------
# Request shape: output budget + effort gating
# ---------------------------------------------------------------------------


class TestRequestShape:
    """The request must stay valid across models with different capabilities."""

    @pytest.mark.asyncio
    async def test_output_budget_leaves_room_for_thinking(self) -> None:
        """A thinking-by-default model must not spend the whole budget reasoning.

        ``max_tokens`` caps thinking *and* response text together. The previous
        1024 ceiling predates adaptive-thinking-by-default and could truncate a
        short answer into nothing.
        """
        mock_async_client, client = _make_wired_client("Response")

        await client.ask("test", model="opus")

        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert call_kwargs["max_tokens"] >= 4096

    @pytest.mark.asyncio
    async def test_effort_requested_on_declaring_model(self) -> None:
        """Cost posture: internal augmentation asks for low effort, not the API default of high."""
        mock_async_client, client = _make_wired_client("Response")

        await client.ask("test", model="opus")

        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert call_kwargs["output_config"] == {"effort": "low"}

    @pytest.mark.asyncio
    async def test_effort_omitted_on_model_that_rejects_it(self) -> None:
        """Haiku 4.5 — this client's own default — errors on ``effort``.

        Sending it anyway would break every default internal call, so the
        parameter must be absent rather than merely ignored.
        """
        mock_async_client, client = _make_wired_client("Response")

        await client.ask("test")  # default model is haiku

        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert "output_config" not in call_kwargs

    @pytest.mark.asyncio
    async def test_rejected_model_id_is_logged_distinctly(self) -> None:
        """A 404 must not read as "the SDK isn't installed".

        Both return ``None`` to the caller, so the log line is the only thing
        that distinguishes a stale ``_MODEL_MAP`` entry — the failure most
        likely to be introduced by an edit — from an unconfigured environment.
        """
        import structlog.testing

        class NotFoundError(Exception):
            pass

        ac = MagicMock()
        ac.messages.create = AsyncMock(side_effect=NotFoundError("model not found"))
        client = _make_client(ac)

        with structlog.testing.capture_logs() as logs:
            assert await client.ask("test", model="claude-does-not-exist") is None

        events = [entry.get("event") for entry in logs]
        assert "llm_model_unknown" in events
        assert "llm_call_failed" not in events

    @pytest.mark.asyncio
    async def test_other_api_errors_keep_the_generic_warning(self) -> None:
        """Only 404s are reclassified; everything else stays on the old path."""
        import structlog.testing

        ac = MagicMock()
        ac.messages.create = AsyncMock(side_effect=RuntimeError("connection reset"))
        client = _make_client(ac)

        with structlog.testing.capture_logs() as logs:
            assert await client.ask("test") is None

        events = [entry.get("event") for entry in logs]
        assert "llm_call_failed" in events
        assert "llm_model_unknown" not in events

    @pytest.mark.asyncio
    async def test_effort_omitted_on_unknown_model(self) -> None:
        """An unrecognised or future model gets the conservative request shape."""
        mock_async_client, client = _make_wired_client("Response")

        await client.ask("test", model="some-future-model-6-0")

        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert "output_config" not in call_kwargs


# ---------------------------------------------------------------------------
# Ollama integration tests
# ---------------------------------------------------------------------------


class TestOllamaLLMClient:
    """Tests for LLMClient's local Ollama model routing."""

    @pytest.mark.asyncio
    async def test_ask_routes_to_ollama_when_prefix_given(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ask() routes requests starting with 'ollama/' to Ollama API."""
        import httpx

        class MockResponse:
            status_code = 200

            def json(self):
                return {"response": "Hello from local Ollama"}

        call_args = []

        async def mock_post(self_client, url, json, **kwargs):
            call_args.append((url, json))
            return MockResponse()

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        client = LLMClient(model="ollama/qwen2.5-coder")
        assert client.available is True

        result = await client.ask("Who are you?")
        assert result == "Hello from local Ollama"
        assert len(call_args) == 1
        assert "api/generate" in call_args[0][0]
        assert call_args[0][1]["model"] == "qwen2.5-coder"
        assert call_args[0][1]["prompt"] == "Who are you?"

    @pytest.mark.asyncio
    async def test_ask_routes_to_ollama_via_env_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ask() falls back to Ollama when OLLAMA_HOST is set and Anthropic is unavailable."""
        import httpx

        class MockResponse:
            status_code = 200

            def json(self):
                return {"response": "Fallback Ollama response"}

        async def mock_post(self_client, url, json, **kwargs):
            return MockResponse()

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
        monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")

        # Create client where Anthropic is unavailable
        client = _make_unavailable_client()
        assert client.available is True  # Available because OLLAMA_HOST is set

        result = await client.ask("Test prompt", model="some-model")
        assert result == "Fallback Ollama response"
