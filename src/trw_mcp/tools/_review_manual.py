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
from trw_mcp.tools._review_validation import normalize_review_findings

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._review_provenance import RunIdentity

logger = structlog.get_logger(__name__)

_FR_LABEL_PATTERN = r"(?:PRD-[\w-]+-)?FR\d+"
_FR_CAPTURE_PATTERN = r"(?:PRD-[\w-]+-)?FR(\d+)"

# Honest label for what reconcile coverage actually measures: a flat substring
# test for extracted FR identifiers against added diff lines. This is NOT
# behavioral / spec verification -- consumers must not read a 'clean' verdict
# as "FRs verified covered". See VISION Principle #3 (honest evidence).
# trw:intentional reconcile does presence-matching, not behavioral coverage
RECONCILE_COVERAGE_METHOD = "identifier_presence_in_diff"
MANUAL_EMPTY_REVIEW_REASON = (
    "manual review recorded no schema-valid findings and no independent reviewer receipt; "
    "it is an empty artifact, not substantive REVIEW evidence"
)


# ---------------------------------------------------------------------------
# Manual mode
# ---------------------------------------------------------------------------


def handle_manual_mode(
    raw_findings: list[dict[str, str]],
    resolved_run: Path | None,
    review_id: str,
    ts: str,
    prd_ids: list[str] | None = None,
    *,
    reviewer_source: str | None = None,
    reviewer_receipt_id: str | None = None,
    review_completed: bool = False,
    verified_reviewer_identity: RunIdentity | None = None,
    external_receipt_path: str | None = None,
    adversarial_pass: bool = False,
) -> ManualReviewResult:
    """Handle the manual review mode -- validate findings, compute verdict, persist.

    PRD-CORE-213-FR01: stamps a ``reviewer`` provenance block. ``reviewer_source``
    defaults to ``self`` for manual mode (derived honestly from the mode); an
    explicit ``operator`` source requires ``reviewer_receipt_id`` or raises.
    ``verified_reviewer_identity`` (OQ-001) must come from
    ``resolve_verified_reviewer_identity`` — it stamps the framework-verified
    reviewer identity instead of the delivering run's.

    PRD-CORE-255-FR02/FR04: ``external_receipt_path`` and ``adversarial_pass`` are
    passed through UNINTERPRETED to the receipt writer, which is the only place
    that decides what they earn. This handler never upgrades a family or honors
    an adversarial claim itself — a second interpretation site is how the two
    would drift apart.
    """
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._review_provenance import build_reviewer_block, derive_reviewer_source

    accepted, rejections = normalize_review_findings(raw_findings)
    validated = cast("list[ReviewFindingDict]", accepted)
    rejected_count = len(raw_findings) - len(validated)
    critical_count, warning_count, info_count = count_by_severity(validated)
    verdict = _helpers._compute_verdict(cast("list[dict[str, str]]", validated))
    # CORE-205 FR02: a completed, fully covered review may honestly have zero
    # findings.  Empty-by-default remains non-substantive; the explicit
    # completion assertion records that the server-issued manual rubric was
    # actually exercised.
    substantive = bool(validated) or review_completed

    source = derive_reviewer_source("manual", reviewer_source)
    reviewer_block = build_reviewer_block(
        resolved_run,
        FileStateReader(),
        source=source,
        receipt_id=reviewer_receipt_id,
        ts=ts,
        verified_identity=verified_reviewer_identity,
    )

    result: ManualReviewResult = {
        "review_id": review_id,
        "verdict": verdict,
        "total_findings": len(validated),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "run_path": str(resolved_run) if resolved_run else None,
        "substantive": substantive,
        "reviewer": reviewer_block,
    }
    if not substantive:
        result["non_substantive_reason"] = MANUAL_EMPTY_REVIEW_REASON
    if rejected_count:
        # The caller's own input was dropped — say so IN the response. A log line
        # is invisible to the agent that supplied the findings, and silence here
        # is what turned a bad accept-list into an empty review nobody noticed.
        logger.warning("manual_review_findings_rejected", rejected=rejected_count, accepted=len(validated))
        result["rejected_findings_count"] = rejected_count
        result["rejected_findings"] = rejections

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
            "rejected_findings_count": rejected_count,
            "rejected_findings": rejections,
            "substantive": substantive,
            "non_substantive_reason": "" if substantive else MANUAL_EMPTY_REVIEW_REASON,
            "reviewer": reviewer_block,
            "review_completed": review_completed,
            "external_receipt_path": external_receipt_path or "",
            "adversarial_pass": adversarial_pass,
        },
        {
            "review_id": review_id,
            "verdict": verdict,
            "critical_count": critical_count,
            "substantive": substantive,
            "warning_count": warning_count,
            "prd_ids": list(prd_ids) if prd_ids else [],
        },
        cast("dict[str, object]", result),
    )
    return result


def validate_manual_findings(
    raw_findings: list[dict[str, str]],
) -> list[ReviewFindingDict]:
    """Validate and normalize a list of manually-provided findings.

    Runs each finding through ReviewFinding model validation, normalizing
    severity levels to the canonical set. Accepted findings only — callers that
    must REPORT what was dropped use ``normalize_review_findings`` directly (see
    :func:`handle_manual_mode`), which is the same validator with the rejection
    record attached.
    """
    accepted, rejections = normalize_review_findings(raw_findings)
    for rejection in rejections:
        logger.warning(
            "manual_review_finding_rejected",
            index=rejection.get("index"),
            reason=rejection.get("reason"),
        )
    return cast("list[ReviewFindingDict]", accepted)


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
        rf"(?:^|\n)(?:###?\s*)?{_FR_CAPTURE_PATTERN}\s*[:\-\u2013]\s*(.+?)(?=\n(?:###?\s*)?{_FR_LABEL_PATTERN}|\Z)",
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


