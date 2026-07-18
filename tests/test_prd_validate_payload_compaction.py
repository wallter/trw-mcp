"""Compaction behavior for the ``trw_prd_validate`` wire payload (token-bloat W5).

Covers ``build_validate_payload(verbose=...)`` and the fail-open
``compact_validate_payload`` reshape: grouped smell findings, EARS
counts/actionable-lines, cache-hash gating, and wiring-warning dedup.
"""

from __future__ import annotations

import json
from typing import Any, cast

from trw_mcp.models.typed_dicts import ValidateResultDict
from trw_mcp.tools._prd_validate_payload import compact_validate_payload


def _full_payload() -> ValidateResultDict:
    """A representative full (verbose-shaped) payload for compaction tests."""
    payload: dict[str, Any] = {
        "path": "docs/PRD-X.md",
        "valid": True,
        "improvement_suggestions": [
            {
                "dimension": "wiring",
                "priority": "medium",
                "message": "wiring_gate_warning: FR01 has no consumer",
                "current_score": 0.0,
                "potential_gain": 0.0,
            },
            {
                "dimension": "content_density",
                "priority": "low",
                "message": "add more detail",
                "current_score": 1.0,
                "potential_gain": 2.0,
            },
        ],
        "smell_findings": [
            {
                "category": "weak_modal",
                "matched_text": "should",
                "line_number": 2,
                "severity": "warning",
                "suggestion": "Use 'shall'.",
            },
            {
                "category": "weak_modal",
                "matched_text": "may",
                "line_number": 3,
                "severity": "info",
                "suggestion": "Use 'shall'.",
            },
            {
                "category": "vague_adverb",
                "matched_text": "quickly",
                "line_number": 2,
                "severity": "warning",
                "suggestion": "Be measurable.",
            },
        ],
        "ears_classifications": [
            {"line_number": 2, "pattern": "non-ears", "text": "The system should quickly handle input."},
            {"line_number": 3, "pattern": "non-ears", "text": "The system may adequately process data."},
            {"line_number": 4, "pattern": "ubiquitous", "text": "The system shall respond."},
        ],
        "wiring_gate_warnings": [
            "wiring_gate_warning: FR01 has no consumer",
            "seam_schema_warning: FR02 seam expired",
        ],
        "cache": {
            "hit": False,
            "key": "abc123",
            "storage_version": 2,
            "miss_reason": "absent",
            "degraded": False,
            "content_hash": "sha256:deadbeef",
            "config_hash": "sha256:cafef00d",
            "validator_version": "v1",
        },
    }
    return cast("ValidateResultDict", payload)


def test_compact_groups_smell_findings_by_category() -> None:
    result = compact_validate_payload(_full_payload())
    smells = cast("list[dict[str, Any]]", result["smell_findings"])
    by_cat = {g["category"]: g for g in smells}
    assert set(by_cat) == {"weak_modal", "vague_adverb"}
    weak = by_cat["weak_modal"]
    assert weak["count"] == 2
    # Suggestion is emitted once per category (not repeated per occurrence).
    assert weak["suggestion"] == "Use 'shall'."
    # A single warning hit escalates the category severity to "warning".
    assert weak["severity"] == "warning"
    assert weak["sample_lines"] == [2, 3]
    # Per-occurrence noise is dropped in compact mode.
    assert "matched_text" not in weak


def test_compact_smell_sample_lines_capped_at_five() -> None:
    findings = [
        {"category": "weak_modal", "matched_text": "should", "line_number": n, "severity": "warning", "suggestion": "x"}
        for n in range(1, 12)
    ]
    payload = cast("ValidateResultDict", {"smell_findings": findings})
    result = compact_validate_payload(payload)
    group = cast("list[dict[str, Any]]", result["smell_findings"])[0]
    assert group["count"] == 11
    assert group["sample_lines"] == [1, 2, 3, 4, 5]


def test_compact_ears_returns_counts_and_actionable_lines() -> None:
    result = compact_validate_payload(_full_payload())
    ears = cast("dict[str, Any]", result["ears_classifications"])
    assert ears["counts"] == {"non-ears": 2, "ubiquitous": 1}
    # Only actionable (non-ears/complex) patterns contribute line numbers.
    assert ears["actionable_lines"] == [2, 3]
    # Text excerpts are dropped (caller already has the PRD on disk).
    assert "text" not in json.dumps(ears)


def test_compact_ears_actionable_lines_capped_at_ten() -> None:
    classifications = [{"line_number": n, "pattern": "complex", "text": "t"} for n in range(1, 20)]
    payload = cast("ValidateResultDict", {"ears_classifications": classifications})
    ears = cast("dict[str, Any]", compact_validate_payload(payload)["ears_classifications"])
    assert ears["counts"] == {"complex": 19}
    assert ears["actionable_lines"] == list(range(1, 11))


def test_compact_drops_cache_addressing_hashes() -> None:
    cache = cast("dict[str, Any]", compact_validate_payload(_full_payload())["cache"])
    for dropped in ("key", "content_hash", "config_hash"):
        assert dropped not in cache
    # Decision-relevant cache signal is retained.
    for kept in ("hit", "degraded", "miss_reason", "storage_version", "validator_version"):
        assert kept in cache


def test_compact_dedups_wiring_warning_already_in_suggestions() -> None:
    result = compact_validate_payload(_full_payload())
    warnings = result["wiring_gate_warnings"]
    # FR01 message is echoed as an improvement_suggestion -> removed here.
    assert "wiring_gate_warning: FR01 has no consumer" not in warnings
    # A wiring warning NOT in the top-5 suggestions is preserved (FR03).
    assert "seam_schema_warning: FR02 seam expired" in warnings


def test_compact_sets_compact_flag() -> None:
    assert compact_validate_payload(_full_payload())["compact"] is True


def test_compact_is_fail_open_on_bad_input() -> None:
    # A non-list smell_findings must not raise; the payload is returned as-is.
    payload = cast("ValidateResultDict", {"smell_findings": "not-a-list", "cache": None})
    result = compact_validate_payload(payload)
    assert result is payload


def test_compact_reduces_token_footprint_vs_verbose() -> None:
    verbose = _full_payload()
    verbose_size = len(json.dumps(verbose, default=str))
    compact = compact_validate_payload(_full_payload())
    compact_size = len(json.dumps(compact, default=str))
    assert compact_size < verbose_size
