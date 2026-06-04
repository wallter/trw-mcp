"""Tests for requirement-smell detection + EARS classification (AARE-F v3.0.0 §2.4/§2.1).

These detectors populate ValidationResultV2.smell_findings / .ears_classifications,
which were always-empty stubs before this change. The wiring test at the bottom is
the load-bearing one: it proves validate_prd_quality_v2 actually surfaces findings
(behavior), not merely that the functions exist.
"""

from __future__ import annotations

from trw_mcp.state.validation._prd_scoring_smells import classify_ears, detect_smells, summarize_smells


def _categories(content: str) -> set[str]:
    return {f.category for f in detect_smells(content)}


def test_weak_modal_flagged_in_requirement_line() -> None:
    cats = _categories("FR01: The system should process the request.")
    assert "weak_modal" in cats


def test_vague_adverb_and_subjective_flagged() -> None:
    cats = _categories("FR02: The system shall quickly return a user-friendly response.")
    assert "vague_adverb" in cats
    assert "subjective" in cats


def test_escape_clause_and_open_ended_flagged() -> None:
    cats = _categories("FR03: The system shall, where possible, cache results, etc.")
    assert "escape_clause" in cats
    assert "open_ended" in cats


def test_compound_requirement_flagged() -> None:
    cats = _categories("FR04: The system shall validate input and persist it and notify the user.")
    assert "compound" in cats


def test_prose_line_without_requirement_markers_is_not_scanned() -> None:
    # No FR/NFR/checkbox/shall and does not open with an EARS keyword -> not a requirement line.
    assert detect_smells("This document should describe the approach generally.") == []


def test_smells_inside_code_fence_are_ignored() -> None:
    content = "```\nFR99: the system should quickly do X\n```\n"
    assert detect_smells(content) == []


def test_ears_classification_patterns() -> None:
    content = "\n".join(
        [
            "When a request arrives, the system shall respond.",
            "While idle, the system shall conserve power.",
            "If input is invalid, then the system shall reject it.",
            "Where caching is enabled, the system shall cache responses.",
            "The system shall log every event.",
            "While busy, when a request arrives, the system shall queue it.",
            "FR09: the system must remain available.",
        ]
    )
    by_pattern = {row["pattern"] for row in classify_ears(content)}
    assert {"event-driven", "state-driven", "unwanted-behavior", "optional-feature", "ubiquitous", "complex", "non-ears"} <= by_pattern


def test_ears_ignores_non_requirement_lines() -> None:
    # A heading / prose line with no shall/FR/must is not classified.
    assert classify_ears("## Functional Requirements\n") == []


def test_validator_populates_smell_and_ears_fields(config) -> None:
    """WIRING: validate_prd_quality_v2 must surface real findings (fields were empty stubs)."""
    from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

    content = "\n".join(
        [
            "---",
            "id: PRD-CORE-999",
            "title: Smell wiring probe",
            "version: 1.0.0",
            "status: draft",
            "priority: P2",
            "---",
            "## Functional Requirements",
            "FR01: When a request arrives, the system shall quickly return a user-friendly result.",
            "FR02: The system should, where possible, handle errors and retry and log, etc.",
        ]
    )
    result = validate_prd_quality_v2(content, config)

    assert len(result.smell_findings) > 0, "smell_findings must be populated (was an empty stub)"
    smell_cats = {f.category for f in result.smell_findings}
    assert {"weak_modal", "vague_adverb", "subjective", "escape_clause", "open_ended"} & smell_cats

    assert len(result.ears_classifications) > 0, "ears_classifications must be populated (was an empty stub)"
    patterns = {row["pattern"] for row in result.ears_classifications}
    assert "event-driven" in patterns


def test_smell_detection_does_not_change_total_score(config) -> None:
    """Detection is informational: smell weight is 0, so the score is unaffected."""
    from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

    clean = "\n".join(
        ["---", "id: PRD-CORE-998", "title: t", "version: 1.0.0", "status: draft", "priority: P2", "---", "## Functional Requirements", "FR01: The system shall persist the record."]
    )
    result = validate_prd_quality_v2(clean, config)
    # smell dimension is never added to the scored dimensions list.
    assert all(d.name != "smell_score" for d in result.dimensions)


# ---------------------------------------------------------------------------
# PRD-QUAL-092: summarize_smells helper + suggestion surfacing
# ---------------------------------------------------------------------------


def test_summarize_smells_returns_none_without_warnings() -> None:
    """No warning-severity findings -> no advisory (FR01 guard / NFR02 boundedness)."""
    # superlative/absolute are 'info' severity, not 'warning'.
    findings = detect_smells("FR01: The system shall use the best algorithm always.")
    info_only = [f for f in findings if f.severity == "warning"]
    assert info_only == []  # precondition: only info-severity smells present
    assert summarize_smells(findings) is None


