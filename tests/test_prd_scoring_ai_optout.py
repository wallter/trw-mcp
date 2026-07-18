"""Non-AI opt-out for the AI/agentic PRD classifier (feedback sub_5qbmT6WPNoP58rlv item 7).

The heuristic ``_is_ai_agentic_prd`` trips on as few as two incidental keyword
hits (e.g. a PRD that merely mentions "prompt" and "inference"), which then
imposes AI-operational-evidence weighting on a PRD that is not AI-operational.
A PRD author can short-circuit this by declaring ``ai_operational: false`` in
the frontmatter. A real AI PRD (no opt-out) must still classify as AI.
"""

from __future__ import annotations

from trw_mcp.state.validation._prd_scoring_ai import _is_ai_agentic_prd

# Body that incidentally trips the heuristic (>=2 keyword hits: prompt, inference, ai).
_INCIDENTAL_AI_BODY = (
    "The tooling reads a prompt template and runs inference on the CLI. "
    "This AI-adjacent phrasing is incidental to a plumbing change."
)


def test_incidental_keywords_classify_as_ai_without_opt_out() -> None:
    """Baseline: the heuristic DOES trip on two incidental keyword hits."""
    assert _is_ai_agentic_prd({"category": "INFRA"}, _INCIDENTAL_AI_BODY) is True


def test_ai_operational_false_bool_opts_out() -> None:
    """An explicit ``ai_operational: false`` (bool) short-circuits to non-AI."""
    frontmatter = {"category": "INFRA", "ai_operational": False}
    assert _is_ai_agentic_prd(frontmatter, _INCIDENTAL_AI_BODY) is False


def test_ai_operational_false_string_opts_out() -> None:
    """String false-y values (e.g. YAML-as-str) also opt out."""
    for raw in ("false", "False", "no", "off", "0"):
        frontmatter = {"category": "INFRA", "ai_operational": raw}
        assert _is_ai_agentic_prd(frontmatter, _INCIDENTAL_AI_BODY) is False, raw


def test_ai_operational_true_does_not_opt_out() -> None:
    """A truthy value leaves the heuristic in force (backward compatible)."""
    frontmatter = {"category": "INFRA", "ai_operational": True}
    assert _is_ai_agentic_prd(frontmatter, _INCIDENTAL_AI_BODY) is True


def test_missing_key_leaves_heuristic_untouched() -> None:
    """Absent key => unchanged behavior."""
    assert _is_ai_agentic_prd({"category": "INFRA"}, _INCIDENTAL_AI_BODY) is True


def test_real_ai_prd_still_classified_despite_no_opt_out() -> None:
    """A genuine AI PRD (AI title + operational headings) still classifies as
    AI when it does NOT opt out — the opt-out must not weaken real detection."""
    frontmatter = {"category": "CORE", "title": "LLM agentic evaluation harness"}
    content = "Evaluation Plan\nRelease Gate\nMonitoring Plan\n"
    assert _is_ai_agentic_prd(frontmatter, content) is True


def test_real_ai_prd_can_still_be_opted_out() -> None:
    """Even a keyword-rich PRD honors an explicit opt-out (author's call)."""
    frontmatter = {
        "category": "CORE",
        "title": "LLM agentic evaluation harness",
        "ai_operational": False,
    }
    content = "Evaluation Plan\nRelease Gate\nMonitoring Plan\n"
    assert _is_ai_agentic_prd(frontmatter, content) is False
