"""Category-specific PRD template variants (PRD-CORE-080-FR01).

Defines the required section headings per PRD category variant so that
the structural completeness scorer evaluates each PRD against its
appropriate section list instead of the hardcoded 12-section Feature list.
"""

from __future__ import annotations

from typing import Final

# Category → required section headings
# Counts: feature=12, fix=8, infrastructure=9, research=7
TEMPLATE_VARIANTS: Final[dict[str, list[str]]] = {
    "feature": [  # CORE, QUAL
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
    ],
    "fix": [  # FIX
        "Problem Statement",
        "Root Cause Analysis",
        "Functional Requirements",
        "Non-Functional Requirements",
        "Test Strategy",
        "Rollback Plan",
        "Traceability Matrix",
        "Open Questions",
    ],
    "infrastructure": [  # INFRA, LOCAL
        "Problem Statement",
        "Goals & Non-Goals",
        "Functional Requirements",
        "Non-Functional Requirements",
        "Technical Approach",
        "Test Strategy",
        "Rollout Plan",
        "Traceability Matrix",
        "Open Questions",
    ],
    "research": [  # RESEARCH, EXPLR
        "Problem Statement",
        "Background & Prior Art",
        "Research Questions",
        "Methodology",
        "Findings",
        "Recommendations",
        "Open Questions",
    ],
}

# Maps each PRD category string → variant name
CATEGORY_TO_VARIANT: Final[dict[str, str]] = {
    "CORE": "feature",
    "QUAL": "feature",
    "FIX": "fix",
    "INFRA": "infrastructure",
    "LOCAL": "infrastructure",
    "RESEARCH": "research",
    "EXPLR": "research",
}


def get_variant_for_category(category: str) -> str:
    """Return the template variant name for a given PRD category.

    Defaults to ``"feature"`` for unknown or missing categories (backward
    compatible with legacy PRDs that omit the category field).

    Args:
        category: PRD category string (e.g. ``"FIX"``, ``"CORE"``).

    Returns:
        Variant name (``"feature"``, ``"fix"``, ``"infrastructure"``, or
        ``"research"``).
    """
    return CATEGORY_TO_VARIANT.get(category.upper() if category else "", "feature")


def get_required_sections(category: str) -> list[str]:
    """Return the required section headings for a given PRD category.

    Args:
        category: PRD category string (e.g. ``"FIX"``, ``"CORE"``).

    Returns:
        List of section heading strings expected for the category.
    """
    variant = get_variant_for_category(category)
    return list(TEMPLATE_VARIANTS[variant])
