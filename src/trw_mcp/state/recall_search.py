"""Recall search, filtering, and access tracking.

Extracted from tools/learning.py (PRD-FIX-010) to separate search/ranking
logic from tool orchestration.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger()


def search_entries(
    entries_dir: Path,
    query_tokens: list[str],
    reader: FileStateReader,
    *,
    tags: list[str] | None = None,
    min_impact: float = 0.0,
    status: str | None = None,
) -> tuple[list[dict[str, object]], list[Path]]:
    """Search learning entries matching query, tags, impact, and status filters.

    Uses hybrid search (BM25 + RRF) when query_tokens are provided and the
    retrieval module is available. Falls back to keyword scan when not.

    Args:
        entries_dir: Path to the learnings/entries/ directory.
        query_tokens: Lowercased query tokens (empty list for wildcard).
        reader: File state reader instance.
        tags: Optional tag filter — entry must have at least one matching tag.
        min_impact: Minimum impact score threshold.
        status: Optional status filter (e.g. 'active').

    Returns:
        Tuple of (matching_entries, matched_file_paths).
    """
    matching: list[dict[str, object]] = []
    matched_files: list[Path] = []

    if not entries_dir.exists():
        return matching, matched_files

    # --- Hybrid search path (CORE-041) ---
    if query_tokens:
        try:
            from trw_mcp.models.config import get_config
            from trw_mcp.state.retrieval import hybrid_search

            config = get_config()
            query = " ".join(query_tokens)
            hybrid_results = hybrid_search(query, entries_dir, reader, config=config)
            if hybrid_results:
                # Apply tag, impact, status filters to hybrid results
                for data in hybrid_results:
                    entry_tags = data.get("tags", [])
                    raw_impact = data.get("impact", 0.0)
                    entry_impact = float(str(raw_impact))
                    if entry_impact < min_impact:
                        continue
                    if status is not None:
                        entry_status = str(data.get("status", "active"))
                        if entry_status != status:
                            continue
                    if tags and isinstance(entry_tags, list) and not any(t in entry_tags for t in tags):
                        continue
                    matching.append(data)
                    entry_id = str(data.get("id", ""))
                    # Resolve file path for access tracking
                    if entry_id:
                        for fname in entries_dir.glob("*.yaml"):
                            if entry_id in fname.name:
                                matched_files.append(fname)
                                break
                        else:
                            matched_files.append(entries_dir / f"{entry_id}.yaml")

                logger.debug(
                    "recall_search_hybrid_complete",
                    query_tokens=query_tokens,
                    results=len(matching),
                )
                return matching, matched_files
        except Exception:
            pass  # Fall through to keyword search

    # --- Keyword scan fallback ---
    for entry_file in sorted(entries_dir.glob("*.yaml")):
        try:
            data = reader.read_yaml(entry_file)
            summary = str(data.get("summary", "")).lower()
            detail = str(data.get("detail", "")).lower()
            entry_tags = data.get("tags", [])
            raw_impact = data.get("impact", 0.0)
            entry_impact = float(str(raw_impact))

            # Check impact threshold
            if entry_impact < min_impact:
                continue

            # Check status filter
            if status is not None:
                entry_status = str(data.get("status", "active"))
                if entry_status != status:
                    continue

            # Check tag filter
            if tags and isinstance(entry_tags, list) and not any(t in entry_tags for t in tags):
                continue

            # Check query match — all tokens must appear in summary, detail, or tags
            # Expand hyphenated tags so "pydantic-v2" also matches query "pydantic"
            tag_tokens: list[str] = []
            if isinstance(entry_tags, list):
                for t in entry_tags:
                    tag_str = str(t).lower()
                    tag_tokens.append(tag_str)
                    if "-" in tag_str:
                        tag_tokens.extend(tag_str.split("-"))
            tag_text = " ".join(tag_tokens)
            text = summary + " " + detail + " " + tag_text
            if all(token in text for token in query_tokens):
                matching.append(data)
                matched_files.append(entry_file)
        except (StateError, ValueError, TypeError):
            continue

    logger.debug(
        "recall_search_complete",
        query_tokens=query_tokens,
        results=len(matching),
        scanned=len(list(entries_dir.glob("*.yaml"))) if entries_dir.exists() else 0,
    )
    return matching, matched_files


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


def update_access_tracking(
    matched_files: list[Path],
    reader: FileStateReader,
    writer: FileStateWriter,
) -> list[str]:
    """Increment access count and last_accessed_at for matched entries.

    Args:
        matched_files: Paths to matched learning entry YAML files.
        reader: File state reader instance.
        writer: File state writer instance.

    Returns:
        List of learning IDs that were successfully tracked.
    """
    matched_ids: list[str] = []
    today_iso = date.today().isoformat()

    for entry_file in matched_files:
        try:
            data = reader.read_yaml(entry_file)
            prev_count = int(str(data.get("access_count", 0)))
            data["access_count"] = prev_count + 1
            data["last_accessed_at"] = today_iso
            writer.write_yaml(entry_file, data)
            entry_id = str(data.get("id", ""))
            if entry_id:
                matched_ids.append(entry_id)
        except (StateError, ValueError, TypeError):
            continue

    return matched_ids


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