def _extract_fr_not_checkable(
    prd_content: str,
    prd_id: str,
) -> list[dict[str, str]]:
    """Return FRs that have NO extractable identifier.

    Reconcile coverage is identifier-presence-in-diff matching. An FR whose
    text yields no backtick/``--flag``/PascalCase identifier cannot be checked
    by that method at all -- previously such FRs passed *silently* (no
    mismatch emitted), which let the ``clean`` verdict over-claim coverage.
    Surfacing them as ``fr_not_checkable`` keeps the verdict honest.
    """
    not_checkable: list[dict[str, str]] = []
    section = _extract_section(prd_content, "Functional Requirements")
    if not section:
        return not_checkable

    fr_pattern = re.compile(
        rf"(?:^|\n)(?:###?\s*)?{_FR_CAPTURE_PATTERN}\s*[:\-–]\s*(.+?)(?=\n(?:###?\s*)?{_FR_LABEL_PATTERN}|\Z)",
        re.DOTALL,
    )
    for m in fr_pattern.finditer(section):
        fr_num = m.group(1)
        fr_text = m.group(2).strip()
        if not _extract_identifiers(fr_text):
            not_checkable.append(
                {
                    "prd_id": prd_id,
                    "fr": f"FR{fr_num}",
                    "reason": "no_extractable_identifier",
                    "fr_text": fr_text[:200],
                }
            )
    return not_checkable


def _count_frs_in_prd(prd_path: Path) -> int:
    """Count FR entries in a PRD file."""
    try:
        return len(
            re.findall(
                rf"(?:^|\n)(?:###?\s*)?{_FR_LABEL_PATTERN}",
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
        # No PRD to check against. 'clean' here means "nothing to reconcile",
        # NOT "FRs verified covered" -- flag it so a consumer can't conflate them.
        return {
            "review_id": review_id,
            "verdict": "clean",
            "mismatches": [],
            "message": "No governing PRDs found",
            "no_governing_prd": True,
            "reason": "no_governing_prd_found_nothing_reconciled",
            "coverage_method": RECONCILE_COVERAGE_METHOD,
        }

    diff = _helpers._get_git_diff()

    project_root = resolve_project_root()
    prds_dir = project_root / config.prds_relative_path

    all_mismatches: list[dict[str, str]] = []
    all_not_checkable: list[dict[str, str]] = []
    # A PRD that could not be opened was skipped with only a log line while
    # prd_count still reported it, so two missing PRDs came back
    # verdict='clean', prd_count=2 — "reconciled cleanly" for files never read.
    prds_not_read: list[str] = []
    total_frs = 0

    for prd_id in effective_prd_ids:
        prd_path = prds_dir / f"{prd_id}.md"
        try:
            prd_content = prd_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("reconcile_prd_not_found", prd_id=prd_id, path=str(prd_path))
            prds_not_read.append(prd_id)
            continue
        # Count FRs from already-loaded content (avoids double file read)
        fr_section = _extract_section(prd_content, "Functional Requirements")
        total_frs += len(re.findall(rf"(?:^|\n)(?:###?\s*)?{_FR_LABEL_PATTERN}", fr_section))
        mismatches = _extract_fr_mismatches(prd_content, prd_id, diff)
        all_mismatches.extend(mismatches)
        # FRs with no extractable identifier can't be checked by the
        # presence-matching method -- surface them instead of passing silently.
        all_not_checkable.extend(_extract_fr_not_checkable(prd_content, prd_id))

    verdict = "drift_detected" if all_mismatches else "clean"
    prds_read_count = len(effective_prd_ids) - len(prds_not_read)

    result: ReconcileReviewResult = {
        "review_id": review_id,
        "verdict": verdict,
        "mismatches": all_mismatches,
        "prd_count": len(effective_prd_ids),
        # prd_count is what was REQUESTED; this is what was actually opened. The
        # two differ exactly when a PRD could not be read, and only the second
        # one bounds what the verdict can honestly speak for.
        "prds_read_count": prds_read_count,
        "total_frs": total_frs,
        "mismatch_count": len(all_mismatches),
        # Honest labeling: this verdict reflects identifier substring presence
        # in the diff, NOT behavioral / spec verification.
        "coverage_method": RECONCILE_COVERAGE_METHOD,
        "fr_not_checkable": all_not_checkable,
        "not_checkable_count": len(all_not_checkable),
    }
    if prds_not_read:
        result["prds_not_read"] = prds_not_read
        result["prds_not_read_count"] = len(prds_not_read)
    if not prds_read_count:
        # Nothing was opened, so 'clean' means "nothing was reconciled", not
        # "no drift" — same distinction the no_governing_prd branch already draws.
        result["reason"] = "no_prd_could_be_read_nothing_reconciled"

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
            "prds_read_count": prds_read_count,
            "prds_not_read": prds_not_read,
            "total_frs": total_frs,
            "mismatch_count": len(all_mismatches),
            "mismatches": all_mismatches,
            "coverage_method": RECONCILE_COVERAGE_METHOD,
            "fr_not_checkable": all_not_checkable,
            "not_checkable_count": len(all_not_checkable),
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
