"""Tests for LLM client abstraction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestLLMClientAvailability:
    """Tests for LLMClient availability detection."""

    def test_unavailable_when_no_sdk(self) -> None:
        with patch.dict("sys.modules", {"claude_agent_sdk": None}):
            import importlib

            import trw_mcp.clients.llm as llm_mod

            importlib.reload(llm_mod)

            client = llm_mod.LLMClient()
            assert client.available is False

    def test_ask_sync_returns_none_when_unavailable(self) -> None:
        """ask_sync always returns None when SDK is not installed."""
        with patch.dict("sys.modules", {"claude_agent_sdk": None}):
            import importlib

            import trw_mcp.clients.llm as llm_mod

            importlib.reload(llm_mod)

            client = llm_mod.LLMClient()
            assert client.available is False
            assert client.ask_sync("test prompt") is None
            assert client.ask_sync("test prompt", system="sys") is None

    @pytest.mark.asyncio
    async def test_ask_returns_none_when_unavailable(self) -> None:
        """ask() always returns None when SDK is not installed."""
        with patch.dict("sys.modules", {"claude_agent_sdk": None}):
            import importlib

            import trw_mcp.clients.llm as llm_mod

            importlib.reload(llm_mod)

            client = llm_mod.LLMClient()
            assert client.available is False
            result = await client.ask("test prompt")
            assert result is None

    def test_default_model_is_haiku(self) -> None:
        from trw_mcp.clients.llm import LLMClient

        client = LLMClient()
        assert client._model == "haiku"

    def test_custom_model(self) -> None:
        from trw_mcp.clients.llm import LLMClient

        client = LLMClient(model="sonnet")
        assert client._model == "sonnet"

    def test_custom_max_turns_and_system_prompt(self) -> None:
        from trw_mcp.clients.llm import LLMClient

        client = LLMClient(max_turns=3, system_prompt="You are helpful.")
        assert client._max_turns == 3
        assert client._system_prompt == "You are helpful."

    def test_available_when_sdk_present(self) -> None:
        """Test that client reports available when SDK can be imported."""
        mock_sdk = MagicMock()
        mock_sdk.query = MagicMock()

        with patch.dict("sys.modules", {"claude_agent_sdk": mock_sdk}):
            import importlib

            import trw_mcp.clients.llm as llm_mod

            importlib.reload(llm_mod)

            client = llm_mod.LLMClient()
            assert client.available is True
            assert client._query_fn is not None

    def test_query_fn_is_none_when_unavailable(self) -> None:
        """_query_fn stays None when SDK is not installed."""
        with patch.dict("sys.modules", {"claude_agent_sdk": None}):
            import importlib

            import trw_mcp.clients.llm as llm_mod

            importlib.reload(llm_mod)

            client = llm_mod.LLMClient()
            assert client._query_fn is None
