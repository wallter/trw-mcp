"""LLM client abstraction over the optional Anthropic adapter.

Provides a thin wrapper that gracefully degrades when the SDK is
not installed. Tools check ``LLMClient.available`` before calling
and fall back to pure-Python logic when unavailable.

The default uses the adapter's fast/low-cost alias; callers can override
per-request. Core TRW logic must not depend on a particular vendor model.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# PRD-CORE-001: Base MCP tool suite — optional LLM augmentation client

_ASK_TIMEOUT_SECS = 120

_SHARED_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None


def _get_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Lazily create and cache a shared ThreadPoolExecutor."""
    global _SHARED_EXECUTOR
    if _SHARED_EXECUTOR is None:
        _SHARED_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    return _SHARED_EXECUTOR


_MODEL_MAP: dict[str, str] = {
    "fast": "claude-haiku-4-5-20251001",
    "balanced": "claude-sonnet-5",
    "frontier": "claude-opus-5",
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-5",
}

#: Output-token ceiling for a single internal augmentation call.
#:
#: This is a cap on *thinking plus response text combined*, not a spend — an
#: unused ceiling costs nothing. It is deliberately larger than a short answer
#: needs because the current Anthropic generation (Opus 5, Sonnet 5, Fable 5)
#: runs adaptive thinking when the ``thinking`` parameter is omitted, whereas
#: the generation this client was originally written against (Opus 4.7,
#: Sonnet 4.6) did not. Under the previous 1024 ceiling a thinking model could
#: spend the entire budget reasoning and return a truncated answer or none at
#: all — a silent quality failure, since ``ask`` degrades to ``None``.
_MAX_OUTPUT_TOKENS = 4096

#: Effort level requested for internal augmentation calls, when the resolved
#: model declares support for it.
#:
#: ``LLMClient`` exists for short, single-turn, cost-sensitive augmentation
#: (its default model is Haiku). Left unset, the API default is ``high``, which
#: on a thinking-by-default model turns every internal call into a deep
#: reasoning request. ``low`` keeps this client's documented cost posture
#: intact across the model bump.
_INTERNAL_EFFORT = "low"


def _resolve_model(alias: str) -> str:
    """Resolve a short model alias to a full model ID."""
    return _MODEL_MAP.get(alias, alias)


def _effort_for(model_id: str) -> str | None:
    """Return the effort level to request for *model_id*, or ``None`` to omit.

    Delegates to the trusted model-capability catalog (PRD-CORE-209) rather
    than carrying a second model table. Sending ``effort`` to a model that does
    not accept it is an API error, not a no-op — Haiku 4.5, this client's own
    default model, is exactly such a model. The catalog distinguishes the three
    cases that matter here: a declared-supported set, an empty set (the model
    rejects the parameter), and ``None`` (unknown model). Only the first sets
    the parameter, so an unrecognised or future model is never sent a request
    shape that could fail.
    """
    from trw_mcp.models.config import lookup_model_effort_capabilities

    declared = lookup_model_effort_capabilities(model_id)
    if declared and _INTERNAL_EFFORT in declared:
        return _INTERNAL_EFFORT
    return None


