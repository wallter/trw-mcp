"""Programmatic artifact scanner for knowledge requirements extraction.

PRD-CORE-106-FR02: Scans PRDs, execution plans, and sprint documents
for structured knowledge requirements and inline learning references.
Uses structured YAML extraction -- no LLM needed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from ruamel.yaml import YAML

_logger = structlog.get_logger(__name__)

# Regex patterns for inline references
_LEARNING_ID_PATTERN = re.compile(r"\bL-[a-zA-Z0-9]{4,8}\b")
_PRD_REF_PATTERN = re.compile(r"\bPRD-[A-Z]+-\d+\b")
_FENCED_CODE_BLOCK = re.compile(r"```[\s\S]*?```", re.MULTILINE)

# Top-level flat keys in knowledge_requirements (non-phase keys)
_FLAT_KEYS = frozenset({"domains", "learning_ids", "checks", "research_notes"})

# Known phase names for phase-keyed requirements
_PHASE_NAMES = frozenset({"research", "plan", "implement", "validate", "review", "deliver"})


@dataclass
class KnowledgeRequirements:
    """Extracted knowledge requirements from artifacts."""

    learning_ids: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)
    checks: list[str] = field(default_factory=list)
    research_notes: list[str] = field(default_factory=list)
    prd_references: set[str] = field(default_factory=set)
    phase_requirements: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def merge(self, other: KnowledgeRequirements) -> None:
        """Merge another KnowledgeRequirements into this one (union/concat)."""
        self.learning_ids |= other.learning_ids
        self.domains |= other.domains
        self.checks.extend(other.checks)
        self.research_notes.extend(other.research_notes)
        self.prd_references |= other.prd_references
        for phase, reqs in other.phase_requirements.items():
            if phase not in self.phase_requirements:
                self.phase_requirements[phase] = {}
            for key, vals in reqs.items():
                self.phase_requirements[phase].setdefault(key, []).extend(vals)


def _extract_yaml_block(content: str) -> str | None:
    """Extract the indented YAML block following ``knowledge_requirements:``.

    Finds the first line starting with ``knowledge_requirements:`` and collects
    all subsequent indented lines (indent > 0) until a non-indented, non-empty
    line is encountered.

    Returns the extracted block as a string (without the header line), or None
    if no ``knowledge_requirements:`` line is found.
    """
    lines = content.splitlines()
    start_idx: int | None = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "knowledge_requirements:" or stripped.startswith("knowledge_requirements:"):
            start_idx = i
            break

    if start_idx is None:
        return None

    # Collect indented lines after the header
    block_lines: list[str] = []
    for line in lines[start_idx + 1 :]:
        # Empty lines within the block are preserved
        if line.strip() == "":
            block_lines.append("")
            continue
        # Non-indented, non-empty line => end of block
        if not line[0].isspace():
            break
        block_lines.append(line)

    if not block_lines:
        return None

    return "\n".join(block_lines)


def _populate_from_yaml(
    data: dict[str, Any],
    result: KnowledgeRequirements,
) -> None:
    """Populate a KnowledgeRequirements from parsed YAML dict.

    Handles both flat keys (domains, learning_ids, checks, research_notes)
    and phase-keyed sub-dicts (implement, validate, etc.).
    """
    # Flat keys
    if isinstance(data.get("domains"), list):
        for d in data["domains"]:
            if isinstance(d, str):
                result.domains.add(d)

    if isinstance(data.get("learning_ids"), list):
        for lid in data["learning_ids"]:
            if isinstance(lid, str):
                result.learning_ids.add(lid)

    if isinstance(data.get("checks"), list):
        for c in data["checks"]:
            if isinstance(c, str):
                result.checks.append(c)

    if isinstance(data.get("research_notes"), list):
        for rn in data["research_notes"]:
            if isinstance(rn, str):
                result.research_notes.append(rn)

    # Phase-keyed sub-dicts
    for key, value in data.items():
        if key in _FLAT_KEYS:
            continue
        if key in _PHASE_NAMES and isinstance(value, dict):
            if key not in result.phase_requirements:
                result.phase_requirements[key] = {}
            for sub_key, sub_vals in value.items():
                if isinstance(sub_vals, list):
                    str_vals = [v for v in sub_vals if isinstance(v, str)]
                    result.phase_requirements[key].setdefault(sub_key, []).extend(
                        str_vals,
                    )


def scan_artifact(path: Path) -> KnowledgeRequirements:
    """Scan a single artifact file for knowledge requirements.

    1. Reads file content as text.
    2. Strips fenced code blocks (``...``) to avoid false matches.
    3. Extracts inline learning IDs matching ``L-[a-zA-Z0-9]{4,8}``.
    4. Extracts inline PRD references matching ``PRD-[A-Z]+-\\d+``.
    5. Searches for ``knowledge_requirements:`` YAML block, parses with
       ``YAML(typ="safe")``, and extracts domains, learning_ids, checks,
       research_notes per phase.

    Args:
        path: Path to the artifact file.

    Returns:
        KnowledgeRequirements with extracted data.
    """
    result = KnowledgeRequirements()

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _logger.warning("artifact_read_failed", path=str(path), error=str(exc))
        return result

    if not content.strip():
        return result

    # Strip fenced code blocks before extracting inline references
    stripped_content = _FENCED_CODE_BLOCK.sub("", content)

    # Extract inline learning IDs
    result.learning_ids = set(_LEARNING_ID_PATTERN.findall(stripped_content))

    # Extract inline PRD references
    result.prd_references = set(_PRD_REF_PATTERN.findall(stripped_content))

    # Extract structured knowledge_requirements YAML block
    yaml_block = _extract_yaml_block(stripped_content)
    if yaml_block is not None:
        try:
            yaml = YAML(typ="safe")
            parsed = yaml.load(yaml_block)
            if isinstance(parsed, dict):
                _populate_from_yaml(parsed, result)
        except Exception as exc:  # justified: fail-open on malformed YAML
            _logger.warning(
                "artifact_yaml_parse_failed",
                path=str(path),
                error=str(exc),
            )

    _logger.debug(
        "artifact_scanned",
        path=str(path),
        learning_ids=len(result.learning_ids),
        prd_refs=len(result.prd_references),
        domains=len(result.domains),
    )
    return result


def scan_artifacts(paths: Sequence[str | Path]) -> KnowledgeRequirements:
    """Scan multiple artifact files and merge their knowledge requirements.

    Skips missing files with a WARNING log. Returns the merged union
    of all successfully scanned artifacts.

    Args:
        paths: List of file paths (str or Path) to scan.

    Returns:
        Merged KnowledgeRequirements from all scanned artifacts.
    """
    merged = KnowledgeRequirements()

    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            _logger.warning("artifact_missing", path=str(path))
            continue
        single = scan_artifact(path)
        merged.merge(single)

    _logger.info(
        "artifacts_scan_complete",
        count=len(paths),
        total_learning_ids=len(merged.learning_ids),
        total_domains=len(merged.domains),
        total_prd_refs=len(merged.prd_references),
    )
    return merged
