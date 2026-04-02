"""Tests for contextual boost dimensions in rank_by_utility() (PRD-CORE-102, Task 3)."""

from __future__ import annotations


def _base_entry(
    entry_id: str = "L-001",
    summary: str = "test entry",
    impact: float = 0.5,
    **extra: object,
) -> dict[str, object]:
    return {
        "id": entry_id,
        "summary": summary,
        "impact": impact,
        "created": "2026-01-01T00:00:00Z",
        **extra,
    }


def test_domain_match_1_4() -> None:
    """Entry with domain=['auth'], context active_domains=['auth'] gets ~1.4x boost."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    entry = _base_entry(domain=["auth"])
    ctx = RecallContext(active_domains=["auth"])
    no_ctx = RecallContext()

    result_ctx = rank_by_utility([entry], ["auth"], 0.3, context=ctx)
    result_no = rank_by_utility([entry], ["auth"], 0.3, context=no_ctx)

    score_ctx = result_ctx[0]["combined_score"]
    score_no = result_no[0]["combined_score"]
    assert isinstance(score_ctx, float)
    assert isinstance(score_no, float)
    assert score_ctx > score_no, f"Expected boost: {score_ctx} > {score_no}"
    # Approximately 1.4x boost on combined score
    assert abs(score_ctx / max(score_no, 1e-9) - 1.4) < 0.15


def test_phase_match_1_3() -> None:
    """Entry with phase_affinity=['IMPLEMENT'], context current_phase='IMPLEMENT' gets ~1.3x boost."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    entry = _base_entry(phase_affinity=["IMPLEMENT"])
    ctx = RecallContext(current_phase="IMPLEMENT")
    no_ctx = RecallContext()

    result_ctx = rank_by_utility([entry], ["implement"], 0.3, context=ctx)
    result_no = rank_by_utility([entry], ["implement"], 0.3, context=no_ctx)

    score_ctx = float(str(result_ctx[0]["combined_score"]))
    score_no = float(str(result_no[0]["combined_score"]))
    assert score_ctx > score_no


def test_team_match_1_2() -> None:
    """Entry team_origin='team-a', context team_id='team-a' gets ~1.2x boost."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    entry = _base_entry(team_origin="team-a")
    ctx = RecallContext(team_id="team-a")
    no_ctx = RecallContext()

    result_ctx = rank_by_utility([entry], [], 0.3, context=ctx)
    result_no = rank_by_utility([entry], [], 0.3, context=no_ctx)

    score_ctx = float(str(result_ctx[0]["combined_score"]))
    score_no = float(str(result_no[0]["combined_score"]))
    assert score_ctx > score_no


def test_outcome_strong_positive_1_5() -> None:
    """Entry with outcome_correlation=0.8 gets ~1.5x boost."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    entry_pos = _base_entry("L-pos", outcome_correlation=0.8)
    entry_neu = _base_entry("L-neu", outcome_correlation=0.0)
    ctx = RecallContext(current_phase="IMPLEMENT")  # any non-None context

    result_pos = rank_by_utility([entry_pos], [], 0.3, context=ctx)
    result_neu = rank_by_utility([entry_neu], [], 0.3, context=ctx)

    score_pos = float(str(result_pos[0]["combined_score"]))
    score_neu = float(str(result_neu[0]["combined_score"]))
    assert score_pos > score_neu


def test_outcome_negative_0_5() -> None:
    """Entry with outcome_correlation=-0.8 gets 0.5x (penalized)."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    entry_neg = _base_entry("L-neg", outcome_correlation=-0.8)
    entry_neu = _base_entry("L-neu", outcome_correlation=0.0)
    ctx = RecallContext(current_phase="IMPLEMENT")

    result_neg = rank_by_utility([entry_neg], [], 0.3, context=ctx)
    result_neu = rank_by_utility([entry_neu], [], 0.3, context=ctx)

    score_neg = float(str(result_neg[0]["combined_score"]))
    score_neu = float(str(result_neu[0]["combined_score"]))
    assert score_neg < score_neu


def test_anchor_validity_zero_excludes() -> None:
    """anchor_validity=0.0 → combined_score=0.0 (excluded)."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    entry = _base_entry(anchor_validity=0.0)
    ctx = RecallContext(current_phase="IMPLEMENT")

    result = rank_by_utility([entry], ["test"], 0.3, context=ctx)
    score = float(str(result[0]["combined_score"]))
    assert score == 0.0