def test_summarize_smells_names_categories_and_lines() -> None:
    """Advisory names the warning categories and cites line numbers (FR01)."""
    content = "\n".join(
        [
            "FR01: The system should quickly return a user-friendly result.",
            "FR02: The system shall, where possible, cache results.",
        ]
    )
    findings = detect_smells(content)
    advisory = summarize_smells(findings)
    assert advisory is not None
    assert "weak_modal" in advisory
    # at least one cited line number is present
    assert any(ch.isdigit() for ch in advisory)


def test_summarize_smells_is_bounded_to_five_lines() -> None:
    """At most ~5 cited line numbers regardless of warning count (NFR02)."""
    # 8 distinct requirement lines each with a weak_modal warning.
    content = "\n".join(f"FR{i:02d}: The system should do thing {i}." for i in range(1, 9))
    findings = detect_smells(content)
    advisory = summarize_smells(findings)
    assert advisory is not None
    # Count the distinct line-number tokens cited in the "line(s) ..." clause.
    cited = advisory.split("line(s)", 1)[1]
    import re as _re

    numbers = _re.findall(r"\d+", cited.split(".")[0])
    assert len(numbers) <= 5


def _smelly_prd() -> str:
    return "\n".join(
        [
            "---",
            "id: PRD-CORE-997",
            "title: Smell suggestion probe",
            "version: 1.0.0",
            "status: draft",
            "priority: P2",
            "---",
            "## Functional Requirements",
            "FR01: The system should quickly return a user-friendly result.",
            "FR02: The system shall, where possible, handle errors, etc.",
        ]
    )


def _clean_prd() -> str:
    # Same structure with smell terms removed (binding 'shall', measurable).
    return "\n".join(
        [
            "---",
            "id: PRD-CORE-997",
            "title: Smell suggestion probe",
            "version: 1.0.0",
            "status: draft",
            "priority: P2",
            "---",
            "## Functional Requirements",
            "FR01: The system shall return a result within 200 ms.",
            "FR02: The system shall handle the documented error codes.",
        ]
    )


def test_smell_suggestion_surfaced(config) -> None:
    """FR01: a smelly PRD surfaces exactly one smell advisory naming weak_modal + a line."""
    from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

    result = validate_prd_quality_v2(_smelly_prd(), config)

    smell_suggestions = [s for s in result.improvement_suggestions if s.dimension == "smell"]
    assert len(smell_suggestions) == 1, "exactly one bounded smell advisory expected"
    msg = smell_suggestions[0].message
    assert "weak_modal" in msg
    assert any(ch.isdigit() for ch in msg)


def test_smell_suggestion_score_invariant(config) -> None:
    """NFR01 (CRITICAL): total_score is identical with vs. without smells (weight stays 0)."""
    from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

    smelly = validate_prd_quality_v2(_smelly_prd(), config)
    clean = validate_prd_quality_v2(_clean_prd(), config)
    assert smelly.total_score == clean.total_score


def test_smell_suggestion_does_not_affect_total_score(config, monkeypatch) -> None:
    """NFR01 (CRITICAL, true before/after): neutralizing the smell advisory on
    IDENTICAL content leaves total_score unchanged.

    Unlike ``test_smell_suggestion_score_invariant`` (which compares two
    DIFFERENT fixtures), this scores ONE smelly PRD, then monkeypatches the
    ``summarize_smells`` symbol that prd_quality actually uses (imported as the
    ``_smells`` module) to a no-op, and scores the SAME content again. If the
    advisory contributed any weight, s1 != s2 — so equality proves it cannot
    move the score.
    """
    from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

    prd = _smelly_prd()
    s1 = validate_prd_quality_v2(prd, config).total_score

    monkeypatch.setattr(
        "trw_mcp.state.validation.prd_quality._smells.summarize_smells",
        lambda findings: None,
    )
    s2 = validate_prd_quality_v2(prd, config).total_score

    assert s1 == s2


def test_smell_suggestion_non_blocking(config) -> None:
    """FR03: smell advisory is advisory only -- valid and quality_tier unchanged."""
    from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

    smelly = validate_prd_quality_v2(_smelly_prd(), config)
    clean = validate_prd_quality_v2(_clean_prd(), config)
    assert smelly.valid == clean.valid
    assert smelly.quality_tier == clean.quality_tier


def test_smell_suggestion_absent_when_no_warnings(config) -> None:
    """FR01 negative: a clean PRD adds no smell advisory."""
    from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

    clean = validate_prd_quality_v2(_clean_prd(), config)
    assert [s for s in clean.improvement_suggestions if s.dimension == "smell"] == []
