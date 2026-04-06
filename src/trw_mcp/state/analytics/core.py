"""Analytics core — constants and shared infrastructure.

Module A of the analytics decomposition.  All other analytics_* modules
import shared helper functions and constants from here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.state._helpers import iter_yaml_entry_files, safe_float, safe_int
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)

# Explicit re-export aliases for mypy --strict (X as X pattern not possible
# when renaming, so we use module-level assignment + __all__ listing).
_safe_float = safe_float
_safe_int = safe_int

__all__ = [
    "_ERROR_KEYWORDS",
    "_NOISE_PREFIXES",
    "_SLUG_MAX_LEN",
    "_SUCCESS_KEYWORDS",
    "_TOPIC_KEYWORD_MAP",
    "_TOPIC_TAG_MAX",
    "_entries_path",
    "_get_event_type",
    "_iter_entry_files",
    "_safe_float",
    "_safe_int",
    "find_entry_by_id",
    "generate_learning_id",
    "infer_topic_tags",
    "is_error_event",
    "is_noise_summary",
    "is_success_event",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SLUG_MAX_LEN = 40
_ERROR_KEYWORDS = ("error", "fail", "exception", "crash", "timeout")
_SUCCESS_KEYWORDS = (
    "complete",
    "success",
    "pass",
    "done",
    "finish",
    "delivered",
    "approved",
    "resolved",
    "merged",
)

# ---------------------------------------------------------------------------
# QUAL-018 FR03: Topic tag inference from summary keywords
# ---------------------------------------------------------------------------

_TOPIC_KEYWORD_MAP: dict[str, str] = {
    # Testing
    "test": "testing",
    "tests": "testing",
    "pytest": "testing",
    "coverage": "testing",
    "fixture": "testing",
    "mock": "testing",
    # Architecture
    "architecture": "architecture",
    "design": "architecture",
    "pattern": "architecture",
    "refactor": "architecture",
    # Configuration
    "config": "configuration",
    "settings": "configuration",
    "env": "configuration",
    "environment": "configuration",
    # Deployment
    "deploy": "deployment",
    "bootstrap": "deployment",
    "install": "deployment",
    "package": "deployment",
    # Performance
    "performance": "performance",
    "cache": "performance",
    "latency": "performance",
    "timeout": "performance",
    # Security
    "security": "security",
    "auth": "security",
    "token": "security",
    "jwt": "security",
    "rbac": "security",
    # Database
    "database": "database",
    "sqlite": "database",
    "migration": "database",
    "sql": "database",
    "query": "database",
    # API
    "api": "api",
    "endpoint": "api",
    "route": "api",
    "rest": "api",
    "mcp": "api",
    # Documentation
    "docs": "documentation",
    "readme": "documentation",
    "prd": "documentation",
    "changelog": "documentation",
    # Debugging
    "debug": "debugging",
    "error": "debugging",
    "bug": "debugging",
    "fix": "debugging",
    "trace": "debugging",
    # Pricing / Cost
    "cost": "pricing",
    "price": "pricing",
    "pricing": "pricing",
    "billing": "pricing",
    "budget": "pricing",
    # Rate limiting
    "rate": "rate-limiting",
    "limit": "rate-limiting",
    "throttle": "rate-limiting",
    "ratelimit": "rate-limiting",
}

_TOPIC_TAG_MAX = 3

# Auto-generated noise prefixes that should never be persisted as learnings.
# These are produced by ceremony/telemetry tools and add no institutional value.
# Canonical location — tools/_learning_helpers.py re-exports for backward compat.
_NOISE_PREFIXES = (
    "Repeated operation:",
    "Success:",
    "Task completed:",
    "Confirmed:",
    "Done:",
    "Completed:",
)

# PRD-CORE-119 M-2: Regex patterns for common low-value agent outputs.
# These catch file-read confirmations, test-pass notifications, simple edit
# confirmations, and status acknowledgments that add no institutional value.
# Patterns are deliberately specific to avoid rejecting valid learnings
# (e.g., "File reads fail silently..." should NOT match).
_NOISE_REGEX = re.compile(
    r"^(?:"
    r"i read the \w+"  # "I read the file", "I read the configuration"
    r"|(?:the |all )?tests? (?:passed|are passing)"  # "The test passed", "All tests passed"
    r"|i made the (?:edit|change)"  # "I made the edit", "I made the change"
    r"|updated the (?:file|code)\b"  # "Updated the file", "Updated the code"
    r"|the build (?:completed|passed|succeeded)"  # "The build completed successfully"
    r")",
    flags=re.IGNORECASE,
)


def is_noise_summary(summary: str) -> bool:
    """Return True if summary matches a known auto-generated noise pattern.

    PRD-QUAL-032-FR09: Reject entries whose summary starts with known
    noise prefixes before they are persisted.

    PRD-CORE-119-M2: Also rejects regex-matched low-value agent outputs
    such as file-read confirmations, test-pass notifications, simple edit
    confirmations, and status acknowledgments.

    PRD-FIX-061-FR01: Canonical location moved from tools/_learning_helpers.py
    to state/analytics/core.py to resolve tools/ -> state/ layer violation.

    Args:
        summary: The learning entry summary text to check.

    Returns:
        True if the summary matches a known noise prefix or regex pattern.
    """
    lower = summary.lower()
    if any(lower.startswith(prefix.lower()) for prefix in _NOISE_PREFIXES):
        return True
    return bool(_NOISE_REGEX.search(lower))


# ---------------------------------------------------------------------------
# Shared helper functions
# ---------------------------------------------------------------------------


def infer_topic_tags(
    summary: str,
    existing_tags: list[str] | None = None,
) -> list[str]:
    """Infer topic tags from a learning summary using keyword matching.

    Scans ``summary`` tokens against ``_TOPIC_KEYWORD_MAP`` and returns
    0-3 new tags not already present in ``existing_tags`` (case-insensitive
    dedup).  Never raises -- returns empty list on any error.

    Args:
        summary: Learning summary text to scan for topic keywords.
        existing_tags: Tags already associated with the entry (used for dedup).

    Returns:
        List of 0-3 inferred tag strings.
    """
    try:
        if not summary:
            return []
        existing_lower = {t.lower() for t in (existing_tags or [])}
        tokens = re.split(r"[\s_\-/:]+", summary.lower())
        inferred: dict[str, str] = {}  # lower(tag) -> canonical tag
        for token in tokens:
            tag = _TOPIC_KEYWORD_MAP.get(token)
            if tag and tag.lower() not in existing_lower and tag.lower() not in inferred:
                inferred[tag.lower()] = tag
                if len(inferred) >= _TOPIC_TAG_MAX:
                    break
        return list(inferred.values())
    except Exception:  # justified: fail-open, tag inference is best-effort enrichment
        return []


def _entries_path(trw_dir: Path) -> Path:
    """Return the canonical entries directory path for a .trw directory."""
    config = get_config()
    return trw_dir / config.learnings_dir / config.entries_dir


def _iter_entry_files(
    entries_dir: Path,
    *,
    sorted_order: bool = False,
) -> Iterator[tuple[Path, dict[str, object]]]:
    """Yield (file_path, data) for each valid YAML entry, skipping index.yaml.

    Delegates path iteration to ``_helpers.iter_yaml_entry_files`` (which
    always yields in sorted order) and adds YAML parsing on top.
    Silently skips files that fail to parse or have unexpected types.
    """
    reader = FileStateReader()
    files = iter_yaml_entry_files(entries_dir)
    for entry_file in files:
        if not sorted_order and entry_file.name == "index.yaml":
            continue
        try:
            data = reader.read_yaml(entry_file)
            yield entry_file, data
        except (StateError, ValueError, TypeError):
            continue


def _get_event_type(event: dict[str, object]) -> str:
    """Extract the event type string from an event dict."""
    return str(event.get("event", ""))


def is_error_event(event: dict[str, object]) -> bool:
    """Check if an event represents an error.

    Args:
        event: Event dictionary from events.jsonl.

    Returns:
        True if the event indicates an error or failure.
    """
    event_type = _get_event_type(event).lower()
    return any(kw in event_type for kw in _ERROR_KEYWORDS)


def is_success_event(event: dict[str, object]) -> bool:
    """Check if an event represents a successful outcome.

    Matches events whose type contains success-related keywords such as
    "complete", "success", "pass", "done", "finish", "approved", etc.

    Args:
        event: Event dictionary from events.jsonl.

    Returns:
        True if the event indicates a successful outcome.
    """
    event_type = _get_event_type(event).lower()
    return any(kw in event_type for kw in _SUCCESS_KEYWORDS)


def find_entry_by_id(
    entries_dir: Path,
    learning_id: str,
) -> tuple[Path, dict[str, object]] | None:
    """Find a learning entry file by scanning for a matching ID.

    Args:
        entries_dir: Path to the entries directory.
        learning_id: ID to search for.

    Returns:
        Tuple of (file_path, entry_data) if found, None otherwise.
    """
    reader = FileStateReader()
    for entry_file in iter_yaml_entry_files(entries_dir):
        try:
            data = reader.read_yaml(entry_file)
            if data.get("id") == learning_id:
                return entry_file, data
        except (StateError, ValueError, TypeError):  # per-item error handling: skip unparseable entry files  # noqa: PERF203
            continue
    return None


def generate_learning_id() -> str:
    """Generate a unique compact learning entry ID (PRD-CORE-110).

    Uses base-62 compact IDs from trw-memory for shorter, more readable IDs.
    Falls back to secrets.token_hex(4) if trw-memory is unavailable.

    Returns:
        String ID in format 'L-{compact_id}'.
    """
    try:
        from trw_memory.utils import generate_compact_id

        return generate_compact_id(prefix="L")
    except (ImportError, RuntimeError):
        import secrets

        return f"L-{secrets.token_hex(4)}"