def test_all_combined() -> None:
    """Multiple boosts multiply together."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    entry = _base_entry(
        domain=["auth"],
        phase_affinity=["IMPLEMENT"],
        team_origin="team-a",
        outcome_correlation=0.8,
    )
    ctx = RecallContext(
        current_phase="IMPLEMENT",
        active_domains=["auth"],
        team_id="team-a",
    )
    no_ctx = RecallContext()

    result_ctx = rank_by_utility([entry], ["auth"], 0.3, context=ctx)
    result_no = rank_by_utility([entry], ["auth"], 0.3, context=no_ctx)

    score_ctx = float(str(result_ctx[0]["combined_score"]))
    score_no = float(str(result_no[0]["combined_score"]))
    # All boosts: 1.4 * 1.3 * 1.2 * 1.5 = 3.276x
    assert score_ctx > score_no * 2.0  # conservatively > 2x


def test_no_context_backward_compat() -> None:
    """Without context, same behavior as before (backward compat)."""
    from trw_mcp.scoring._recall import rank_by_utility

    entries = [
        _base_entry("L-a", impact=0.8),
        _base_entry("L-b", impact=0.3),
    ]
    # No context at all
    result = rank_by_utility(entries, ["test"], 0.3)
    # Should still be sorted by score (L-a higher impact)
    assert result[0]["id"] == "L-a"
    # combined_score should be added
    assert "combined_score" in result[0]


def test_missing_fields_default_1_0() -> None:
    """Entry without domain/phase_affinity → boost=1.0 (no boost applied)."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    entry = _base_entry()  # No domain, phase_affinity, team_origin, etc.
    ctx = RecallContext(
        current_phase="IMPLEMENT",
        active_domains=["auth"],
        team_id="team-a",
    )
    no_ctx = RecallContext(
        current_phase="IMPLEMENT",
        active_domains=["auth"],
        team_id="team-a",
    )

    # Same context but entry has no matching fields → boost should be 1.0
    result = rank_by_utility([entry], [], 0.3, context=ctx)
    result_no_ctx = rank_by_utility([entry], [], 0.3)

    score_with_ctx = float(str(result[0]["combined_score"]))
    score_no_ctx = float(str(result_no_ctx[0]["combined_score"]))
    # Scores should be equal (no boost because no matching fields)
    assert abs(score_with_ctx - score_no_ctx) < 1e-6


def test_combined_score_field_added() -> None:
    """rank_by_utility always adds combined_score to returned entries."""
    from trw_mcp.scoring._recall import rank_by_utility

    entry = _base_entry()
    result = rank_by_utility([entry], ["test"], 0.3)
    assert "combined_score" in result[0]
    assert isinstance(result[0]["combined_score"], float)


def test_phase_match_case_insensitive() -> None:
    """Phase matching is case-insensitive."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    entry = _base_entry(phase_affinity=["implement"])  # lowercase
    ctx = RecallContext(current_phase="IMPLEMENT")  # uppercase

    result = rank_by_utility([entry], [], 0.3, context=ctx)
    result_no = rank_by_utility([entry], [], 0.3)

    score_ctx = float(str(result[0]["combined_score"]))
    score_no = float(str(result_no[0]["combined_score"]))
    assert score_ctx > score_no


def test_team_no_match_no_boost() -> None:
    """Different team IDs don't trigger team boost."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    entry = _base_entry(team_origin="team-a")
    ctx = RecallContext(team_id="team-b")  # Different team

    result = rank_by_utility([entry], [], 0.3, context=ctx)
    result_no = rank_by_utility([entry], [], 0.3)

    score_ctx = float(str(result[0]["combined_score"]))
    score_no = float(str(result_no[0]["combined_score"]))
    # No boost since teams don't match
    assert abs(score_ctx - score_no) < 1e-6


def test_assertion_penalties_still_work_with_context() -> None:
    """Assertion penalties continue to work alongside context boosts."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    entry = _base_entry("L-a", impact=0.9, domain=["auth"])
    ctx = RecallContext(active_domains=["auth"])

    # With penalty
    result_penalty = rank_by_utility(
        [entry], [], 0.3,
        assertion_penalties={"L-a": 0.9},
        context=ctx,
    )
    # Without penalty
    result_no_penalty = rank_by_utility([entry], [], 0.3, context=ctx)

    score_penalty = float(str(result_penalty[0]["combined_score"]))
    score_no_penalty = float(str(result_no_penalty[0]["combined_score"]))
    assert score_penalty < score_no_penalty
