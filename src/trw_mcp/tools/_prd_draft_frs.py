"""Research-to-FR drafting logic (PRD-CORE-133).

Deterministic extraction + template-based formatting. No LLM dependency.
Parses research reports (Markdown or JSON) to extract key symbols and file
paths, then produces AARE-F compliant FR blocks.
"""

from __future__ import annotations

import json
import re
from typing import TypedDict

import structlog

_logger = structlog.get_logger(__name__)


class FRBlock(TypedDict):
    """A single AARE-F Functional Requirement block."""

    id: str
    priority: str
    status: str
    description: str
    acceptance: str
    confidence: str


class DraftFRsResult(TypedDict):
    """Return type for ``draft_frs_from_research``."""

    functional_requirements: list[FRBlock]
    key_symbols: list[str]
    relevant_locations: list[str]
    fr_count: int


# ---------------------------------------------------------------------------
# Symbol and path extraction
# ---------------------------------------------------------------------------

# Match backtick-wrapped code symbols like `ClassName`, `module.func()`, `CONSTANT`
_BACKTICK_SYMBOL_RE = re.compile(r"`([A-Z]\w+(?:\.\w+)*(?:\(\))?)`")

# Match PascalCase or UPPER_CASE identifiers that look like class/constant names
_PASCAL_OR_UPPER_RE = re.compile(r"\b([A-Z][a-z]\w{2,}(?:\.\w+)*)\b")

# Match file paths (must contain / and end with common extensions)
_FILE_PATH_RE = re.compile(
    r"(?:^|\s|[`\-*])"  # preceded by whitespace, backtick, dash, star, or start
    r"((?:[\w.]+/)+[\w]+\.(?:py|ts|tsx|js|jsx|rs|go|java|yaml|yml|json|toml|md|sql|sh))"
    r"(?:\s|$|[`),]|:)",  # followed by whitespace, end, backtick, paren, comma, colon
    re.MULTILINE,
)

# Common English words that look like PascalCase but aren't code symbols.
# Module-level frozenset for O(1) lookup and single allocation.
_COMMON_WORDS: frozenset[str] = frozenset({
    "The", "This", "That", "When", "Where", "What", "How", "Why",
    "Note", "See", "Also", "Must", "Should", "Could", "Would",
    "Phase", "Step", "Section", "Table", "Summary", "Analysis",
    "Research", "Report", "Background", "Context", "Description",
    "Priority", "Status", "Finding", "Findings", "Key", "Recommendation",
    "Recommendations", "Changes", "Current", "Existing", "Returns",
    "Add", "Remove", "Update", "Create", "Delete", "Fix", "Replace",
    "Configure", "Implement", "Test", "Verify", "Check", "Run",
    "Build", "Deploy", "Install", "Start", "Stop", "Read", "Write",
    "Load", "Save", "Send", "Fetch", "Get", "Set", "Put", "Post",
    "All", "Any", "Each", "Every", "Some", "None", "True", "False",
})


def _extract_symbols_from_text(text: str) -> list[str]:
    """Extract code symbols from text content."""
    symbols: set[str] = set()

    # Backtick-wrapped symbols (highest signal)
    for match in _BACKTICK_SYMBOL_RE.finditer(text):
        symbols.add(match.group(1))

    # PascalCase identifiers (lower signal, skip common words)
    for match in _PASCAL_OR_UPPER_RE.finditer(text):
        name = match.group(1)
        if name not in _COMMON_WORDS and len(name) > 2:
            symbols.add(name)

    return sorted(symbols)


def _extract_paths_from_text(text: str) -> list[str]:
    """Extract file paths from text content."""
    paths: set[str] = set()
    for match in _FILE_PATH_RE.finditer(text):
        paths.add(match.group(1))
    return sorted(paths)


def _extract_from_json(data: dict[str, object]) -> tuple[list[str], list[str], list[str]]:
    """Extract symbols, paths, and recommendations from JSON research."""
    symbols: list[str] = []
    locations: list[str] = []
    recommendations: list[str] = []

    # Direct fields
    if "key_symbols" in data and isinstance(data["key_symbols"], list):
        symbols = [str(s) for s in data["key_symbols"]]
    if "relevant_locations" in data and isinstance(data["relevant_locations"], list):
        locations = [str(loc) for loc in data["relevant_locations"]]
    if "recommendations" in data and isinstance(data["recommendations"], list):
        recommendations = [str(r) for r in data["recommendations"]]

    # Also extract from text fields
    for field in ("summary", "title", "description", "content"):
        val = data.get(field)
        if isinstance(val, str):
            symbols.extend(_extract_symbols_from_text(val))
            locations.extend(_extract_paths_from_text(val))

    return (
        sorted(set(symbols)),
        sorted(set(locations)),
        recommendations,
    )


# ---------------------------------------------------------------------------
# FR block generation
# ---------------------------------------------------------------------------


def _build_acceptance(
    description: str,
    symbols: list[str],
    locations: list[str],
) -> str:
    """Build acceptance criteria with backtick-wrapped technical grounding."""
    parts: list[str] = [description]

    if symbols:
        symbol_refs = ", ".join(f"`{s}`" for s in symbols[:5])
        parts.append(f"Key symbols: {symbol_refs}.")

    if locations:
        loc_refs = ", ".join(f"`{loc}`" for loc in locations[:3])
        parts.append(f"Relevant files: {loc_refs}.")

    return " ".join(parts)