class LLMClient:
    """Abstraction over Anthropic SDK for internal LLM calls.

    Gracefully degrades: ``ask()`` returns ``None`` when the SDK
    is unavailable. Uses the adapter's fast/low-cost default for cost
    efficiency.

    Args:
        model: Default model identifier. Capability aliases (``"fast"``,
            ``"balanced"``, ``"frontier"``) are preferred; legacy Anthropic
            aliases remain supported for compatibility.
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
        except Exception as e:
            logger.info("Anthropic client initialization deferred: %s", e)

    @property
    def available(self) -> bool:
        """Whether the Anthropic SDK is installed and usable, or local models are available."""
        return self._available or self._model.startswith("ollama/") or bool(os.environ.get("OLLAMA_HOST"))

    async def ask(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        max_turns: int | None = None,
    ) -> str | None:
        """Send a prompt to Claude or local Ollama and return the text response.

        Returns ``None`` if the SDK/Ollama is unavailable or the call fails.

        Args:
            prompt: The user prompt to send.
            system: Override system prompt for this call.
            model: Override model for this call.
            max_turns: Override max turns for this call (unused — reserved for future use).

        Returns:
            The assistant's text response, or ``None`` on failure/unavailability.
        """
        resolved_model = model or self._model
        if resolved_model.startswith("ollama/"):
            ollama_model = resolved_model.split("/", 1)[1]
            return await self._ask_ollama(prompt, system=system, model=ollama_model)

        if not self._available or self._async_client is None:
            # Check if we have OLLAMA_HOST and can try falling back to Ollama
            if os.environ.get("OLLAMA_HOST"):
                return await self._ask_ollama(prompt, system=system, model=resolved_model)
            return None

        resolved_model = _resolve_model(resolved_model)
        start = time.monotonic()

        try:
            kwargs: dict[str, Any] = {
                "model": resolved_model,
                "max_tokens": _MAX_OUTPUT_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
            }

            effort = _effort_for(resolved_model)
            if effort is not None:
                kwargs["output_config"] = {"effort": effort}

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

            # PRD-CORE-210 FR04: Claude-5-family models return HTTP-200
            # refusals (stop_reason="refusal") with empty or PARTIAL content.
            # Branch before reading content so a partial is never returned
            # as a complete answer; None keeps the graceful-degrade contract.
            if getattr(response, "stop_reason", None) == "refusal":
                stop_details = getattr(response, "stop_details", None)
                logger.warning(
                    "llm_call_refused",
                    model=resolved_model,
                    category=getattr(stop_details, "category", None),
                )
                return None

            if response.content:
                return str(response.content[0].text) if hasattr(response.content[0], "text") else None
            return None

        except Exception as exc:  # justified: boundary, external Anthropic API can raise arbitrary errors
            latency_ms = (time.monotonic() - start) * 1000
            self._append_usage_record(resolved_model, 0, 0, latency_ms, success=False)
            from trw_mcp.telemetry.anonymizer import strip_pii

            # A retired or mistyped model id is a 404, and folding it into the
            # generic warning makes it indistinguishable from "the SDK isn't
            # installed" — both surface to the caller as a bare None. Model ids
            # are a maintained table (_MODEL_MAP) that goes stale on every
            # Anthropic release, so this is the failure most likely to be
            # introduced by an edit and the least likely to be noticed.
            if type(exc).__name__ == "NotFoundError":
                logger.warning(
                    "llm_model_unknown",
                    model=resolved_model,
                    remedy="model id was rejected by the API — check _MODEL_MAP against the current Anthropic roster",
                )
            else:
                logger.warning(
                    "llm_call_failed",
                    prompt_preview=strip_pii(prompt[:80]),
                    exc_info=True,
                )
            return None

    async def _ask_ollama(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str = "qwen2.5-coder",
    ) -> str | None:
        """Send a prompt to local Ollama and return the text response."""
        import httpx

        host = os.environ.get("OLLAMA_HOST") or os.environ.get("OLLAMA_API_BASE") or "http://localhost:11434"
        url = f"{host.rstrip('/')}/api/generate"
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }

        effective_system = system or self._system_prompt
        if effective_system:
            payload["system"] = effective_system

        start = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=120.0)
            latency_ms = (time.monotonic() - start) * 1000

            if response.status_code != 200:
                self._append_usage_record(model, 0, 0, latency_ms, success=False)
                return None

            data = response.json()
            response_text = str(data.get("response", ""))

            eval_count = int(data.get("eval_count", 0) or 0)
            self._append_usage_record(model, 0, eval_count, latency_ms, success=True)
            return response_text
        except Exception:
            latency_ms = (time.monotonic() - start) * 1000
            self._append_usage_record(model, 0, 0, latency_ms, success=False)
            from trw_mcp.telemetry.anonymizer import strip_pii

            logger.warning(
                "ollama_llm_call_failed",
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
        if not self.available:
            return None

        coro = self.ask(prompt, system=system, model=model, max_turns=max_turns)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        executor = _get_executor()
        future = executor.submit(asyncio.run, coro)
        return future.result(timeout=_ASK_TIMEOUT_SECS)
