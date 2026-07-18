"""LLMClient refusal-handling tests (PRD-CORE-210 FR04).

Claude-5-family models return HTTP-200 refusals (stop_reason="refusal")
with empty or partial content; the client must branch on stop_reason and
never return a partial as a complete answer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from trw_mcp.clients.llm import LLMClient


def _wired_client(response: object) -> LLMClient:
    client = LLMClient()
    client._available = True
    mock_async = MagicMock()
    mock_async.messages.create = AsyncMock(return_value=response)
    client._async_client = mock_async
    return client


def _response(
    *,
    stop_reason: str,
    text: str,
    category: str | None = None,
) -> SimpleNamespace:
    stop_details = (
        SimpleNamespace(type="refusal", category=category, explanation=None) if stop_reason == "refusal" else None
    )
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=stop_details,
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


@pytest.mark.asyncio
async def test_refusal_with_partial_content_returns_none() -> None:
    client = _wired_client(_response(stop_reason="refusal", text="partial out", category="cyber"))
    assert await client.ask("prompt", model="claude-fable-5") is None


@pytest.mark.asyncio
async def test_refusal_with_null_category_returns_none() -> None:
    # stop_details.category is legitimately null on some refusals.
    client = _wired_client(_response(stop_reason="refusal", text="", category=None))
    assert await client.ask("prompt", model="claude-fable-5") is None


@pytest.mark.asyncio
async def test_end_turn_still_returns_text() -> None:
    client = _wired_client(_response(stop_reason="end_turn", text="answer"))
    assert await client.ask("prompt") == "answer"


@pytest.mark.asyncio
async def test_response_without_stop_reason_attr_is_unaffected() -> None:
    # Older SDK response fixtures without the attribute keep working.
    response = SimpleNamespace(
        content=[SimpleNamespace(text="legacy")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    client = _wired_client(response)
    assert await client.ask("prompt") == "legacy"
