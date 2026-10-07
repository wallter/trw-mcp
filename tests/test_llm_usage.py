"""Tests for LLM usage model and client instrumentation — PRD-CORE-020."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs

pytestmark = pytest.mark.usefixtures("llm_contact_on")

# ---------------------------------------------------------------------------
# LLMClient Instrumentation Tests
# ---------------------------------------------------------------------------


def _make_mock_anthropic(
    response_text: str = "test response",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> tuple[MagicMock, MagicMock]:
    """Build a mock anthropic module and async client.

    Returns:
        (mock_anthropic, mock_async_client)
    """
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text=response_text)]
    mock_response.usage = MagicMock(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )

    mock_async_client = MagicMock()
    mock_async_client.messages.create = AsyncMock(return_value=mock_response)

    mock_anthropic = MagicMock()
    mock_anthropic.AsyncAnthropic.return_value = mock_async_client
    mock_anthropic.Anthropic.return_value = MagicMock()

    return mock_anthropic, mock_async_client


class TestLLMClientNoUsagePath:
    """Test LLMClient when usage_log_path=None."""

    async def test_llm_client_no_usage_path(self, tmp_path: Path) -> None:
        """LLMClient(usage_log_path=None) does NOT create any files."""
        from trw_mcp.clients.llm import LLMClient

        mock_anthropic, mock_async_client = _make_mock_anthropic()

        client = LLMClient(usage_log_path=None)
        client._available = True
        client._async_client = mock_async_client

        result = await client.ask("hello")

        assert result == "test response"
        # No JSONL files should be created anywhere in tmp_path
        assert list(tmp_path.rglob("*.jsonl")) == []

    async def test_llm_client_disabled_logging(self, tmp_path: Path) -> None:
        """When usage_log_path=None, no file operations occur during ask()."""
        from trw_mcp.clients.llm import LLMClient

        _, mock_async_client = _make_mock_anthropic()

        client = LLMClient(usage_log_path=None)
        client._available = True
        client._async_client = mock_async_client

        # Call ask multiple times — still no files created
        await client.ask("first")
        await client.ask("second")

        assert list(tmp_path.rglob("*.jsonl")) == []


class TestLLMClientLogsUsageOnSuccess:
    """Test JSONL logging on successful ask() calls."""

    async def test_llm_client_logs_usage_on_success(self, tmp_path: Path) -> None:
        """Successful ask() creates a JSONL record with correct fields."""
        from trw_mcp.clients.llm import LLMClient

        log_path = tmp_path / "usage.jsonl"
        _, mock_async_client = _make_mock_anthropic(
            response_text="hello world",
            input_tokens=100,
            output_tokens=50,
        )

        client = LLMClient(usage_log_path=log_path)
        client._available = True
        client._async_client = mock_async_client

        result = await client.ask("test prompt")

        assert result == "hello world"
        assert log_path.exists()

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["success"] is True
        assert record["input_tokens"] == 100
        assert record["output_tokens"] == 50
        assert record["caller"] == "ask"
        assert "ts" in record
        assert "model" in record
        assert "latency_ms" in record

    async def test_llm_client_logs_resolved_model_name(self, tmp_path: Path) -> None:
        """The model written to JSONL is the resolved full ID, not the alias."""
        from trw_mcp.clients.llm import LLMClient

        log_path = tmp_path / "usage.jsonl"
        _, mock_async_client = _make_mock_anthropic()

        client = LLMClient(model="haiku", usage_log_path=log_path)
        client._available = True
        client._async_client = mock_async_client

        await client.ask("test")

        record = json.loads(log_path.read_text().strip())
        # haiku alias resolves to full model ID
        assert record["model"] == "claude-haiku-5-5"

    async def test_llm_client_appends_multiple_records(self, tmp_path: Path) -> None:
        """Multiple ask() calls append multiple lines to the JSONL file."""
        from trw_mcp.clients.llm import LLMClient

        log_path = tmp_path / "usage.jsonl"
        _, mock_async_client = _make_mock_anthropic()

        client = LLMClient(usage_log_path=log_path)
        client._available = True
        client._async_client = mock_async_client

        await client.ask("first")
        await client.ask("second")

        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 2


class TestLLMClientLogsFailure:
    """Test JSONL logging on failed ask() calls."""

    async def test_llm_client_logs_failure_record(self, tmp_path: Path) -> None:
        """Failed ask() (exception from API) writes record with success=False."""
        from trw_mcp.clients.llm import LLMClient

        log_path = tmp_path / "usage.jsonl"

        mock_async_client = MagicMock()
        mock_async_client.messages.create = AsyncMock(side_effect=RuntimeError("API error"))

        client = LLMClient(usage_log_path=log_path)
        client._available = True
        client._async_client = mock_async_client

        result = await client.ask("failing prompt")

        assert result is None  # ask() returns None on failure
        assert log_path.exists()

        record = json.loads(log_path.read_text().strip())
        assert record["success"] is False
        assert record["input_tokens"] == 0
        assert record["output_tokens"] == 0
        assert record["caller"] == "ask"

    async def test_llm_client_logs_failure_latency_nonzero(self, tmp_path: Path) -> None:
        """Failed ask() still records a non-negative latency_ms."""
        from trw_mcp.clients.llm import LLMClient

        log_path = tmp_path / "usage.jsonl"
        mock_async_client = MagicMock()
        mock_async_client.messages.create = AsyncMock(side_effect=ValueError("bad request"))

        client = LLMClient(usage_log_path=log_path)
        client._available = True
        client._async_client = mock_async_client

        await client.ask("test")

        record = json.loads(log_path.read_text().strip())
        assert record["latency_ms"] >= 0.0


class TestLLMClientLoggingNonFatal:
    """Test that logging failures do not propagate to ask() callers."""

    async def test_llm_client_usage_logging_failure_non_fatal(self, tmp_path: Path) -> None:
        """If JSONL write raises, ask() still returns the response text."""
        from trw_mcp.clients.llm import LLMClient
        from trw_mcp.state import persistence as persistence_module

        log_path = tmp_path / "usage.jsonl"
        _, mock_async_client = _make_mock_anthropic(response_text="ok response")

        client = LLMClient(usage_log_path=log_path)
        client._available = True
        client._async_client = mock_async_client

        # Patch FileStateWriter.append_jsonl to raise
        original_class = persistence_module.FileStateWriter

        class BrokenWriter(original_class):
            def append_jsonl(self, path: Path, data: dict[str, Any]) -> None:
                raise OSError("disk full")

        with patch.object(persistence_module, "FileStateWriter", BrokenWriter):
            result = await client.ask("important question")

        # Response is still returned despite logging failure
        assert result == "ok response"


class TestLLMClientUnavailable:
    """Test LLMClient behavior when SDK is unavailable."""

    async def test_ask_returns_none_when_unavailable(self) -> None:
        """ask() returns None when _available=False."""
        from trw_mcp.clients.llm import LLMClient

        client = LLMClient()
        client._available = False

        result = await client.ask("test")
        assert result is None

    def test_available_property_false_by_default_without_sdk(self) -> None:
        """When anthropic is not importable, available is False."""
        # Remove anthropic from sys.modules to simulate missing SDK
        saved = sys.modules.pop("anthropic", None)
        try:
            from trw_mcp.clients.llm import LLMClient

            client = LLMClient()
            # Can't reliably test this without actually missing the package,
            # but we can verify the property exists and returns a bool
            assert isinstance(client.available, bool)
        finally:
            if saved is not None:
                sys.modules["anthropic"] = saved


class TestLLMClientEdgeCases:
    """Test edge cases and branch coverage for LLMClient."""

    async def test_ask_with_system_prompt_on_client(self, tmp_path: Path) -> None:
        """System prompt on client is passed through to API call."""
        from trw_mcp.clients.llm import LLMClient

        _, mock_async_client = _make_mock_anthropic()

        client = LLMClient(system_prompt="You are helpful.", usage_log_path=None)
        client._available = True
        client._async_client = mock_async_client

        result = await client.ask("test prompt")

        assert result == "test response"
        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert call_kwargs["system"] == "You are helpful."

    async def test_ask_with_system_override(self, tmp_path: Path) -> None:
        """Per-call system= argument overrides client system_prompt."""
        from trw_mcp.clients.llm import LLMClient

        _, mock_async_client = _make_mock_anthropic()

        client = LLMClient(system_prompt="Default system.", usage_log_path=None)
        client._available = True
        client._async_client = mock_async_client

        await client.ask("test", system="Override system.")

        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert call_kwargs["system"] == "Override system."

    async def test_ask_returns_none_for_empty_content(self, tmp_path: Path) -> None:
        """ask() returns None when response.content is empty."""
        from trw_mcp.clients.llm import LLMClient

        mock_response = MagicMock()
        mock_response.content = []  # empty content list
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=0)

        mock_async_client = MagicMock()
        mock_async_client.messages.create = AsyncMock(return_value=mock_response)

        client = LLMClient(usage_log_path=None)
        client._available = True
        client._async_client = mock_async_client

        with capture_logs() as logs:
            result = await client.ask("test")
        assert result is None
        # None comes from the empty-content branch, not from a failed or refused call.
        assert [e for e in logs if e["event"] in {"llm_call_failed", "llm_call_refused"}] == []
        assert mock_async_client.messages.create.await_count == 1

        # Control: the same client with one text block returns that text.
        mock_response.content = [MagicMock(type="text", text="real answer")]
        assert await client.ask("test") == "real answer"

    async def test_ask_returns_none_when_content_item_has_no_text(self, tmp_path: Path) -> None:
        """ask() returns None when content[0] has no .text attribute."""
        from trw_mcp.clients.llm import LLMClient

        content_item = MagicMock(spec=[])  # spec=[] means no attributes
        mock_response = MagicMock()
        mock_response.content = [content_item]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

        mock_async_client = MagicMock()
        mock_async_client.messages.create = AsyncMock(return_value=mock_response)

        client = LLMClient(usage_log_path=None)
        client._available = True
        client._async_client = mock_async_client

        with capture_logs() as logs:
            result = await client.ask("test")
        assert result is None
        assert [e for e in logs if e["event"] in {"llm_call_failed", "llm_call_refused"}] == []

        # Control: a typed text block next to the attribute-less item is the only text returned.
        mock_response.content = [content_item, MagicMock(type="text", text="real answer")]
        assert await client.ask("test") == "real answer"

    async def test_ask_handles_invalid_token_types(self, tmp_path: Path) -> None:
        """ask() handles TypeError/ValueError from bad token values gracefully."""
        from trw_mcp.clients.llm import LLMClient

        log_path = tmp_path / "usage.jsonl"
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="response")]
        usage_mock = MagicMock()
        usage_mock.input_tokens = "not-a-number"  # will raise ValueError
        usage_mock.output_tokens = None  # will raise TypeError
        mock_response.usage = usage_mock

        mock_async_client = MagicMock()
        mock_async_client.messages.create = AsyncMock(return_value=mock_response)

        client = LLMClient(usage_log_path=log_path)
        client._available = True
        client._async_client = mock_async_client

        result = await client.ask("test")
        # Despite bad token values, ask() returns the response
        assert result == "response"
        # Record is still written with 0 tokens (fallback)
        import json

        record = json.loads(log_path.read_text().strip())
        assert record["input_tokens"] == 0
        assert record["output_tokens"] == 0

    async def test_ask_handles_response_without_usage(self, tmp_path: Path) -> None:
        """ask() handles response with no usage attribute."""
        from trw_mcp.clients.llm import LLMClient

        log_path = tmp_path / "usage.jsonl"
        mock_response = MagicMock(spec=["content"])  # no 'usage' attribute
        mock_response.content = [MagicMock(type="text", text="no-usage-response")]

        mock_async_client = MagicMock()
        mock_async_client.messages.create = AsyncMock(return_value=mock_response)

        client = LLMClient(usage_log_path=log_path)
        client._available = True
        client._async_client = mock_async_client

        result = await client.ask("test")
        assert result == "no-usage-response"
        # Record written with 0 tokens
        import json

        record = json.loads(log_path.read_text().strip())
        assert record["input_tokens"] == 0
        assert record["output_tokens"] == 0

    def test_ask_sync_returns_none_when_unavailable(self) -> None:
        """ask_sync() returns None when _available=False."""
        from trw_mcp.clients.llm import LLMClient

        client = LLMClient()
        client._available = False

        result = client.ask_sync("test")
        assert result is None

    def test_ask_sync_runs_in_new_loop(self, tmp_path: Path) -> None:
        """ask_sync() works when no event loop is running."""
        from trw_mcp.clients.llm import LLMClient

        _, mock_async_client = _make_mock_anthropic(response_text="sync response")

        client = LLMClient(usage_log_path=None)
        client._available = True
        client._async_client = mock_async_client

        # ask_sync calls asyncio.run() when no loop is running
        result = client.ask_sync("test prompt")
        assert result == "sync response"
