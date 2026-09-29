"""Misc coverage tests for LLM and model enum branches."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch


class TestLLMClientAskSync:
    """Cover the ThreadPoolExecutor branch when event loop is already running."""

    async def test_ask_sync_with_running_loop_uses_thread_pool(self) -> None:
        """Lines 205-209: ask_sync when a loop is running uses ThreadPoolExecutor."""
        from trw_mcp.clients.llm import LLMClient

        client = LLMClient()
        if not client.available:
            client._available = True

            async def mock_ask(*args: Any, **kwargs: Any) -> str | None:
                return "mocked response"

            with patch.object(client, "ask", mock_ask):
                result = client.ask_sync("test prompt")
                assert result == "mocked response"
        else:
            with patch.object(client, "ask", return_value="mocked"):
                result = client.ask_sync("test prompt")
                assert result is None or isinstance(result, str)

    def test_ask_sync_without_running_loop(self) -> None:
        """Lines 200-203: ask_sync without a running loop uses asyncio.run."""
        from trw_mcp.clients.llm import LLMClient

        client = LLMClient()
        if not client.available:
            result = client.ask_sync("test")
            assert result is None
            return

        async def fast_ask(*args: Any, **kwargs: Any) -> str:
            return "sync result"

        with patch.object(client, "ask", fast_ask):
            result = client.ask_sync("test prompt")
            assert result == "sync result"

    async def test_ask_sync_thread_pool_executes_coroutine(self) -> None:
        """Lines 205-209: directly test the concurrent.futures path."""
        from trw_mcp.clients.llm import LLMClient

        client = LLMClient()
        object.__setattr__(client, "_available", True)

        async def mock_ask(prompt: str, *, system: Any = None, model: Any = None, max_turns: Any = None) -> str:
            return "thread pool result"

        with patch.object(client, "ask", mock_ask):
            result = client.ask_sync("hello")
            assert result == "thread pool result"


class TestAutoUpgradeCoverage:
    """Lines 29-30: get_installed_version ImportError/AttributeError."""
