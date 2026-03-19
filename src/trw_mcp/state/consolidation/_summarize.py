"""LLM summarization for memory consolidation — FR02, FR05.

LLM-powered cluster summarization with retry logic and longest-entry fallback.

NOTE: _summarize_cluster_llm looks up ``LLMClient`` from the parent package
at call time so that ``patch("trw_mcp.state.consolidation.LLMClient")``
works after the flat module was converted to a package.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

import structlog
from trw_memory.lifecycle.consolidation import (
    _parse_consolidation_response,
    _redact_paths,
)

from trw_mcp.models.typed_dicts import LearningEntryDict

if TYPE_CHECKING:
    from trw_mcp.clients.llm import LLMClient

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# FR02 — LLM-Powered Cluster Summarization
# ---------------------------------------------------------------------------


def _summarize_cluster_llm(
    cluster: Sequence[LearningEntryDict],
    llm: LLMClient | None = None,
) -> dict[str, str] | None:
    """Summarize a cluster of entries into a single consolidated entry via LLM.

    Builds a prompt containing all cluster entries' summary and detail,
    requests JSON output with "summary" and "detail" keys, and validates
    that the output is shorter than the sum of inputs. Retries once with
    an explicit length constraint if the first response is too long.

    Args:
        cluster: List of entry dicts representing the cluster.
        llm: Optional LLMClient instance. Instantiates one if None.

    Returns:
        Dict with "summary" and "detail" keys, or None on failure.
    """
    if llm is not None:
        client = llm
    else:
        # Late-bind LLMClient from the package so patch targets work
        _LLMClient = getattr(sys.modules["trw_mcp.state.consolidation"], "LLMClient")  # noqa: B009
        client = _LLMClient(model="haiku")

    # Build prompt (NFR06: redact filesystem paths before sending to LLM)
    entries_text = "\n".join(
        f"Entry {i + 1}:\n  summary: {_redact_paths(str(e.get('summary', '')))}\n  detail: {_redact_paths(str(e.get('detail', '')))}"
        for i, e in enumerate(cluster)
    )
    prompt = (
        "Consolidate the following related learning entries into a single entry.\n"
        "Respond with exactly one JSON object on a single line:\n"
        '{"summary": "concise one-liner", "detail": "merged explanation"}\n\n' + entries_text
    )
    system = "You are a knowledge consolidation assistant. Be concise and precise."

    total_input_len = sum(len(str(e.get("summary", ""))) for e in cluster)

    response: str | None = client.ask_sync(prompt, system=system)
    if response is None:
        return None

    result = _parse_consolidation_response(response)
    if result is None:
        return None

    # Length check: consolidated summary must be shorter than the sum of inputs
    if len(result["summary"]) < total_input_len:
        return result

    # Retry once with explicit length constraint
    max_chars = max(50, total_input_len // 2)
    retry_prompt = f"{prompt}\n\nIMPORTANT: The summary must be under {max_chars} characters."
    retry_response: str | None = client.ask_sync(retry_prompt, system=system)
    if retry_response is None:
        return None

    return _parse_consolidation_response(retry_response)


# ---------------------------------------------------------------------------
# FR05 — Graceful Degradation Without LLM
# ---------------------------------------------------------------------------


def _summarize_cluster_fallback(
    cluster: Sequence[LearningEntryDict],
) -> dict[str, str]:
    """Select the longest-content entry as the consolidated summary/detail.

    Used when LLM is unavailable or summarization fails.
    Logs at INFO level with cluster_size.

    Args:
        cluster: List of entry dicts in the cluster.

    Returns:
        Dict with "summary" and "detail" from the best entry.
    """
    best = max(
        cluster,
        key=lambda e: len(str(e.get("summary", ""))) + len(str(e.get("detail", ""))),
    )
    logger.info(
        "consolidation_llm_fallback",
        cluster_size=len(cluster),
        selected_id=str(best.get("id", "")),
    )
    return {
        "summary": str(best.get("summary", "")),
        "detail": str(best.get("detail", "")),
    }
