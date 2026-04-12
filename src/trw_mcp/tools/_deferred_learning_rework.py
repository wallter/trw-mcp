"""Rework-metric helpers for deferred delivery learning steps.

Extracted from ``_deferred_steps_learning.py`` to keep the parent module
under the PRD-FIX-061 size gate while preserving its public re-exports.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.typed_dicts import ReworkMetricsResult
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)


def _step_collect_rework_metrics(
    run_path: Path | None,
    reader: FileStateReader,
) -> ReworkMetricsResult:
    """Collect audit rework metrics from events.jsonl for the delivery report.

    Scans events.jsonl for:
    - audit_cycle_complete events (counted per PRD ID)
    - First verdict per PRD determines first-pass compliance

    Returns dict with:
    - audit_cycles: dict mapping PRD ID to cycle count
    - first_pass_compliance: dict mapping PRD ID to bool
    - sprint_avg_audit_cycles: float average across all PRDs
    - sprint_first_pass_compliance_rate: float (0.0-1.0)
    """
    empty: ReworkMetricsResult = {
        "audit_cycles": {},
        "first_pass_compliance": {},
        "finding_categories": {},
        "sprint_avg_audit_cycles": 0.0,
        "sprint_first_pass_compliance_rate": 0.0,
    }

    if run_path is None:
        return empty

    events_path = run_path / "meta" / "events.jsonl"
    if not events_path.exists():
        return empty

    try:
        events = reader.read_jsonl(events_path)
    except Exception:  # justified: fail-open, metrics are best-effort
        logger.debug("rework_metrics_read_failed", exc_info=True)
        return empty

    audit_cycles: dict[str, int] = {}
    first_verdict: dict[str, str] = {}
    finding_categories: dict[str, int] = {}

    for ev in events:
        if str(ev.get("event", "")) != "audit_cycle_complete":
            continue

        ev_data = _extract_event_data(ev)
        prd_id = str(ev_data.get("prd_id", ""))
        if not prd_id:
            continue

        verdict = str(ev_data.get("verdict", "")).upper()
        audit_cycles[prd_id] = audit_cycles.get(prd_id, 0) + 1
        for category in _extract_finding_categories(ev_data):
            finding_categories[category] = finding_categories.get(category, 0) + 1

        if prd_id not in first_verdict:
            first_verdict[prd_id] = verdict

    if not audit_cycles:
        return empty

    first_pass_compliance: dict[str, bool] = {
        prd_id: first_verdict.get(prd_id, "") == "PASS"
        for prd_id in audit_cycles
    }
    prd_count = len(audit_cycles)
    compliant_count = sum(1 for value in first_pass_compliance.values() if value)
    return {
        "audit_cycles": audit_cycles,
        "first_pass_compliance": first_pass_compliance,
        "finding_categories": finding_categories,
        "sprint_avg_audit_cycles": sum(audit_cycles.values()) / prd_count,
        "sprint_first_pass_compliance_rate": compliant_count / prd_count,
    }


def _extract_event_data(event: dict[str, object]) -> dict[str, object]:
    """Return normalized event payload for flat or nested event records."""
    nested = event.get("data")
    if isinstance(nested, dict):
        return nested
    return event


def _extract_finding_categories(event_data: dict[str, object]) -> list[str]:
    """Extract normalized finding categories from an audit-cycle event payload."""
    candidates = event_data.get("finding_categories", event_data.get("categories"))
    if isinstance(candidates, dict):
        expanded: list[str] = []
        for category, count in candidates.items():
            try:
                repeats = max(int(str(count)), 0)
            except ValueError:
                repeats = 0
            expanded.extend([str(category)] * repeats)
        return expanded
    if isinstance(candidates, list):
        return [str(category) for category in candidates if str(category)]
    if isinstance(candidates, str) and candidates:
        return [candidates]

    findings = event_data.get("findings")
    if isinstance(findings, list):
        extracted: list[str] = []
        for finding in findings:
            if isinstance(finding, dict):
                category = str(finding.get("category", finding.get("type", "")))
                if category:
                    extracted.append(category)
            elif isinstance(finding, str) and finding:
                extracted.append(finding)
        return extracted
    return []
