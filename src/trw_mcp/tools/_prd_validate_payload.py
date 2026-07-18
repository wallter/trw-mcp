"""ValidateResultDict assembly + compaction for ``trw_prd_validate``.

Belongs to the ``requirements.py`` facade. Extracted so ``requirements.py``
stays under its effective-LOC ratchet: the pure projection of a
:class:`ValidationResultV2` (plus cache metadata) into the wire ``ValidateResultDict``
lives here, while the tool retains orchestration (I/O, caching, phase update).

Token-bloat W5: ``trw_prd_validate`` is the highest per-session token lever in
grooming/audit loops. ``build_validate_payload`` is compact-by-default and
mirrors the ``_session_start_trim.py`` precedent — a ``verbose=True`` escape
hatch reproduces the full diagnostic shape, load-bearing scoring/gate fields are
never dropped, and compaction is fail-open (any error returns the full payload).
The compaction only reshapes the RESPONSE; every score/gate verdict is already
computed from full data upstream in ``prd_quality.py``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import structlog

from trw_mcp.models.requirements import ValidationResultV2
from trw_mcp.models.typed_dicts import ValidateResultDict
from trw_mcp.state.validation import extract_wiring_warnings
from trw_mcp.tools._prd_validation_cache import (
    CACHE_SCHEMA_VERSION,
    is_degraded_reason,
)

logger = structlog.get_logger(__name__)

# EARS patterns that are authoring smells worth surfacing line numbers for.
# The others (ubiquitous/state-driven/event-driven/optional-feature/
# unwanted-behavior) are well-formed and only need a count in compact mode.
_ACTIONABLE_EARS_PATTERNS = frozenset({"non-ears", "complex"})
# Cap on the actionable-line list in compact mode (mirrors improvement_suggestions[:5]).
_MAX_ACTIONABLE_EARS_LINES = 10
# Per-category sample-line cap for grouped smell findings.
_MAX_SMELL_SAMPLE_LINES = 5
# Cache sub-fields that are pure on-disk addressing plumbing — never actionable
# to the calling LLM. Kept in verbose mode for cache-mechanism debugging.
_VERBOSE_ONLY_CACHE_KEYS = ("key", "content_hash", "config_hash")


def build_validate_payload(
    v2_result: ValidationResultV2,
    *,
    path: Path,
    sections: list[str],
    sections_expected: list[str],
    frontmatter: dict[str, object],
    cache_hit: bool,
    cache_key: str,
    cache_miss_reason: str,
    cache_metadata: Mapping[str, object],
    verbose: bool = False,
) -> ValidateResultDict:
    """Project a validated V2 result + cache metadata into the wire dict.

    Compact by default; ``verbose=True`` returns the full diagnostic shape.
    ``implementation_test_link_coverage`` (an exact alias of
    ``measured_traceability_coverage``) and the always-empty ``readability`` /
    always-zero ``consistency_score`` stub fields are dropped unconditionally.
    """
    payload: ValidateResultDict = {
        # V1 fields (backward compatible, from V2 inline computation)
        "path": str(path),
        "valid": v2_result.valid,
        "completeness_score": v2_result.completeness_score,
        "traceability_coverage": v2_result.traceability_coverage,
        "measured_traceability_coverage": v2_result.measured_traceability_coverage,
        "verification_mapping_coverage": v2_result.verification_mapping_coverage,
        "ambiguity_rate": v2_result.ambiguity_rate,
        "prd_status": str(frontmatter.get("status", "") or ""),
        "sections_found": sections,
        "sections_expected": sections_expected,
        "failures": [
            {
                "field": f.field,
                "rule": f.rule,
                "message": f.message,
                "severity": f.severity,
            }
            for f in v2_result.failures
        ],
        # V2 fields (PRD-CORE-008)
        "total_score": v2_result.total_score,
        "quality_tier": v2_result.quality_tier,
        "grade": v2_result.grade,
        "dimensions": [
            {
                "name": d.name,
                "score": d.score,
                "max_score": d.max_score,
                "details": d.details,
            }
            for d in v2_result.dimensions
        ],
        "improvement_suggestions": [
            {
                "dimension": s.dimension,
                "priority": s.priority,
                "message": s.message,
                "current_score": s.current_score,
                "potential_gain": s.potential_gain,
            }
            for s in v2_result.improvement_suggestions[:5]
        ],
        # Rich diagnostics (PRD-FIX-011: previously discarded). Full per-
        # occurrence shape; compaction groups/caps these for the default path.
        "smell_findings": [
            {
                "category": sf.category,
                "matched_text": sf.matched_text,
                "line_number": sf.line_number,
                "severity": sf.severity,
                "suggestion": sf.suggestion,
            }
            for sf in v2_result.smell_findings
        ],
        "ears_classifications": v2_result.ears_classifications,
        "section_scores": [
            {
                "section_name": ss.section_name,
                "density": ss.density,
                "substantive_lines": ss.substantive_lines,
            }
            for ss in v2_result.section_scores
        ],
        # Risk scaling metadata (PRD-QUAL-013)
        "effective_risk_level": v2_result.effective_risk_level,
        "risk_scaled": v2_result.risk_scaled,
        "status_drift_warnings": v2_result.status_drift_warnings,
        "integrity_warnings": v2_result.integrity_warnings,
        # PRD-CORE-190 FR03: full wiring set, un-truncated (helper docs).
        "wiring_gate_warnings": extract_wiring_warnings(v2_result),
        "cache": {
            "hit": cache_hit,
            "key": cache_key,
            "storage_version": CACHE_SCHEMA_VERSION,
            "miss_reason": "" if cache_hit else cache_miss_reason,
            "degraded": (not cache_hit) and is_degraded_reason(cache_miss_reason),
            **cache_metadata,
        },
    }
    if verbose:
        payload["compact"] = False
        return payload
    return compact_validate_payload(payload)


def _group_smell_findings(findings: Sequence[object]) -> list[dict[str, object]]:
    """Collapse per-occurrence smell findings into one entry per category.

    Each category keeps its constant ``suggestion`` once (it was re-emitted
    verbatim on every hit), a total ``count``, the escalated ``severity``
    (``warning`` if any hit in the category is a warning), and up to
    :data:`_MAX_SMELL_SAMPLE_LINES` sample line numbers.
    """
    order: list[str] = []
    count: dict[str, int] = {}
    severity: dict[str, str] = {}
    suggestion: dict[str, object] = {}
    samples: dict[str, list[int]] = {}
    for sf in findings:
        if not isinstance(sf, dict):
            continue
        category = str(sf.get("category") or "uncategorized")
        sev = str(sf.get("severity") or "")
        if category not in count:
            order.append(category)
            count[category] = 0
            severity[category] = sev
            suggestion[category] = sf.get("suggestion", "")
            samples[category] = []
        count[category] += 1
        line = sf.get("line_number")
        if isinstance(line, int) and len(samples[category]) < _MAX_SMELL_SAMPLE_LINES:
            samples[category].append(line)
        if sev == "warning":
            severity[category] = "warning"
    return [
        {
            "category": category,
            "count": count[category],
            "severity": severity[category],
            "suggestion": suggestion[category],
            "sample_lines": samples[category],
        }
        for category in order
    ]


def _compact_ears(classifications: Sequence[object]) -> dict[str, object]:
    """Reduce the per-line EARS list to counts + actionable line numbers.

    Well-formed patterns collapse to a ``counts`` histogram; only the
    authoring-smell patterns (:data:`_ACTIONABLE_EARS_PATTERNS`) contribute
    capped ``actionable_lines``. The ``text`` excerpt is dropped — the caller
    already has the PRD source on disk.
    """
    counts: dict[str, int] = {}
    actionable_lines: list[int] = []
    for ec in classifications:
        if not isinstance(ec, dict):
            continue
        pattern = str(ec.get("pattern") or "unknown")
        counts[pattern] = counts.get(pattern, 0) + 1
        if pattern in _ACTIONABLE_EARS_PATTERNS and len(actionable_lines) < _MAX_ACTIONABLE_EARS_LINES:
            line = ec.get("line_number")
            if isinstance(line, int):
                actionable_lines.append(line)
    return {"counts": counts, "actionable_lines": actionable_lines}


def _dedup_wiring_warnings(payload: ValidateResultDict) -> list[str]:
    """Drop wiring warnings already surfaced as improvement_suggestions[:5].

    A wiring-dimension message inside the returned top-5 suggestions is emitted
    as a full object there AND as a bare string in ``wiring_gate_warnings`` — a
    byte-for-byte duplicate in the same response. This keeps the un-truncated
    set (PRD-CORE-190 FR03) minus only the messages the caller already has.
    """
    warnings = payload.get("wiring_gate_warnings")
    if not isinstance(warnings, list):
        return warnings if isinstance(warnings, list) else []
    seen: set[str] = set()
    suggestions = payload.get("improvement_suggestions")
    if isinstance(suggestions, list):
        for suggestion in suggestions:
            if isinstance(suggestion, dict):
                message = suggestion.get("message")
                if isinstance(message, str):
                    seen.add(message)
    return [w for w in warnings if not (isinstance(w, str) and w in seen)]


def compact_validate_payload(payload: ValidateResultDict) -> ValidateResultDict:
    """Reshape a full validate payload into the compact default form.

    Fail-open: any internal error returns ``payload`` unchanged so a validation
    response is never lost. Every reshaped field has a ``verbose=True`` full
    form; the scoring/gate verdicts are already fixed upstream, so this only
    trims the diagnostic surface.
    """
    try:
        raw_smells = payload.get("smell_findings")
        if isinstance(raw_smells, list):
            payload["smell_findings"] = _group_smell_findings(raw_smells)

        raw_ears = payload.get("ears_classifications")
        if isinstance(raw_ears, list):
            payload["ears_classifications"] = _compact_ears(raw_ears)

        cache = payload.get("cache")
        if isinstance(cache, dict):
            for key in _VERBOSE_ONLY_CACHE_KEYS:
                cache.pop(key, None)

        payload["wiring_gate_warnings"] = _dedup_wiring_warnings(payload)
        payload["compact"] = True
        return payload
    except Exception:  # justified: fail-open, compaction must never drop a result
        logger.debug("prd_validate_compaction_failed", exc_info=True)
        return payload
