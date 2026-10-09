"""The trw-mcp copies of two trw-distill records keep up with fields trw-distill 0.12.0 added."""

from __future__ import annotations

from trw_mcp.tools._before_edit_hint_core import BeforeYouEditHintPayload
from trw_mcp.tools.codebase_risk_report import FileRiskScorePayload

_HINT = {
    "target_path": "foo.py",
    "target_exists_in_map": True,
    "importers": ["a.py", "b.py"],
    "inferred_tests": ["tests/test_foo.py"],
    "doc_references": [],
    "co_change_neighbors": [],
}

_RISK = {
    "target_path": "foo.py",
    "target_exists_in_map": True,
    "composite_score": 0.5,
    "fanin_score": 0.1,
    "fanout_score": 0.1,
    "untested_score": 0.1,
    "undocumented_score": 0.1,
    "size_score": 0.1,
    "fanin_count": 2,
    "fanout_count": 1,
    "test_edge_count": 1,
    "doc_edge_count": 0,
    "line_count": 10,
}


def test_a_cut_list_reports_its_total_and_an_uncut_or_older_one_does_not() -> None:
    """`importers_total` says how many there are behind a capped list; it is output only when it says something."""
    cut = BeforeYouEditHintPayload.model_validate({**_HINT, "importers_total": 47, "inferred_tests_total": 1})
    older = BeforeYouEditHintPayload.model_validate(_HINT)  # written before trw-distill 0.12.0: no totals at all

    assert cut.importers_total == 47
    dumped = cut.model_dump()
    assert dumped["importers_total"] == 47
    assert "inferred_tests_total" not in dumped  # equal to the list: nothing was cut
    assert not [key for key in older.model_dump() if key.endswith("_total")]  # never a made-up zero beside two entries


def test_a_risk_score_with_tier_counts_is_accepted() -> None:
    """The record refuses unknown fields; without this one every current risk report read as malformed."""
    score = FileRiskScorePayload.model_validate({**_RISK, "test_tier_counts": {"import_confirmed": 1}})

    assert score.test_tier_counts == {"import_confirmed": 1}
    assert FileRiskScorePayload.model_validate(_RISK).test_tier_counts == {}
