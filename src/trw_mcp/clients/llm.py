"""LLM client abstraction over Anthropic SDK.

Provides a thin wrapper that gracefully degrades when the SDK is
not installed. Tools check ``LLMClient.available`` before calling
and fall back to pure-Python logic when unavailable.

The default model is Haiku for cost efficiency; callers can
override per-request.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger()

# PRD-CORE-001: Base MCP tool suite — optional LLM augmentation client

_ASK_TIMEOUT_SECS = 120

_MODEL_MAP: dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}


def _resolve_model(alias: str) -> str:
    """Resolve a short model alias to a full model ID."""
    return _MODEL_MAP.get(alias, alias)


class LLMClient:
    """Abstraction over Anthropic SDK for internal LLM calls.

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
        self._client: Any = None
        self._async_client: Any = None

        try:
            import anthropic  # type: ignore[import-not-found]

            self._client = anthropic.Anthropic()
            self._async_client = anthropic.AsyncAnthropic()
            self._available = True
        except ImportError:
            logger.warning("LLM features disabled — install with: pip install trw-mcp[ai]")

    @property
    def available(self) -> bool:
        """Whether the Anthropic SDK is installed and usable."""
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
            max_turns: Override max turns for this call (unused — reserved for future use).

        Returns:
            The assistant's text response, or ``None`` on failure/unavailability.
        """
        if not self._available or self._async_client is None:
            return None

        try:
            kwargs: dict[str, Any] = {
                "model": _resolve_model(model or self._model),
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            }

            effective_system = system or self._system_prompt
            if effective_system:
                kwargs["system"] = effective_system

            response = await self._async_client.messages.create(**kwargs)

            if response.content:
                return str(response.content[0].text) if hasattr(response.content[0], "text") else None
            return None

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

        coro = self.ask(prompt, system=system, model=model, max_turns=max_turns)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=_ASK_TIMEOUT_SECS)
