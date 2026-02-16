"""LLM client abstraction over Claude Agent SDK.

Provides a thin wrapper that gracefully degrades when the SDK is
not installed. Tools check ``LLMClient.available`` before calling
and fall back to pure-Python logic when unavailable.

The default model is Haiku for cost efficiency; callers can
override per-request.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

import structlog

logger = structlog.get_logger()

# PRD-CORE-001: Base MCP tool suite — optional LLM augmentation client


class _SDKQueryProtocol(Protocol):
    """Protocol matching the ``claude_agent_sdk.query`` async generator."""

    def __call__(
        self,
        *,
        prompt: str,
        options: object,
        model: str,
    ) -> AsyncIterator[object]: ...


class LLMClient:
    """Abstraction over Claude Agent SDK for internal LLM calls.

    Gracefully degrades: ``ask()`` returns ``None`` when the SDK
    is unavailable.  Uses Haiku by default for cost efficiency.

    Args:
        model: Default model identifier — ``"haiku"``, ``"sonnet"``, or ``"opus"``.
        max_turns: Maximum agentic turns per query (default 1 for simple Q&A).
        system_prompt: Optional system prompt applied to all queries.
    """

    def __init__(
        self,
        model: str = "haiku",
        max_turns: int = 1,
        system_prompt: str = "",
    ) -> None:
        self._model = model
        self._max_turns = max_turns
        self._system_prompt = system_prompt
        self._available = False
        self._query_fn: _SDKQueryProtocol | None = None

        try:
            from claude_agent_sdk import query as sdk_query  # type: ignore[import-not-found]

            self._query_fn = sdk_query
            self._available = True
        except ImportError:
            logger.warning("claude_agent_sdk import failed — LLM features disabled")

    @property
    def available(self) -> bool:
        """Whether the Claude Agent SDK is installed and usable."""
        return self._available

    async def ask(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        max_turns: int | None = None,
    ) -> str | None:
        """Send a prompt to Claude and return the text response.

        Returns ``None`` if the SDK is unavailable or the call fails.

        Args:
            prompt: The user prompt to send.
            system: Override system prompt for this call.
            model: Override model for this call.
            max_turns: Override max turns for this call.

        Returns:
            The assistant's text response, or ``None`` on failure/unavailability.
        """
        if not self._available or self._query_fn is None:
            return None

        try:  # pragma: no cover — requires claude-agent-sdk
            from claude_agent_sdk import (
                AssistantMessage,
                ClaudeAgentOptions,
                TextBlock,
            )

            opts = ClaudeAgentOptions(
                system_prompt=system or self._system_prompt,
                max_turns=max_turns or self._max_turns,
            )

            text_parts: list[str] = []
            async for message in self._query_fn(
                prompt=prompt,
                options=opts,
                model=model or self._model,
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)

            result = "\n".join(text_parts).strip()
            return result if result else None

        except Exception:
            logger.warning(
                "llm_call_failed",
                prompt_preview=prompt[:80],
                exc_info=True,
            )
            return None

    def ask_sync(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        max_turns: int | None = None,
    ) -> str | None:
        """Synchronous wrapper around ``ask()``.

        Detects whether an event loop is already running and handles
        accordingly. Safe to call from synchronous MCP tool handlers.

        Args:
            prompt: The user prompt to send.
            system: Override system prompt for this call.
            model: Override model for this call.
            max_turns: Override max turns for this call.

        Returns:
            The assistant's text response, or ``None`` on failure/unavailability.
        """
        if not self._available:
            return None

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():  # pragma: no cover
            # We're inside an already-running loop (e.g. FastMCP).
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self.ask(
                    prompt, system=system, model=model, max_turns=max_turns,
                ))
                return future.result(timeout=120)
        else:  # pragma: no cover
            return asyncio.run(self.ask(
                prompt, system=system, model=model, max_turns=max_turns,
            ))
