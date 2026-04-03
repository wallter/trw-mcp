"""Compression helpers for observation masking middleware.

Extracted from context_budget.py to stay within the 200-line module gate.
Provides tier-aware JSON/text compression and content hashing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, cast

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

SHALLOW_DICT_KEYS: frozenset[str] = frozenset(
    {
        "run",
        "reflect",
        "checkpoint",
        "claude_md_sync",
        "auto_upgrade",
        "stale_runs_closed",
        "embeddings_backfill",
        "embed_health",
        "assertion_status",
    }
)


def truncate(s: str, limit: int) -> str:
    """Truncate string to limit chars, appending ellipsis if truncated."""
    if len(s) <= limit:
        return s
    return s[:limit] + "\u2026"


def _is_learning_item(value: object) -> bool:
    """Detect compact learning-like dicts returned by recall/session_start."""
    return (
        isinstance(value, dict)
        and "id" in value
        and "summary" in value
    )


def _should_strip_key(
    key: str,
    data: dict[str, object],
) -> bool:
    """Return True when a top-level key should be removed for compactness."""
    if key in STRIP_KEYS:
        return True

    # Recall payloads often carry bulky architecture/conventions context.
    # Drop that before truncating the actual learning content.
    if key == "context" and ("learnings" in data or "patterns" in data or "auto_recalled" in data):
        return True

    return False


def _compress_learning_item(
    item: dict[str, object],
    tier: Literal["compact", "minimal"],
) -> dict[str, object]:
    """Shrink learning entries while preserving useful summary text."""
    summary_limit = 300 if tier == "minimal" else 500
    compressed: dict[str, object] = {}

    if "id" in item:
        compressed["id"] = item["id"]
    if "summary" in item:
        compressed["summary"] = truncate(str(item["summary"]), summary_limit)
    if "impact" in item:
        compressed["impact"] = item["impact"]

    status = item.get("status")
    if status not in (None, "", "active"):
        compressed["status"] = status

    if item.get("injected") is True:
        compressed["injected"] = True

    return compressed


def _compress_shallow_dict(
    value: dict[str, object],
    tier: Literal["compact", "minimal"],
    *,
    str_limit: int,
) -> dict[str, object]:
    """Keep nested status-style dicts readable without preserving deep payloads."""
    compressed: dict[str, object] = {}
    scalar_list_limit = 3 if tier == "minimal" else 5

    for subkey, subvalue in value.items():
        if isinstance(subvalue, str):
            compressed[subkey] = truncate(subvalue, str_limit)
        elif isinstance(subvalue, (int, float, bool)) or subvalue is None:
            compressed[subkey] = subvalue
        elif isinstance(subvalue, list):
            if all(not isinstance(item, (dict, list)) for item in subvalue):
                compressed[subkey] = subvalue[:scalar_list_limit]
            else:
                compressed[subkey] = "[nested]"
        else:
            compressed[subkey] = "[nested]"

    return compressed


def _compress_value(
    key: str,
    value: object,
    tier: Literal["compact", "minimal"],
    *,
    str_limit: int,
) -> object:
    """Compress a top-level JSON value with recall-aware structure handling."""
    if key in {"learnings", "auto_recalled"} and isinstance(value, list):
        return [
            _compress_learning_item(cast("dict[str, object]", item), tier)
            if _is_learning_item(item)
            else strip_deep(item, max_depth=1 if tier == "minimal" else 2)
            for item in value
        ]

    if key in SHALLOW_DICT_KEYS and isinstance(value, dict):
        return _compress_shallow_dict(value, tier, str_limit=str_limit)

    if isinstance(value, str):
        return truncate(value, str_limit)

    if tier == "minimal" and isinstance(value, dict):
        return strip_deep(value, max_depth=1)

    if tier == "minimal" and isinstance(value, list):
        return [strip_deep(item, max_depth=1) for item in value]

    return value


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
        if _should_strip_key(k, data):
            continue
        result[k] = _compress_value(k, v, tier, str_limit=str_limit)

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
