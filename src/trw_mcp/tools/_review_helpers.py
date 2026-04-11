"""Extracted helpers for trw_review tool — finding validation, mode handlers.

Keeps the tool closure in review.py focused on dispatch while
business logic lives in testable pure-ish functions.

Shared constants and low-level helpers (_normalize_severity, _compute_verdict,
_get_git_diff, _persist_review_artifact, etc.) live here as the canonical
definitions; review.py re-exports them so existing test patches at
``trw_mcp.tools.review.*`` continue to resolve.

Mode handler functions are extracted to sub-modules for module-size compliance:
- ``_review_auto.py``: handle_auto_mode, handle_cross_model_mode
- ``_review_manual.py``: handle_manual_mode, handle_reconcile_mode, validate_manual_findings,
  count_by_severity, and reconciliation helpers
- ``_review_multi.py``: _run_multi_reviewer_analysis
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared constants and low-level helpers (canonical definitions)
# ---------------------------------------------------------------------------

# Reviewer roles for multi-agent review (QUAL-027)
REVIEWER_ROLES: tuple[str, ...] = (
    "correctness",
    "security",
    "test-quality",
    "performance",
    "style",
    "spec-compliance",
)


def _get_git_diff() -> str:
    """Get git diff of HEAD, returning empty string on any error."""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],  # noqa: S607 — git is a well-known VCS tool; all args are static literals, no user input
            capture_output=True,
            text=True,
            timeout=30,
        )
        logger.debug("review_git_diff", length=len(result.stdout))
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _normalize_severity(severity: str) -> str:
    """Map external severity labels to internal severity levels."""
    severity_lower = severity.lower().strip()
    if severity_lower in ("error", "critical", "high"):
        return "critical"
    if severity_lower in ("warning", "medium"):
        return "warning"
    return "info"


def _invoke_cross_model_review(
    diff: str,
    config: TRWConfig,
) -> list[dict[str, str]]:
    """Invoke cross-model review via external provider.

    This function is the integration point for cross-model review.
    It attempts to call an external code-review service. Since the
    MCP server cannot synchronously call another MCP server, this
    returns an empty list with a preparation note.

    Args:
        diff: The git diff text to review.
        config: TRWConfig instance with cross_model_* fields.

    Returns:
        List of normalized finding dicts (empty until provider is configured).
    """
    if not diff:
        return []

    # Integration point: when code-review-mcp or another provider
    # is configured, this function will route the diff to it.
    # For now, return empty — the cross_model_skipped flag in the
    # caller communicates that no external review was performed.
    return []


def _compute_verdict(findings: list[dict[str, str]]) -> str:
    """Compute review verdict from worst severity across findings."""
    critical_count = sum(1 for f in findings if f.get("severity") == "critical")
    warning_count = sum(1 for f in findings if f.get("severity") == "warning")
    logger.debug(
        "review_findings_count",
        count=len(findings),
        critical=critical_count,
        warnings=warning_count,
    )

    if critical_count > 0:
        return "block"
    if warning_count > 0:
        return "warn"
    return "pass"


def _persist_review_artifact(
    resolved_run: Path | None,
    review_data: dict[str, object],
    event_fields: dict[str, object],
) -> str:
    """Write review.yaml and log review_complete event.

    Specific to manual/cross_model/auto review modes — writes to
    ``meta/review.yaml`` and logs event type ``review_complete``.
    Do NOT use for reconciliation (which writes ``reconciliation.yaml``
    with event type ``spec_reconciliation``).

    Args:
        resolved_run: Run directory path, or None if no run active.
        review_data: Full review data dict to write to review.yaml.
        event_fields: Fields to include in the review_complete event.

    Returns:
        Path string to review.yaml, or empty string if no run.
    """
    if resolved_run is None:
        return ""

    writer = FileStateWriter()
    reader = FileStateReader()
    events = FileEventLogger(writer)

    review_path = resolved_run / "meta" / "review.yaml"
    writer.write_yaml(review_path, review_data)

    events_path = resolved_run / "meta" / "events.jsonl"
    if events_path.parent.exists():
        prd_ids = _resolve_review_prd_ids(resolved_run, reader, event_fields)
        verdict = str(review_data.get("verdict", event_fields.get("verdict", ""))).upper()
        finding_categories = _extract_review_finding_categories(review_data)
        for prd_id in prd_ids:
            events.log_event(
                events_path,
                "audit_cycle_complete",
                {
                    "prd_id": prd_id,
                    "verdict": verdict,
                    "finding_categories": finding_categories,
                },
            )
        events.log_event(events_path, "review_complete", event_fields)

    return str(review_path)


def _resolve_review_prd_ids(
    resolved_run: Path,
    reader: FileStateReader,
    event_fields: dict[str, object],
) -> list[str]:
    """Resolve PRD IDs for a review event from explicit fields or run scope."""
    raw_prd_ids = event_fields.get("prd_ids")
    if isinstance(raw_prd_ids, list):
        prd_ids = [str(prd_id) for prd_id in raw_prd_ids if str(prd_id)]
        if prd_ids:
            return prd_ids

    run_yaml_path = resolved_run / "meta" / "run.yaml"
    if not run_yaml_path.exists():
        return []

    run_data = reader.read_yaml(run_yaml_path)
    raw_scope = run_data.get("prd_scope", []) if isinstance(run_data, dict) else []
    if not isinstance(raw_scope, list):
        return []
    return [str(prd_id) for prd_id in raw_scope if str(prd_id)]


def _extract_review_finding_categories(review_data: dict[str, object]) -> list[str]:
    """Extract finding categories from persisted review data."""
    findings = review_data.get("findings", review_data.get("cross_model_findings"))
    if not isinstance(findings, list):
        return []
    return [
        str(finding.get("category", ""))
        for finding in findings
        if isinstance(finding, dict) and str(finding.get("category", ""))
    ]


# ---------------------------------------------------------------------------
# Lazy re-exports from sub-modules (preserves existing import paths)
# ---------------------------------------------------------------------------

# Mapping of re-exported names to their source sub-module
_REEXPORT_MAP: dict[str, str] = {
    # _review_auto.py
    "handle_auto_mode": "trw_mcp.tools._review_auto",
    "handle_cross_model_mode": "trw_mcp.tools._review_auto",
    # _review_manual.py
    "handle_manual_mode": "trw_mcp.tools._review_manual",
    "handle_reconcile_mode": "trw_mcp.tools._review_manual",
    "validate_manual_findings": "trw_mcp.tools._review_manual",
    "count_by_severity": "trw_mcp.tools._review_manual",
    "_extract_section": "trw_mcp.tools._review_manual",
    "_extract_identifiers": "trw_mcp.tools._review_manual",
    "_added_lines_only": "trw_mcp.tools._review_manual",
    "_extract_fr_mismatches": "trw_mcp.tools._review_manual",
    "_count_frs_in_prd": "trw_mcp.tools._review_manual",
    # _review_multi.py
    "_run_multi_reviewer_analysis": "trw_mcp.tools._review_multi",
}


def __getattr__(name: str) -> object:
    """Lazy re-export of mode handler functions from sub-modules.

    This avoids circular imports: sub-modules import shared helpers from
    this module, and this module re-exports mode handlers from sub-modules.
    The deferred ``__getattr__`` approach ensures sub-modules are only
    loaded when a re-exported name is actually accessed, by which time
    this module is fully initialized.
    """
    module_path = _REEXPORT_MAP.get(name)
    if module_path is not None:
        import importlib

        mod = importlib.import_module(module_path)
        value = getattr(mod, name)
        # Cache on module dict for subsequent fast access
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
