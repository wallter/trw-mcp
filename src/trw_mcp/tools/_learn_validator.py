"""LLM-based utility validation for trw_learn.

Provides a lightweight scoring pass using a fast model (e.g. Haiku) to reject
summaries that offer no durable institutional knowledge.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.clients.llm import LLMClient

logger = structlog.get_logger(__name__)


def is_high_utility(
    summary: str,
    detail: str,
    llm: LLMClient,
) -> tuple[bool, str]:
    """Use LLM to validate whether a learning contains durable technical insight.

    Args:
        summary: The one-line summary of the learning.
        detail: The full context/explanation.
        llm: LLM client instance (must support ask_sync).

    Returns:
        tuple[is_valid: bool, reason: str]
        If valid, reason is empty.
        If rejected, reason contains the LLM's explanation.
        If LLM call fails or is unavailable, fails open (returns True, "").
    """
    if not summary:
        return False, "Summary is empty."

    prompt = (
        "Evaluate the following software engineering learning for utility.\n"
        "A high-utility learning captures a durable technical insight, pattern, gotcha, "
        "or architectural decision that would prevent future mistakes.\n"
        "A low-utility learning is a routine status update, a claim like 'task completed', "
        "'PRD groomed', or a generic statement of work done without technical depth.\n\n"
        f"Summary: {summary}\n"
        f"Detail: {detail}\n\n"
        "Respond with a single JSON object containing 'valid' (boolean) and 'reason' (string explaining why if invalid, or empty string if valid):\n"
        '{"valid": true|false, "reason": "..."}'
    )

    try:
        response = llm.ask_sync(
            prompt,
            system="You are a stringent quality gate for an engineering knowledge base. Reject routine status updates. Be concise.",
        )

        if not response:
            return True, ""  # Fail open if LLM is unavailable

        # Extract JSON from response (handling potential markdown fences)
        text = response.strip()
        if text.startswith("```json"):
            text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
        text = text.strip()
        
        parsed = json.loads(text)
        is_valid = bool(parsed.get("valid", True))
        reason = str(parsed.get("reason", ""))
        
        if not is_valid:
            logger.info("learn_llm_validation_rejected", summary=summary[:50], reason=reason)
            
        return is_valid, reason

    except Exception as exc:
        logger.warning("learn_llm_validation_failed", error=str(exc))
        return True, ""  # Fail open on error
