"""LLM client abstraction over Anthropic SDK.

Provides a thin wrapper that gracefully degrades when the SDK is
not installed. Tools check ``LLMClient.available`` before calling
and fall back to pure-Python logic when unavailable.

The default model is Haiku for cost efficiency; callers can
override per-request.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# PRD-CORE-001: Base MCP tool suite — optional LLM augmentation client

_ASK_TIMEOUT_SECS = 120

# FIX-046-FR05: Shared executor for sync-to-async bridge (avoids per-call creation)
import concurrent.futures

_SHARED_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def _get_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Lazily create and cache a shared ThreadPoolExecutor."""
    global _SHARED_EXECUTOR  # noqa: PLW0603
    if _SHARED_EXECUTOR is None:
        _SHARED_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    return _SHARED_EXECUTOR

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
        usage_log_path: Path | None = None,
    ) -> None:
        self._model = model
        self._max_turns = max_turns
        self._system_prompt = system_prompt
        self._usage_log_path = usage_log_path
        self._available = False
        self._client: Any = None
        self._async_client: Any = None

        try:
            import anthropic  # type: ignore[import-not-found,unused-ignore]

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

        resolved_model = _resolve_model(model or self._model)
        start = time.monotonic()

        try:
            kwargs: dict[str, Any] = {
                "model": resolved_model,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            }

            effective_system = system or self._system_prompt
            if effective_system:
                kwargs["system"] = effective_system

            response = await self._async_client.messages.create(**kwargs)

            latency_ms = (time.monotonic() - start) * 1000
            input_tokens = 0
            output_tokens = 0
            if hasattr(response, "usage"):
                try:
                    input_tokens = int(response.usage.input_tokens)
                    output_tokens = int(response.usage.output_tokens)
                except (TypeError, ValueError):
                    logger.debug("usage_token_parse_failed", exc_info=True)
            self._append_usage_record(resolved_model, input_tokens, output_tokens, latency_ms, success=True)

            if response.content:
                return str(response.content[0].text) if hasattr(response.content[0], "text") else None
            return None

        except Exception:  # justified: boundary, external Anthropic API can raise arbitrary errors
            latency_ms = (time.monotonic() - start) * 1000
            self._append_usage_record(resolved_model, 0, 0, latency_ms, success=False)
            from trw_mcp.telemetry.anonymizer import strip_pii

            logger.warning(
                "llm_call_failed",
                prompt_preview=strip_pii(prompt[:80]),
                exc_info=True,
            )
            return None

    def _append_usage_record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        success: bool,
    ) -> None:
        """Append a usage record to the JSONL log (non-fatal)."""
        usage_log_path: Path | None = getattr(self, "_usage_log_path", None)
        if usage_log_path is None:
            return
        try:
            from trw_mcp.state.persistence import FileStateWriter  # local import avoids circular

            record: dict[str, object] = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": round(latency_ms, 2),
                "caller": "ask",
                "success": success,
            }
            FileStateWriter().append_jsonl(usage_log_path, record)
        except Exception:  # justified: fail-open telemetry, usage logging never blocks LLM calls
            logger.warning("llm_usage_log_failed", exc_info=True)

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

        executor = _get_executor()
        future = executor.submit(asyncio.run, coro)
        return future.result(timeout=_ASK_TIMEOUT_SECS)
