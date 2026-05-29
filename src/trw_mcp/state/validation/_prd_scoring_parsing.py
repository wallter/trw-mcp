"""PRD scoring — markdown parsing + content-density + ambiguity helpers.

Belongs to the ``_prd_scoring.py`` facade. Re-exported there for back-compat.

6 helpers + the regex/constant cluster they depend on:

- ``_HEADING_RE`` / ``_SUBHEADING_RE`` / ``_PLACEHOLDER_RE`` / ``_VAGUE_TERMS_RE``
  / ``_REQUIREMENT_LINE_RE`` — compiled regexes shared across scorers.
- ``_EXPECTED_SECTION_NAMES`` / ``_HIGH_WEIGHT_SECTIONS`` / ``_SECTION_WEIGHTS``
  — section-vocabulary + density-weight tables.
- ``_REQUIRED_SUBSECTIONS_BY_VARIANT`` — per-template subsection requirements.
- ``_get_section_weights``, ``_compute_ambiguity_rate``,
  ``_parse_section_content``, ``_extract_subheadings``,
  ``_is_substantive_line``, ``_validation_profile``.

Extracted as DIST-243 batch 58.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

# Section heading pattern: ## N. Title
_HEADING_RE = re.compile(r"^##\s+\d+\.\s+(.+)$", re.MULTILINE)

# Placeholder patterns for content density (common template defaults)
_PLACEHOLDER_RE = re.compile(
    r"^\s*<!--.*?-->\s*$"
    r"|^\s*\{[^}]+\}\s*$"
    r"|^\s*\[.*TODO.*\]\s*$",
    re.IGNORECASE,
)

# Section headings expected in an AARE-F compliant PRD
_EXPECTED_SECTION_NAMES: list[str] = [
    "Problem Statement",
    "Goals & Non-Goals",
    "User Stories",
    "Functional Requirements",
    "Non-Functional Requirements",
    "Technical Approach",
    "Test Strategy",
    "Rollout Plan",
    "Success Metrics",
    "Dependencies & Risks",
    "Open Questions",
    "Traceability Matrix",
]

# Sections with higher weight in density scoring
_HIGH_WEIGHT_SECTIONS: dict[str, float] = {
    "Problem Statement": 2.0,
    "Functional Requirements": 2.0,
    "Traceability Matrix": 1.5,
}

# Section weights used by external consumers
_SECTION_WEIGHTS: dict[str, float] = _HIGH_WEIGHT_SECTIONS

# Pre-compiled regexes for ambiguity rate computation (FR02 -- PRD-FIX-054).
# Word-boundary matching avoids false positives on substrings.
_VAGUE_TERMS_RE = re.compile(
    r"\b(?:might|possibly|approximately|as needed|if possible|as appropriate)\b"
    r"|should consider"
    r"|etc\."
    r"|and/or",
    re.IGNORECASE,
)

# Lines that qualify as requirement-like statements
_REQUIREMENT_LINE_RE = re.compile(
    r"FR\d+|NFR\d+|- \[ \]|\bWhen\b|\bWhile\b|\bIf\b|\bWhere\b",
)

_SUBHEADING_RE = re.compile(r"^###\s+(.+)$", re.MULTILINE)

_REQUIRED_SUBSECTIONS_BY_VARIANT: dict[str, list[str]] = {
    "feature": [
        "Primary Control Points",
        "Behavior Switch Matrix",
        "Unit Tests",
        "Integration Tests",
        "Acceptance Tests",
        "Regression Tests",
        "Negative / Fallback Tests",
        "Completion Evidence (Definition of Done)",
        "Migration / Backward Compatibility",
    ],
    "infrastructure": [
        "Primary Control Points",
        "Behavior Switch Matrix",
        "Unit Tests",
        "Integration Tests",
        "Acceptance Tests",
        "Regression Tests",
        "Negative / Fallback Tests",
        "Completion Evidence (Definition of Done)",
        "Migration / Backward Compatibility",
    ],
    "fix": [
        "Root Cause",
        "Contributing Factors",
        "Fix Verification",
        "Regression Tests",
        "Negative / Fallback Tests",
        "Completion Evidence (Definition of Done)",
        "Migration / Backward Compatibility",
    ],
    "research": [
        "Approach",
        "Data Sources",
        "Evaluation Criteria",
    ],
}


def _get_section_weights(config: TRWConfig) -> dict[str, float]:
    """Build per-section weight map from TRWConfig (PRD-CORE-080-FR04)."""
    return {
        "Problem Statement": config.density_weight_problem_statement,
        "Functional Requirements": config.density_weight_functional_requirements,
        "Traceability Matrix": config.density_weight_traceability_matrix,
    }


def _compute_ambiguity_rate(content: str) -> float:
    """Compute ambiguity rate: vague-term count / requirement-statement count."""
    vague_count = len(_VAGUE_TERMS_RE.findall(content))
    req_lines = [line for line in content.splitlines() if _REQUIREMENT_LINE_RE.search(line)]
    total_req = len(req_lines)
    if total_req == 0:
        return 0.0
    return vague_count / total_req


def _parse_section_content(content: str) -> list[tuple[str, str]]:
    """Split PRD content into (section_name, section_body) pairs."""
    from trw_mcp.state.prd_utils import _FRONTMATTER_RE

    fm_match = _FRONTMATTER_RE.match(content)
    body = content[fm_match.end() :] if fm_match else content

    sections: list[tuple[str, str]] = []
    matches = list(_HEADING_RE.finditer(body))
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append((name, body[start:end]))
    return sections


def _extract_subheadings(content: str) -> set[str]:
    """Return all level-3 markdown subheading titles present in the PRD body."""
    return {match.group(1).strip() for match in _SUBHEADING_RE.finditer(content)}


def _is_substantive_line(line: str) -> bool:
    """Check if a line is substantive (not blank, comment, heading, or placeholder)."""
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return False
    if _PLACEHOLDER_RE.match(line):
        return False
    if stripped.startswith("<!--") and stripped.endswith("-->"):
        return False
    if re.match(r"^\s*\|[\s\-:|]+\|\s*$", line):
        return False
    return not re.match(r"^\s*---\s*$", line)


def _validation_profile(frontmatter: dict[str, object]) -> str:
    """Return the explicit PRD validation profile, if one is declared."""
    nested = frontmatter.get("prd")
    nested_profile = nested.get("validation_profile") if isinstance(nested, dict) else None
    profile = frontmatter.get("validation_profile", nested_profile)
    return str(profile or "").strip().lower()
