"""Shared validation for findings that may satisfy substantive REVIEW readiness."""

from __future__ import annotations

from collections.abc import Sequence

#: Every severity label a caller may supply, mapped to the internal level it
#: normalizes to. This is the SINGLE source for both questions the review path
#: asks — "is this label acceptable?" and "what does it mean?" — because when
#: those were two lists they disagreed: TRW's own audit protocol emits P0/P1/P2
#: (``data/skills/trw-audit/audit-framework.md`` Section E severity table, and
#: every ``trw-auditor`` report), and the accept-list held none of them. A whole
#: audit handoff normalized to zero findings and recorded ``substantive: false``.
#:
#: P-level alignment follows the audit protocol's own verdict rule (PASS requires
#: zero P0 AND zero P1), which matches the pre-existing ``high -> critical``
#: mapping: a P1 is verdict-blocking there, so it is verdict-blocking here.
SEVERITY_ALIASES: dict[str, str] = {
    "critical": "critical",
    "error": "critical",
    "high": "critical",
    "p0": "critical",
    "p1": "critical",
    "warning": "warning",
    "medium": "warning",
    "p2": "warning",
    "info": "info",
    "low": "info",
    "p3": "info",
}

#: Derived, never hand-maintained — a label is acceptable iff it has a meaning.
_VALID_REVIEW_SEVERITIES = frozenset(SEVERITY_ALIASES)

#: Closed set of machine-readable rejection reasons. A rejected finding is
#: caller-fixable INPUT feedback, not maintainer diagnostics: the audit incident
#: this module exists to prevent (every ``P0``/``P1``/``P2`` finding dropped,
#: ``substantive: false``, and nothing in the response naming the offending
#: field) was caused as much by the SILENCE as by the accept-list. Every drop
#: now carries an index, a reason from this set, and — where the offending value
#: is a short label — that value.
REJECT_NOT_A_MAPPING = "not_a_mapping"
REJECT_BLANK_CATEGORY = "blank_or_missing_category"
REJECT_BLANK_DESCRIPTION = "blank_or_missing_description"
REJECT_UNRECOGNIZED_SEVERITY = "unrecognized_severity"
REJECT_INVALID_CONFIDENCE = "invalid_confidence"
REJECT_SCHEMA_INVALID = "schema_invalid"

#: Closed set of reasons the confidence gate removed a finding that had ALREADY
#: passed schema validation. Kept distinct from the reject reasons above because
#: the two need different fixes: a rejection means the payload was malformed, a
#: suppression means it was well-formed and filtered out before the verdict.
SUPPRESSED_BELOW_THRESHOLD = "below_confidence_threshold"
SUPPRESSED_UNSCORABLE_CONFIDENCE = "unscorable_confidence"
SUPPRESSED_NOT_A_MAPPING = "not_a_mapping"

#: Cap on the per-response rejection detail list. The COUNT is always exact;
#: only the itemized list is capped, so a pathological payload cannot bloat the
#: tool response (trw-mcp-python.md response token budget).
MAX_REPORTED_REJECTIONS = 10


def classify_review_finding(
    finding: object,
    *,
    default_confidence: float | None = None,
) -> tuple[dict[str, object] | None, str, str]:
    """Normalize one finding, returning ``(normalized, reason_code, offending_value)``.

    On acceptance ``reason_code`` is ``""``. On rejection ``normalized`` is
    ``None`` and ``reason_code`` names which check failed, so the caller can tell
    the author exactly what to fix instead of dropping the finding in silence.
    ``offending_value`` carries the short label that failed (the severity string)
    when one exists, else ``""`` — never a description body, which could be long
    or sensitive.
    """
    if not isinstance(finding, dict):
        return None, REJECT_NOT_A_MAPPING, ""

    category = finding.get("category")
    description = finding.get("description")
    severity = finding.get("severity")
    if not isinstance(category, str) or not category.strip():
        return None, REJECT_BLANK_CATEGORY, ""
    if not isinstance(description, str) or not description.strip():
        return None, REJECT_BLANK_DESCRIPTION, ""
    if not isinstance(severity, str) or severity.strip().lower() not in _VALID_REVIEW_SEVERITIES:
        return None, REJECT_UNRECOGNIZED_SEVERITY, str(severity)[:32] if severity is not None else ""

    from pydantic import ValidationError

    from trw_mcp.models.run import ReviewFinding
    from trw_mcp.tools._review_helpers import _normalize_severity

    normalized: dict[str, object] = {
        **finding,
        "category": category.strip(),
        "description": description.strip(),
        "severity": _normalize_severity(severity),
    }
    if "confidence" not in normalized and default_confidence is not None:
        normalized["confidence"] = default_confidence
    raw_confidence = normalized.get("confidence")
    if isinstance(raw_confidence, bool):
        return None, REJECT_INVALID_CONFIDENCE, ""
    if isinstance(raw_confidence, (int, float)) and 1 < raw_confidence <= 100:
        normalized["confidence"] = raw_confidence / 100

    try:
        return ReviewFinding.model_validate(normalized).model_dump(), "", ""
    except (TypeError, ValidationError):
        return None, REJECT_SCHEMA_INVALID, ""


