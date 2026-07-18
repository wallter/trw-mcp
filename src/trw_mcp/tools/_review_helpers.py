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
    from trw_mcp.tools._review_provenance import RunIdentity

logger = structlog.get_logger(__name__)

PRE_IMPLEMENTATION_CHECKLIST_EVENT = "pre_implementation_checklist_complete"
PRE_AUDIT_SELF_REVIEW_EVENT = "pre_audit_self_review"
_PREFLIGHT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        PRE_IMPLEMENTATION_CHECKLIST_EVENT,
        PRE_AUDIT_SELF_REVIEW_EVENT,
    }
)

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


def _get_git_diff(paths: list[str] | None = None, base: str | None = None) -> str:
    """Get a git diff, returning empty string on any error.

    Default (no args) diffs the working tree against ``HEAD`` — the original
    contract. PRD-CORE-213-FR04 extends this with:
      - ``base``: diff ``<base>..HEAD`` (the run's recorded base ref) instead of
        the uncommitted ``HEAD`` diff, so committed transitions are visible.
      - ``paths``: a path-limited diff (``git diff ... -- <paths>``) so the
        transition detector's cost is bounded by the PRD directory (NFR03).
    All arguments are trusted internal literals / repo-relative paths — never
    caller-tainted shell input.
    """
    cmd = ["git", "diff", f"{base}..HEAD" if base else "HEAD"]
    if paths:
        cmd.append("--")
        cmd.extend(paths)
    try:
        # git is a well-known VCS tool; all args are static literals / repo-relative
        # paths, never caller-tainted shell input.
        result = subprocess.run(  # noqa: S603
            cmd,
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


def _cross_family_available(config: TRWConfig) -> bool:
    """Single source of truth for cross-family review availability (QUAL-108-FR04).

    Today this is config-only: a cross-family review is *available* iff the
    cross-model review feature is enabled AND a provider is configured. It does
    NOT assert the provider is reachable or that it returned findings — that
    *realized* signal is computed by the caller (NFR02 truthfulness invariant).

    # SEAM(PRD-DIST-2444): discovery-feed availability. A future discovered-model
    # inventory (D18 / PRD-DIST-2444, the proprietary trw-distill ledger) may
    # feed this predicate so availability reflects the realized fleet state
    # rather than config alone. This PRD makes NO discovery call here; the
    # inventory is read through a future thin adapter (QUAL-108 OQ2), never by
    # importing trw-distill internals. Expiry: revisit when PRD-DIST-2444 lands.
    """
    return bool(config.cross_model_review_enabled) and bool(config.cross_model_provider)


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
    result_payload: dict[str, object] | None = None,
    *,
    verified_reviewer_identity: RunIdentity | None = None,
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

    events_path = resolved_run / "meta" / "events.jsonl"
    prd_ids = _resolve_review_prd_ids(resolved_run, reader, event_fields)
    review_payload = dict(review_data)
    # PRD-CORE-213-FR01: stamp reviewer provenance onto every persisted
    # review.yaml. Manual mode injects its own block (with any explicit
    # reviewer_source) upstream; this central call covers auto/cross_model
    # from their ``mode`` key. Fail-open — never blocks the artifact write.
    from trw_mcp.tools._review_provenance import ensure_reviewer_block

    ensure_reviewer_block(review_payload, resolved_run, reader, verified_identity=verified_reviewer_identity)
    preflight_checks = _load_preflight_checks(resolved_run, reader, prd_ids)
    if preflight_checks:
        review_payload["preflight_checks"] = preflight_checks

    # CORE-205 FR02/FR03: the typed receipt is authoritative.  review.yaml is
    # retained only as a derived projection.  Receipt failure is visible and,
    # under enforce mode, cannot retain a legacy positive ``substantive`` bit.
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._review_receipt_writer import record_review_receipt

    evidence_mode = str(getattr(get_config(), "evidence_receipt_mode", "enforce"))
    receipt_outcome = record_review_receipt(
        resolved_run,
        review_payload,
        tuple(prd_ids),
        policy_mode=evidence_mode,
    )
    review_payload["review_receipt_id"] = receipt_outcome.receipt_id
    review_payload["review_plan_id"] = receipt_outcome.plan_id
    review_payload["typed_receipt_state"] = receipt_outcome.state
    review_payload["typed_receipt_reason"] = receipt_outcome.reason_code
    if evidence_mode == "enforce" and not receipt_outcome.ok:
        review_payload["substantive"] = False
        review_payload["non_substantive_reason"] = receipt_outcome.reason_code
    if result_payload is not None:
        result_payload["review_receipt_id"] = receipt_outcome.receipt_id
        result_payload["review_plan_id"] = receipt_outcome.plan_id
        result_payload["typed_receipt_state"] = receipt_outcome.state
        result_payload["typed_receipt_reason"] = receipt_outcome.reason_code
        if evidence_mode == "enforce" and not receipt_outcome.ok:
            result_payload["substantive"] = False
            result_payload["non_substantive_reason"] = receipt_outcome.reason_code

    review_path = resolved_run / "meta" / "review.yaml"
    writer.write_yaml(review_path, review_payload)
    markdown_path = resolved_run / "meta" / "review.md"
    writer.write_text(markdown_path, render_review_markdown(review_payload))

    if events_path.parent.exists():
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


def render_review_markdown(review_data: dict[str, object]) -> str:
    """Render review data as PR-description friendly Markdown."""
    verdict = str(review_data.get("verdict", "unknown")).upper()
    mode = str(review_data.get("mode", review_data.get("phase", "manual")))
    findings = review_data.get("findings", review_data.get("cross_model_findings", []))
    lines = [f"# TRW Review: {verdict}", "", f"- Mode: `{mode}`"]
    if isinstance(findings, list):
        lines.append(f"- Findings: {len(findings)}")
        lines.append("")
        lines.append("| Severity | Category | Description |")
        lines.append("| --- | --- | --- |")
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = _markdown_table_cell(finding.get("severity", "info"))
            category = _markdown_table_cell(finding.get("category", "general"))
            description = _markdown_table_cell(finding.get("description", ""))
            lines.append(f"| {severity} | {category} | {description} |")
    return "\n".join(lines) + "\n"


def _markdown_table_cell(value: object) -> str:
    """Escape a value for safe single-line Markdown table rendering."""
    return " ".join(str(value).replace("|", "\\|").splitlines())


def _log_preflight_events(
    resolved_run: Path | None,
    *,
    prd_id: str,
    checklist_complete: bool = False,
    self_review: dict[str, object] | None = None,
) -> list[str]:
    """Persist explicit preflight checklist/self-review events for a run."""
    if resolved_run is None:
        return []

    events_path = resolved_run / "meta" / "events.jsonl"
    if not events_path.parent.exists():
        return []

    writer = FileStateWriter()
    event_logger = FileEventLogger(writer)
    logged_events: list[str] = []

    if checklist_complete:
        event_logger.log_event(
            events_path,
            PRE_IMPLEMENTATION_CHECKLIST_EVENT,
            {
                "prd_id": prd_id,
                "completed": True,
            },
        )
        logged_events.append(PRE_IMPLEMENTATION_CHECKLIST_EVENT)

    if self_review is not None:
        event_logger.log_event(
            events_path,
            PRE_AUDIT_SELF_REVIEW_EVENT,
            _normalize_self_review_payload(prd_id, self_review if isinstance(self_review, dict) else {}),
        )
        logged_events.append(PRE_AUDIT_SELF_REVIEW_EVENT)

    return logged_events


def _resolve_review_prd_ids(
    resolved_run: Path,
    reader: FileStateReader,
    event_fields: dict[str, object],
) -> list[str]:
    """Resolve PRD IDs for a review event from explicit fields or run scope."""
    raw_prd_ids = event_fields.get("prd_ids")
    if isinstance(raw_prd_ids, list):
        prd_ids = list(dict.fromkeys(str(prd_id) for prd_id in raw_prd_ids if str(prd_id)))
        if prd_ids:
            return prd_ids

    run_yaml_path = resolved_run / "meta" / "run.yaml"
    if not run_yaml_path.exists():
        return []

    run_data = reader.read_yaml(run_yaml_path)
    raw_scope = run_data.get("prd_scope", []) if isinstance(run_data, dict) else []
    if not isinstance(raw_scope, list):
        return []
    return list(dict.fromkeys(str(prd_id) for prd_id in raw_scope if str(prd_id)))


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


def _normalize_self_review_payload(prd_id: str, self_review: dict[str, object]) -> dict[str, object]:
    """Normalize a self-review payload before persisting to events.jsonl."""
    return {
        "prd_id": prd_id,
        "passed": _normalize_self_review_count(self_review.get("passed")),
        "failed": _normalize_self_review_count(self_review.get("failed")),
        "skipped": _normalize_self_review_count(self_review.get("skipped")),
        "wiring_issues": _normalize_issue_list(self_review.get("wiring_issues")),
        "nfr_issues": _normalize_issue_list(self_review.get("nfr_issues")),
        "test_issues": _normalize_issue_list(self_review.get("test_issues")),
    }


def _normalize_self_review_count(value: object) -> int:
    """Coerce malformed self-review counters to a non-negative integer."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0) if value.is_integer() else 0
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return 0
        try:
            return max(int(normalized), 0)
        except ValueError:
            return 0
    return 0


def _normalize_issue_list(value: object) -> list[str]:
    """Coerce a possibly-missing issue collection into ``list[str]``."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    if value is None:
        return []
    return []


def _load_preflight_checks(
    resolved_run: Path,
    reader: FileStateReader,
    prd_ids: list[str],
) -> dict[str, dict[str, dict[str, object]]]:
    """Load latest preflight checklist/self-review events for the scoped PRDs."""
    events_path = resolved_run / "meta" / "events.jsonl"
    if not events_path.exists():
        return {}

    try:
        events = reader.read_jsonl(events_path)
    except Exception:  # justified: fail-open, review artifact should persist without preflight metadata
        logger.debug("review_preflight_checks_unavailable", exc_info=True)
        return {}

    scoped_prd_ids = set(prd_ids)
    preflight_checks: dict[str, dict[str, dict[str, object]]] = {}
    for event in events:
        event_type = str(event.get("event", ""))
        if event_type not in _PREFLIGHT_EVENT_TYPES:
            continue

        event_data = _extract_review_event_data(event)
        prd_id = str(event_data.get("prd_id", ""))
        if not prd_id or (scoped_prd_ids and prd_id not in scoped_prd_ids):
            continue

        preflight_checks.setdefault(prd_id, {})[event_type] = {
            **event_data,
            "ts": str(event.get("ts", event_data.get("ts", ""))),
        }

    return preflight_checks


def _extract_review_event_data(event: dict[str, object]) -> dict[str, object]:
    """Return a normalized event payload for flat or nested event records."""
    nested = event.get("data")
    if isinstance(nested, dict):
        return nested
    return event


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
