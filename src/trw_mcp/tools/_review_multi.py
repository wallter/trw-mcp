# Parent facade: tools/_review_helpers.py
"""Multi-reviewer analysis handler.

Extracted from ``_review_helpers.py`` to keep the facade under the
500-line threshold.  All public names are re-exported from
``_review_helpers.py`` so existing import paths are preserved.

QUAL-027: Multi-agent parallel review -- confidence-scored findings.

Note: shared helpers are accessed via ``_helpers.<name>`` (module reference)
rather than direct name imports so that patches on
``trw_mcp.tools._review_helpers.*`` in tests correctly intercept calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.models.typed_dicts import (
    MultiReviewerAnalysisResult,
    ReviewFindingDict,
)
from trw_mcp.tools import _review_helpers as _helpers

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)


def _run_multi_reviewer_analysis(
    diff: str,
    config: TRWConfig,
) -> MultiReviewerAnalysisResult:
    """Run structured multi-perspective code review analysis.

    When called without pre-collected reviewer_findings, performs
    basic structural analysis only. The actual multi-agent spawning
    is handled client-side by the /trw-review-pr skill.

    Args:
        diff: The git diff text to analyze.
        config: TRWConfig instance with review_* fields.

    Returns:
        Dict with reviewer_roles_run, findings, and errors.
    """
    result: MultiReviewerAnalysisResult = {
        "reviewer_roles_run": list(_helpers.REVIEWER_ROLES),
        "reviewer_errors": [],
        "findings": [],
    }

    if not diff:
        return result

    # Basic structural analysis: detect obvious patterns in the diff.
    # Full multi-agent analysis is handled client-side via subagents.
    findings: list[ReviewFindingDict] = []

    # Check for common issues detectable via diff text analysis
    lines = diff.split("\n")
    for i, line in enumerate(lines):
        # Detect TODO/FIXME/HACK comments in added lines
        if line.startswith("+") and not line.startswith("+++"):
            stripped = line[1:].strip()
            for marker in ("TODO", "FIXME", "HACK", "XXX"):
                if marker in stripped.upper():
                    findings.append(
                        {
                            "reviewer_role": "style",
                            "confidence": 60,
                            "category": "placeholder",
                            "severity": "info",
                            "description": f"Placeholder comment detected: {stripped[:80]}",
                            "line": i + 1,
                        }
                    )
                    break

    result["findings"] = cast("list[dict[str, object]]", findings)
    return result