def _generate_frs(
    recommendations: list[str],
    symbols: list[str],
    locations: list[str],
    extra_context: str,
) -> list[FRBlock]:
    """Generate AARE-F FR blocks from extracted data."""
    frs: list[FRBlock] = []

    if not recommendations:
        # If no explicit recommendations, create a single FR from the research
        desc = extra_context if extra_context else "Implement the changes described in the research report."
        frs.append({
            "id": "FR01",
            "priority": "Must Have",
            "status": "active",
            "description": desc,
            "acceptance": _build_acceptance(
                "Implementation matches the research findings.",
                symbols,
                locations,
            ),
            "confidence": "0.8",
        })
        return frs

    for i, rec in enumerate(recommendations, 1):
        # Determine priority based on position (first recommendations are higher priority)
        priority = "Must Have" if i <= 2 else "Should Have"

        # Include extra_context in first FR if provided
        desc = rec
        if extra_context and i == 1:
            desc = f"{rec} Additional context: {extra_context}"

        # Distribute symbols and locations across FRs
        fr_symbols = symbols[: max(3, len(symbols) // len(recommendations) + 1)]
        fr_locations = locations[: max(2, len(locations) // len(recommendations) + 1)]

        frs.append({
            "id": f"FR{i:02d}",
            "priority": priority,
            "status": "active",
            "description": desc,
            "acceptance": _build_acceptance(
                f"Verified: {rec.rstrip('.')}.",
                fr_symbols,
                fr_locations,
            ),
            "confidence": str(round(0.9 - (i - 1) * 0.05, 2)),
        })

    return frs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def draft_frs_from_research(
    research_report: str,
    extra_context: str = "",
) -> DraftFRsResult:
    """Convert a research report into AARE-F FR blocks.

    Args:
        research_report: Markdown or JSON research content.
        extra_context: Additional constraints or requirements.

    Returns:
        Dictionary with functional_requirements, key_symbols,
        relevant_locations, and fr_count.
    """
    symbols: list[str] = []
    locations: list[str] = []
    recommendations: list[str] = []

    # Try parsing as JSON first
    try:
        data = json.loads(research_report)
        if isinstance(data, dict):
            symbols, locations, recommendations = _extract_from_json(data)
    except (json.JSONDecodeError, ValueError):
        pass

    # Always also extract from raw text (catches markdown and supplements JSON)
    text_symbols = _extract_symbols_from_text(research_report)
    text_paths = _extract_paths_from_text(research_report)

    # Merge (dedup)
    symbols = sorted(set(symbols) | set(text_symbols))
    locations = sorted(set(locations) | set(text_paths))

    # Extract recommendations from markdown if not from JSON
    if not recommendations:
        recommendations = _extract_recommendations_from_markdown(research_report)

    # Also parse extra_context for additional signals
    if extra_context:
        symbols = sorted(set(symbols) | set(_extract_symbols_from_text(extra_context)))
        locations = sorted(set(locations) | set(_extract_paths_from_text(extra_context)))
        # Extract actionable items from extra context
        for line in extra_context.split("."):
            line = line.strip()
            if line and len(line) > 10:
                recommendations.append(line)

    frs = _generate_frs(recommendations, symbols, locations, extra_context)

    _logger.info(
        "draft_frs_complete",
        fr_count=len(frs),
        symbol_count=len(symbols),
        location_count=len(locations),
        recommendation_count=len(recommendations),
    )

    return DraftFRsResult(
        functional_requirements=frs,
        key_symbols=symbols,
        relevant_locations=locations,
        fr_count=len(frs),
    )


def _extract_recommendations_from_markdown(text: str) -> list[str]:
    """Extract actionable recommendations from markdown text.

    Looks for numbered lists, bullet points under "Recommendations"/"Changes"
    headers, and imperative sentences.
    """
    recs: list[str] = []

    # Find recommendation sections
    in_rec_section = False
    for line in text.split("\n"):
        stripped = line.strip()

        # Check for recommendation-like headers
        lower = stripped.lower()
        if any(
            kw in lower
            for kw in ("recommendation", "changes needed", "action item", "next step")
        ):
            in_rec_section = True
            continue

        # Check for other headers (exit rec section)
        if stripped.startswith("#") and in_rec_section:
            in_rec_section = False
            continue

        # Extract numbered or bulleted items
        if in_rec_section and stripped:
            # Remove bullet/number prefix
            cleaned = re.sub(r"^(?:\d+\.\s*|\-\s*|\*\s*)", "", stripped)
            if cleaned and len(cleaned) > 10:
                recs.append(cleaned)

    # If no explicit recommendation section, look for numbered lists anywhere
    if not recs:
        for match in re.finditer(r"^\s*\d+\.\s+(.{15,})", text, re.MULTILINE):
            item = match.group(1).strip()
            # Filter out non-actionable items
            if any(
                kw in item.lower()
                for kw in ("add", "create", "implement", "update", "remove", "fix", "replace", "configure")
            ):
                recs.append(item)

    return recs