def normalize_review_finding(
    finding: object,
    *,
    default_confidence: float | None = None,
) -> dict[str, object] | None:
    """Return a canonical schema-valid finding, or ``None``.

    A finding can contribute substantive REVIEW evidence only when its category
    and description are non-blank strings and its severity is a recognized
    canonical or external alias. This rejects placeholder mappings such as
    ``{}`` instead of silently converting them into ``info`` findings. Surviving
    input is validated and serialized by the canonical ``ReviewFinding`` model;
    percentage confidence values are normalized to that model's 0..1 scale.

    Thin projection of :func:`classify_review_finding` for callers that do not
    report rejections; prefer :func:`normalize_review_findings` on any path whose
    result reaches a tool response.
    """
    return classify_review_finding(finding, default_confidence=default_confidence)[0]


def normalize_review_findings(
    raw_findings: Sequence[object],
    *,
    default_confidence: float | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Normalize a batch, returning ``(accepted, rejections)``.

    ``rejections`` is the caller-facing record of what was dropped and why —
    ``{"index": int, "reason": str}`` plus ``"value"`` when a short offending
    label exists. It is capped at :data:`MAX_REPORTED_REJECTIONS` entries; the
    caller reports the exact count separately. Every review mode that accepts
    caller-supplied findings routes through here so no mode can regress to a
    silent drop.
    """
    accepted: list[dict[str, object]] = []
    rejections: list[dict[str, object]] = []
    for index, raw in enumerate(raw_findings):
        normalized, reason, value = classify_review_finding(raw, default_confidence=default_confidence)
        if normalized is not None:
            accepted.append(normalized)
            continue
        if len(rejections) < MAX_REPORTED_REJECTIONS:
            entry: dict[str, object] = {"index": index, "reason": reason}
            if value:
                entry["value"] = value
            rejections.append(entry)
    return accepted, rejections


def apply_confidence_gate(
    findings: Sequence[object],
    threshold: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]], int]:
    """Split *findings* into ``(surfaced, suppressed_report, suppressed_count)``.

    The confidence gate is the SECOND place a review can lose a caller's finding,
    after schema validation. It used to drop silently: a finding filtered here
    never reached ``surfaced``, never reached ``findings`` in review.yaml, and was
    named nowhere in the response — so a payload of real findings could come back
    ``verdict='pass'`` with ``critical_count=0``. Every removal now carries an
    index and a closed-set reason.

    ``threshold`` is the 0-100 config scale; a finding's ``confidence`` is the
    ``ReviewFinding`` 0.0-1.0 scale, so values <= 1.0 are scaled up before
    comparison. ``suppressed_count`` is exact; the report is capped at
    :data:`MAX_REPORTED_REJECTIONS`.
    """
    surfaced: list[dict[str, object]] = []
    report: list[dict[str, object]] = []
    suppressed_count = 0

    def _note(index: int, reason: str, value: str) -> None:
        nonlocal suppressed_count
        suppressed_count += 1
        if len(report) < MAX_REPORTED_REJECTIONS:
            entry: dict[str, object] = {"index": index, "reason": reason}
            if value:
                entry["value"] = value
            report.append(entry)

    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            _note(index, SUPPRESSED_NOT_A_MAPPING, "")
            continue
        confidence = finding.get("confidence", 0)
        # bool is an int subclass; a True/False confidence is not a score.
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            _note(index, SUPPRESSED_UNSCORABLE_CONFIDENCE, str(confidence)[:32])
            continue
        confidence_pct = confidence * 100 if confidence <= 1.0 else confidence
        if confidence_pct >= threshold:
            surfaced.append(finding)
        else:
            _note(index, SUPPRESSED_BELOW_THRESHOLD, str(confidence_pct))
    return surfaced, report, suppressed_count
