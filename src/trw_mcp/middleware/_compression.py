"""Compression helpers for observation masking middleware.

Extracted from context_budget.py to stay within the 200-line module gate.
Provides tier-aware JSON/text compression and content hashing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from mcp.types import TextContent

# Keys stripped at compact tier and above
STRIP_KEYS: frozenset[str] = frozenset(
    {
        "metadata",
        "details",
        "ceremony",
        "ceremony_status",
        "debug",
        "analytics",
        "recommendations",
        "skill_chaining",
    }
)


def truncate(s: str, limit: int) -> str:
    """Truncate string to limit chars, appending ellipsis if truncated."""
    if len(s) <= limit:
        return s
    return s[:limit] + "\u2026"


def strip_deep(data: object, max_depth: int, current: int = 0) -> object:
    """Recursively strip objects deeper than max_depth levels."""
    if current >= max_depth:
        if isinstance(data, (dict, list)):
            return "[nested]"
        return data
    if isinstance(data, dict):
        return {
            k: strip_deep(v, max_depth, current + 1)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [strip_deep(item, max_depth, current + 1) for item in data]
    return data


def compress_json(
    data: object,
    tier: Literal["compact", "minimal"],
) -> object:
    """Apply tier-appropriate compression to parsed JSON data."""
    if not isinstance(data, dict):
        return data

    result: dict[str, Any] = {}
    str_limit = 100 if tier == "minimal" else 200

    for k, v in data.items():
        if k in STRIP_KEYS:
            continue
        if isinstance(v, str):
            result[k] = truncate(v, str_limit)
        else:
            result[k] = v

    if tier == "minimal":
        stripped = strip_deep(result, max_depth=2)
        # strip_deep preserves dict structure for dict inputs
        if isinstance(stripped, dict):
            result = stripped

    return result


def compress_text_block(
    text: str,
    tier: Literal["full", "compact", "minimal"],
) -> str:
    """Compress a single TextContent text value based on tier."""
    if tier == "full":
        return text

    # Try JSON compression first
    if text and text[0] in ("{", "["):
        try:
            parsed = json.loads(text)
            compressed = compress_json(parsed, tier)
            return json.dumps(compressed, separators=(",", ":"))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Non-JSON text truncation
    if tier == "minimal":
        limit = 200
        suffix = "\n[\u2026 truncated]"
    else:
        limit = 500
        suffix = "\n[\u2026 truncated \u2014 use trw_status() for full output]"

    if len(text) <= limit:
        return text
    return text[:limit] + suffix


def hash_content(content: list[Any]) -> str:
    """SHA-256 hash of all TextContent text in a result."""
    h = hashlib.sha256()
    for block in content:
        if isinstance(block, TextContent):
            h.update(block.text.encode("utf-8"))
    return h.hexdigest()
