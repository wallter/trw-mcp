"""Recall ranking does not use backend bandit weights (trw-mcp 6.1.0).

trw-mcp 6.0.0 shipped "recall ranking no longer uses reward feedback", but
``rank_targeted_by_utility`` still multiplied every score by the backend
``bandit_params`` weight cached in the intelligence cache. These tests pin the
shipped claim: a context carrying bandit weights ranks and scores exactly like
the same context without them, for targeted and wildcard recall.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trw_mcp.scoring._recall import RecallContext, rank_targeted_by_utility


class _StaticIntelCache:
    def __init__(self, params: dict[str, float] | None) -> None:
        self._params = params

    def get_bandit_params(self) -> dict[str, float] | None:
        return self._params


def _entry(entry_id: str, summary: str, impact: float) -> dict[str, object]:
    return {
        "id": entry_id,
        "summary": summary,
        "detail": "",
        "tags": ["test"],
        "impact": impact,
        "type": "pattern",
        "status": "active",
        "created": datetime.now(tz=timezone.utc).isoformat(),
        "domain": ["auth"],
        "phase_affinity": [],
        "team_origin": "",
        "anchor_validity": 1.0,
    }


# L-low has the lowest impact, so without bandit weights it ranks last on
# utility; the weights would lift it to the top and sink L-high.
_ENTRIES = [
    _entry("L-high", "auth token refresh", 0.9),
    _entry("L-mid", "auth token refresh", 0.6),
    _entry("L-low", "auth token refresh", 0.3),
]
_BANDIT_PARAMS = {"L-low": 2.0, "L-high": 0.5}


def _ranked(query_tokens: list[str], intel_cache: _StaticIntelCache | None) -> list[tuple[object, ...]]:
    context = RecallContext(inferred_domains={"auth"}, intel_cache=intel_cache)
    ranked = rank_targeted_by_utility([dict(e) for e in _ENTRIES], query_tokens, 0.3, context=context)
    return [(e["id"], e["combined_score"], e.get("preference_score")) for e in ranked]


@pytest.mark.parametrize("query_tokens", [["auth", "token"], []], ids=["targeted", "wildcard"])
def test_bandit_params_do_not_change_ranking_or_scores(query_tokens: list[str]) -> None:
    without = _ranked(query_tokens, None)
    with_weights = _ranked(query_tokens, _StaticIntelCache(_BANDIT_PARAMS))
    assert with_weights == without
    assert [row[0] for row in without] == ["L-high", "L-mid", "L-low"]


@pytest.mark.parametrize("query_tokens", [["auth", "token"], []], ids=["targeted", "wildcard"])
def test_empty_bandit_params_are_still_neutral(query_tokens: list[str]) -> None:
    assert _ranked(query_tokens, _StaticIntelCache({})) == _ranked(query_tokens, None)
