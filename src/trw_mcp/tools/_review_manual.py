# Parent facade: tools/_review_helpers.py
"""Manual-mode and reconcile-mode review handlers.

Extracted from ``_review_helpers.py`` to keep the facade under the
500-line threshold.  All public names are re-exported from
``_review_helpers.py`` so existing import paths are preserved.

Note: shared helpers are accessed via ``_helpers.<name>`` (module reference)
rather than direct name imports so that ``patch("trw_mcp.tools._review_helpers._get_git_diff", ...)``
in tests correctly intercepts calls from this module.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.models.typed_dicts import (
    ManualReviewResult,
    ReconcileReviewResult,
    ReviewFindingDict,
)
from trw_mcp.state.persistence import FileEventLogger, FileStateWriter
from trw_mcp.tools import _review_helpers as _helpers

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Manual mode
# ---------------------------------------------------------------------------


def handle_manual_mode(
    raw_findings: list[dict[str, str]],
    resolved_run: Path | None,
    review_id: str,
    ts: str,
    prd_ids: list[str] | None = None,
) -> ManualReviewResult:
    """Handle the manual review mode -- validate findings, compute verdict, persist."""
    validated = validate_manual_findings(raw_findings)
    critical_count, warning_count, info_count = count_by_severity(validated)
    verdict = _helpers._compute_verdict(cast("list[dict[str, str]]", validated))

    result: ManualReviewResult = {
        "review_id": review_id,
        "verdict": verdict,
        "total_findings": len(validated),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "run_path": str(resolved_run) if resolved_run else None,
    }

    result["review_yaml"] = _helpers._persist_review_artifact(
        resolved_run,
        {
            "review_id": review_id,
            "timestamp": ts,
            "verdict": verdict,
            "critical_count": critical_count,
            "warning_count": warning_count,
            "info_count": info_count,
            "findings": validated,
        },
        {
            "review_id": review_id,
            "verdict": verdict,
            "critical_count": critical_count,
            "warning_count": warning_count,
            "prd_ids": list(prd_ids) if prd_ids else [],
        },
    )
    return result


def validate_manual_findings(
    raw_findings: list[dict[str, str]],
) -> list[ReviewFindingDict]:
    """Validate and normalize a list of manually-provided findings.

    Runs each finding through ReviewFinding model validation,
    normalizing severity levels to the canonical set.
    """
    import contextlib

    from trw_mcp.models.run import ReviewFinding

    validated: list[ReviewFindingDict] = []
    for f in raw_findings:
        normalized = {**f, "severity": _helpers._normalize_severity(f.get("severity", "info"))}
        with contextlib.suppress(Exception):
            ReviewFinding(**normalized)  # type: ignore[arg-type]  # dict[str,str] coerced by Pydantic
        validated.append(cast("ReviewFindingDict", normalized))
    return validated


def count_by_severity(
    findings: list[ReviewFindingDict],
) -> tuple[int, int, int]:
    """Count findings by severity level.

    Returns:
        Tuple of (critical_count, warning_count, info_count).
    """
    critical = sum(1 for f in findings if f.get("severity") == "critical")
    warning = sum(1 for f in findings if f.get("severity") == "warning")
    info = sum(1 for f in findings if f.get("severity") == "info")
    return critical, warning, info


# ---------------------------------------------------------------------------
# Reconcile mode
# ---------------------------------------------------------------------------


def _extract_section(content: str, section_name: str) -> str:
    """Extract content under a ## section heading."""
    pattern = rf"^##\s+(?:\d+\.\s+)?{re.escape(section_name)}\s*$"
    match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"^##\s+", content[start:], re.MULTILINE)
    if next_heading:
        return content[start : start + next_heading.start()].strip()
    return content[start:].strip()


