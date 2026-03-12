"""Recall search, filtering, and access tracking.

Extracted from tools/learning.py (PRD-FIX-010) to separate search/ranking
logic from tool orchestration.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger()


def search_patterns(
    patterns_dir: Path,
    query_tokens: list[str],
    reader: FileStateReader,
) -> list[dict[str, object]]:
    """Search pattern entries matching query tokens.

    Args:
        patterns_dir: Path to the patterns/ directory.
        query_tokens: Lowercased query tokens (empty list for wildcard).
        reader: File state reader instance.

    Returns:
        List of matching pattern dictionaries.
    """
    matching: list[dict[str, object]] = []
    if not patterns_dir.exists():
        return matching

    for pattern_file in sorted(patterns_dir.glob("*.yaml")):
        if pattern_file.name == "index.yaml":
            continue
        try:
            data = reader.read_yaml(pattern_file)
            name = str(data.get("name", "")).lower()
            desc = str(data.get("description", "")).lower()
            text = name + " " + desc
            if all(token in text for token in query_tokens):
                matching.append(data)
        except (StateError, ValueError, TypeError):
            continue

    return matching


def collect_context(
    trw_dir: Path,
    context_dir_name: str,
    reader: FileStateReader,
) -> dict[str, object]:
    """Collect architecture and conventions context data.

    Args:
        trw_dir: Path to .trw directory.
        context_dir_name: Name of the context subdirectory.
        reader: File state reader instance.

    Returns:
        Dict with optional 'architecture' and 'conventions' keys.
    """
    context: dict[str, object] = {}
    context_dir = trw_dir / context_dir_name
    arch_path = context_dir / "architecture.yaml"
    conv_path = context_dir / "conventions.yaml"
    if reader.exists(arch_path):
        context["architecture"] = reader.read_yaml(arch_path)
    if reader.exists(conv_path):
        context["conventions"] = reader.read_yaml(conv_path)
    return context
