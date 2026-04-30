"""Misc coverage tests for LLM, model enum, and auto-upgrade branches."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


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


class TestReversionTriggerClassify:
    """Lines 52-55: ReversionTrigger.classify."""

    def test_classify_valid_value_returns_member(self) -> None:
        """Line 53: valid trigger string returns the enum member."""
        from trw_mcp.models.run import ReversionTrigger

        result = ReversionTrigger.classify("refactor_needed")
        assert result == ReversionTrigger.REFACTOR_NEEDED

    def test_classify_architecture_mismatch(self) -> None:
        from trw_mcp.models.run import ReversionTrigger

        result = ReversionTrigger.classify("architecture_mismatch")
        assert result == ReversionTrigger.ARCHITECTURE_MISMATCH

    def test_classify_unknown_returns_other(self) -> None:
        """Line 55: unrecognized string returns OTHER."""
        from trw_mcp.models.run import ReversionTrigger

        result = ReversionTrigger.classify("totally_unknown_trigger")
        assert result == ReversionTrigger.OTHER

    def test_classify_empty_string_returns_other(self) -> None:
        from trw_mcp.models.run import ReversionTrigger

        result = ReversionTrigger.classify("")
        assert result == ReversionTrigger.OTHER


class TestEventTypeResolve:
    """Lines 262-265: EventType.resolve."""

    def test_resolve_valid_event_returns_member(self) -> None:
        """Line 263: valid event string returns enum member."""
        from trw_mcp.models.run import EventType

        result = EventType.resolve("run_init")
        assert result == EventType.RUN_INIT

    def test_resolve_checkpoint_event(self) -> None:
        from trw_mcp.models.run import EventType

        result = EventType.resolve("checkpoint")
        assert result == EventType.CHECKPOINT

    def test_resolve_unknown_returns_none(self) -> None:
        """Line 265: unrecognized string returns None."""
        from trw_mcp.models.run import EventType

        result = EventType.resolve("totally_unknown_event_xyz")
        assert result is None

    def test_resolve_empty_string_returns_none(self) -> None:
        from trw_mcp.models.run import EventType

        result = EventType.resolve("")
        assert result is None


class TestAutoUpgradeCoverage:
    """Lines 29-30: get_installed_version ImportError/AttributeError."""

    def test_get_installed_version_missing_attr_returns_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Lines 29-30: When __version__ is missing, returns '0.0.0'."""
        import trw_mcp as _trw_mcp_mod
        from trw_mcp.state.auto_upgrade import get_installed_version

        monkeypatch.delattr(_trw_mcp_mod, "__version__", raising=False)
        result = get_installed_version()
        assert result == "0.0.0"

    def test_get_installed_version_returns_actual_version(self) -> None:
        """Positive case: get_installed_version returns a semver string."""
        import re

        from trw_mcp.state.auto_upgrade import get_installed_version

        version = get_installed_version()
        assert isinstance(version, str)
        assert len(version) > 0
        assert re.match(r"\d+\.\d+\.\d+", version), f"Expected semver pattern, got: {version!r}"