def _extract_identifiers(text: str) -> list[str]:
    """Extract code identifiers from FR text."""
    identifiers: list[str] = []
    # Backtick-wrapped identifiers
    identifiers.extend(re.findall(r"`([^`]+)`", text))
    # --flags
    identifiers.extend(re.findall(r"(--[a-zA-Z][\w-]*)", text))
    # PascalCase class names (2+ uppercase letters)
    identifiers.extend(re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", text))
    return list(dict.fromkeys(identifiers))  # deduplicate preserving order


def _added_lines_only(diff: str) -> str:
    """Extract only added (+) lines from a git diff.

    Filters out removed (-) lines to prevent false negatives: an identifier
    that was *deleted* from code should not count as "present in diff".
    """
    return "\n".join(line for line in diff.splitlines() if not line.startswith("-") or line.startswith("---"))


def _extract_fr_mismatches(
    prd_content: str,
    prd_id: str,
    diff: str,
) -> list[dict[str, str]]:
    """Compare FR identifiers against diff, return mismatches."""
    mismatches: list[dict[str, str]] = []
    section = _extract_section(prd_content, "Functional Requirements")
    if not section:
        return mismatches

    # Only check added lines -- removed lines should not mask drift
    added_diff = _added_lines_only(diff)

    # Split into individual FRs
    fr_pattern = re.compile(
        r"(?:^|\n)(?:###?\s*)?FR(\d+)\s*[:\-\u2013]\s*(.+?)(?=\n(?:###?\s*)?FR\d|\Z)",
        re.DOTALL,
    )
    for m in fr_pattern.finditer(section):
        fr_num = m.group(1)
        fr_text = m.group(2).strip()
        identifiers = _extract_identifiers(fr_text)
        mismatches.extend(
            {
                "prd_id": prd_id,
                "fr": f"FR{fr_num}",
                "identifier": ident,
                "recommendation": "update_spec",
            }
            for ident in identifiers
            if ident not in added_diff
        )
    return mismatches


def _count_frs_in_prd(prd_path: Path) -> int:
    """Count FR entries in a PRD file."""
    try:
        return len(
            re.findall(
                r"(?:^|\n)(?:###?\s*)?FR\d+",
                _extract_section(prd_path.read_text(encoding="utf-8"), "Functional Requirements"),
            )
        )
    except OSError:
        return 0


def handle_reconcile_mode(
    config: TRWConfig,
    resolved_run: Path | None,
    review_id: str,
    ts: str,
    prd_ids: list[str] | None,
) -> ReconcileReviewResult:
    """Handle the reconcile review mode -- compare PRD FRs against git diff."""
    from trw_mcp.state._paths import resolve_project_root

    # Discover PRDs if not explicitly provided
    effective_prd_ids = list(prd_ids) if prd_ids else []
    if not effective_prd_ids and resolved_run is not None:
        from trw_mcp.state.prd_utils import discover_governing_prds

        effective_prd_ids = discover_governing_prds(resolved_run, config)

    if not effective_prd_ids:
        return {
            "review_id": review_id,
            "verdict": "clean",
            "mismatches": [],
            "message": "No governing PRDs found",
        }

    diff = _helpers._get_git_diff()

    project_root = resolve_project_root()
    prds_dir = project_root / config.prds_relative_path

    all_mismatches: list[dict[str, str]] = []
    total_frs = 0

    for prd_id in effective_prd_ids:
        prd_path = prds_dir / f"{prd_id}.md"
        try:
            prd_content = prd_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("reconcile_prd_not_found", prd_id=prd_id, path=str(prd_path))
            continue
        # Count FRs from already-loaded content (avoids double file read)
        fr_section = _extract_section(prd_content, "Functional Requirements")
        total_frs += len(re.findall(r"(?:^|\n)(?:###?\s*)?FR\d+", fr_section))
        mismatches = _extract_fr_mismatches(prd_content, prd_id, diff)
        all_mismatches.extend(mismatches)

    verdict = "drift_detected" if all_mismatches else "clean"

    result: ReconcileReviewResult = {
        "review_id": review_id,
        "verdict": verdict,
        "mismatches": all_mismatches,
        "prd_count": len(effective_prd_ids),
        "total_frs": total_frs,
        "mismatch_count": len(all_mismatches),
    }

    # Persist reconciliation artifact and log event
    if resolved_run is not None:
        writer = FileStateWriter()
        reconciliation_path = resolved_run / "meta" / "reconciliation.yaml"
        reconciliation_data: dict[str, object] = {
            "review_id": review_id,
            "timestamp": ts,
            "verdict": verdict,
            "prd_ids": effective_prd_ids,
            "prd_count": len(effective_prd_ids),
            "total_frs": total_frs,
            "mismatch_count": len(all_mismatches),
            "mismatches": all_mismatches,
        }
        writer.write_yaml(reconciliation_path, reconciliation_data)

        # Log spec_reconciliation event
        events_path = resolved_run / "meta" / "events.jsonl"
        if events_path.parent.exists():
            event_logger = FileEventLogger(writer)
            event_logger.log_event(
                events_path,
                "spec_reconciliation",
                {
                    "review_id": review_id,
                    "verdict": verdict,
                    "mismatch_count": len(all_mismatches),
                    "prd_count": len(effective_prd_ids),
                },
            )

        result["reconciliation_yaml"] = str(reconciliation_path)

    return result
