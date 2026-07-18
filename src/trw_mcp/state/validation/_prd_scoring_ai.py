"""PRD scoring — AI/agentic detection + operational evidence scoring (PRD-QUAL-055).

Belongs to the ``_prd_scoring.py`` facade. Re-exported there for back-compat.

Two helpers + 2 constants:
- ``_AI_KEYWORD_RE``, ``_AI_OPERATIONAL_HEADINGS`` — detection terms
- ``_is_ai_agentic_prd`` — heuristic AI/LLM/agentic PRD detector
- ``_score_ai_operational_evidence`` — Evaluation/Release/Monitoring evidence
  scoring (returns 3-tuple of [0, 1] floats)

Extracted as DIST-243 batch 55.

Non-AI opt-out (feedback sub_5qbmT6WPNoP58rlv item 7): the heuristic trips on
as few as two incidental keyword hits (e.g. a PRD that merely mentions
"prompt" and "inference"). A PRD author can short-circuit the classification —
and therefore the extra AI-operational-evidence weighting/requirements — by
declaring ``ai_operational: false`` in the frontmatter. Any explicit false-y
value (bool ``false`` or the strings 'false'/'no'/'off') forces
``_is_ai_agentic_prd`` to return ``False`` regardless of body keywords. Absent
or truthy values leave the heuristic untouched (backward compatible).
"""

from __future__ import annotations

import re

# Explicit false-y frontmatter values for the ``ai_operational`` opt-out key.
_AI_OPT_OUT_VALUES: frozenset[str] = frozenset({"false", "no", "off", "0"})

# AI/Agentic detection keywords (PRD-QUAL-055). Keep these boundary-aware to
# avoid false positives from ordinary words like "maintainers".
_AI_KEYWORD_RE = re.compile(
    r"\b(?:ai|llm|agentic|generative|prompt(?:ing)?|inference|foundation model|language model)\b",
    re.IGNORECASE,
)
_AI_OPERATIONAL_HEADINGS = (
    "Data / Context Provenance",
    "Failure Modes",
    "Safe Degradation",
    "Human Oversight",
    "Escalation",
    "Evaluation Plan",
    "Release Gate",
    "Monitoring Plan",
    "Risk Register",
    "Failure Class",
)


def _ai_operational_opt_out(frontmatter: dict[str, object]) -> bool:
    """Return True when the PRD explicitly declares ``ai_operational: false``.

    Only an explicit false-y value opts out; a missing key or a truthy value
    leaves the heuristic in force (backward compatible).
    """
    value = frontmatter.get("ai_operational")
    if value is False:
        return True
    return isinstance(value, str) and value.strip().lower() in _AI_OPT_OUT_VALUES


def _is_ai_agentic_prd(frontmatter: dict[str, object], content: str) -> bool:
    """Heuristic detection of AI/LLM/agentic PRDs.

    Honors an explicit ``ai_operational: false`` frontmatter opt-out
    (feedback sub_5qbmT6WPNoP58rlv item 7) that short-circuits the keyword
    heuristic before any body scanning.
    """
    if _ai_operational_opt_out(frontmatter):
        return False
    category = str(frontmatter.get("category", "")).upper()
    title = str(frontmatter.get("title", ""))
    title_keyword_match = _AI_KEYWORD_RE.search(title) is not None
    body_keyword_matches = {match.group(0).lower() for match in _AI_KEYWORD_RE.finditer(content)}
    operational_heading_match = any(heading in content for heading in _AI_OPERATIONAL_HEADINGS)
    if operational_heading_match or title_keyword_match:
        return True
    if category == "QUAL":
        return bool(body_keyword_matches)
    return len(body_keyword_matches) >= 2


def _score_ai_operational_evidence(content: str) -> tuple[float, float, float]:
    """Return evaluation, release, and monitoring evidence scores (each in [0, 1])."""
    ai_evaluation_score = 0.0
    ai_release_score = 0.0
    ai_monitoring_score = 0.0
    if "Evaluation Plan" in content:
        eval_section = content.split("Evaluation Plan")[-1].lower()
        eval_keywords = [
            "baseline",
            "criteria",
            "threshold",
            "accuracy",
            "latency",
            "reliability",
            "A/B",
            "test",
            "user study",
            "metric",
        ]
        ai_evaluation_score = min(sum(1 for kw in eval_keywords if kw in eval_section) / len(eval_keywords), 1.0)
    if "Release Gate" in content:
        release_section = content.split("Release Gate")[-1].lower()
        release_keywords = [
            "canary",
            "phased",
            "rollback",
            "trigger",
            "threshold",
            "error rate",
            "latency",
            "confidence",
        ]
        ai_release_score = min(sum(1 for kw in release_keywords if kw in release_section) / len(release_keywords), 1.0)
    if "Monitoring Plan" in content:
        monitoring_section = content.split("Monitoring Plan")[-1].lower()
        monitoring_keywords = [
            "primary signal",
            "target threshold",
            "escalation",
            "alert",
            "drift",
            "latency",
            "error rate",
            "trust",
        ]
        ai_monitoring_score = min(
            sum(1 for kw in monitoring_keywords if kw in monitoring_section) / len(monitoring_keywords),
            1.0,
        )
    return ai_evaluation_score, ai_release_score, ai_monitoring_score
