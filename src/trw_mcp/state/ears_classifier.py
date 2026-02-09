"""EARS (Easy Approach to Requirements Syntax) pattern classifier.

Implements PRD-CORE-008-FR05. Classifies functional requirements into
5 EARS patterns using keyword and regex matching:
1. Event-Driven: "When [trigger]" → "shall/must/will"
2. State-Driven: "While/During/In [condition]" → "shall/must/will"
3. Unwanted-Behavior: "If [error/failure]" → "shall/must/will" + recovery
4. Optional-Feature: "Where [feature/option]" → "shall/must/will"
5. Ubiquitous: "shall/must/will" without a trigger keyword

All functions are pure — no side effects, no file I/O.
"""

from __future__ import annotations

import re
from enum import Enum

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import DimensionScore


class EARSPattern(str, Enum):
    """EARS pattern classification."""

    EVENT_DRIVEN = "event_driven"
    STATE_DRIVEN = "state_driven"
    UNWANTED_BEHAVIOR = "unwanted_behavior"
    OPTIONAL_FEATURE = "optional_feature"
    UBIQUITOUS = "ubiquitous"
    UNCLASSIFIED = "unclassified"


# Compiled patterns at module load (PRD-CORE-008-NFR01)
_EVENT_RE = re.compile(
    r"\b(?:when|on)\s+.{3,}?\b(?:shall|must|will)\b",
    re.IGNORECASE,
)
_STATE_RE = re.compile(
    r"\b(?:while|during|in)\s+.{3,}?\b(?:shall|must|will)\b",
    re.IGNORECASE,
)
_UNWANTED_RE = re.compile(
    r"\bif\s+.{3,}?(?:error|fail|exception|timeout|invalid|unavailable|corrupt)"
    r".{0,}?\b(?:shall|must|will)\b",
    re.IGNORECASE,
)
_OPTIONAL_RE = re.compile(
    r"\b(?:where)\s+.{3,}?\b(?:shall|must|will)\b",
    re.IGNORECASE,
)
_UBIQUITOUS_RE = re.compile(
    r"\b(?:shall|must|will)\b",
    re.IGNORECASE,
)


def classify_requirement(text: str) -> dict[str, object]:
    """Classify a single requirement into an EARS pattern.

    Patterns are checked in priority order (most specific first):
    Unwanted > Event > State > Optional > Ubiquitous > Unclassified.

    Args:
        text: Requirement text to classify.

    Returns:
        Dict with pattern, confidence, and trigger_text.
    """
    text_stripped = text.strip()
    if not text_stripped:
        return {
            "pattern": EARSPattern.UNCLASSIFIED.value,
            "confidence": 0.0,
            "trigger_text": "",
        }

    # Check in order of specificity
    match = _UNWANTED_RE.search(text_stripped)
    if match:
        return {
            "pattern": EARSPattern.UNWANTED_BEHAVIOR.value,
            "confidence": 0.9,
            "trigger_text": match.group(0)[:80],
        }

    match = _EVENT_RE.search(text_stripped)
    if match:
        return {
            "pattern": EARSPattern.EVENT_DRIVEN.value,
            "confidence": 0.85,
            "trigger_text": match.group(0)[:80],
        }

    match = _STATE_RE.search(text_stripped)
    if match:
        return {
            "pattern": EARSPattern.STATE_DRIVEN.value,
            "confidence": 0.85,
            "trigger_text": match.group(0)[:80],
        }

    match = _OPTIONAL_RE.search(text_stripped)
    if match:
        return {
            "pattern": EARSPattern.OPTIONAL_FEATURE.value,
            "confidence": 0.8,
            "trigger_text": match.group(0)[:80],
        }

    match = _UBIQUITOUS_RE.search(text_stripped)
    if match:
        return {
            "pattern": EARSPattern.UBIQUITOUS.value,
            "confidence": 0.7,
            "trigger_text": match.group(0)[:80],
        }

    return {
        "pattern": EARSPattern.UNCLASSIFIED.value,
        "confidence": 0.0,
        "trigger_text": "",
    }


def _extract_fr_blocks(content: str) -> list[str]:
    """Extract individual FR text blocks from PRD content.

    Looks for the Functional Requirements section and splits into
    individual requirements by ### headings or FR-prefixed IDs.

    Args:
        content: Full PRD markdown content.

    Returns:
        List of FR text blocks.
    """
    # Find FR section
    fr_re = re.compile(
        r"##\s+\d+\.\s+Functional Requirements\s*\n(.*?)(?=\n##\s+\d+\.|\Z)",
        re.DOTALL,
    )
    match = fr_re.search(content)
    if not match:
        return []

    fr_body = match.group(1)

    # Split by ### headings (individual FRs)
    blocks: list[str] = []
    heading_re = re.compile(r"^###\s+", re.MULTILINE)
    parts = heading_re.split(fr_body)

    for part in parts:
        cleaned = part.strip()
        if cleaned and len(cleaned) > 10:
            blocks.append(cleaned)

    return blocks


def classify_all_frs(content: str) -> list[dict[str, object]]:
    """Classify all functional requirements in a PRD.

    Args:
        content: Full PRD markdown content.

    Returns:
        List of classification dicts (one per FR).
    """
    blocks = _extract_fr_blocks(content)
    return [classify_requirement(block) for block in blocks]


def score_ears_coverage(
    content: str,
    config: TRWConfig | None = None,
) -> tuple[DimensionScore, list[dict[str, object]]]:
    """Score the EARS Coverage dimension.

    Coverage = classified_count / total_fr_count.
    Score = coverage * max_score.

    Args:
        content: Full PRD markdown content.
        config: Optional config for weight override.

    Returns:
        Tuple of (DimensionScore, list of classifications).
    """
    _config = config or TRWConfig()
    max_score = _config.validation_ears_weight

    classifications = classify_all_frs(content)

    if not classifications:
        return (
            DimensionScore(
                name="ears_coverage",
                score=0.0,
                max_score=max_score,
                details={"total_frs": 0, "classified": 0, "coverage": 0.0},
            ),
            [],
        )

    classified = sum(
        1 for c in classifications if c["pattern"] != EARSPattern.UNCLASSIFIED.value
    )
    total = len(classifications)
    coverage = classified / total

    score = coverage * max_score

    dim = DimensionScore(
        name="ears_coverage",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details={
            "total_frs": total,
            "classified": classified,
            "coverage": round(coverage, 4),
        },
    )

    return dim, classifications
