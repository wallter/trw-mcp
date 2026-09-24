"""PRD-CORE-294 FR01 / NFR02: bounded default recall.

Drives the REAL registered ``trw_recall`` over a real project store: the
default response is stubs within 3,000 bytes, ``ids=`` returns full rows, and
the deleted shaping options are refused.

PRD-CORE-280 slice e (batch 23b): ported off ``get_backend`` onto
``daemon_checkout``. The row count dropped from 1,200 to 60 -- through the
daemon each store costs ~230 ms on the write path, so 1,200 rows would blow
the test timeout; 60 rows already exceeds ``recall_max_results`` (25, see
``_defaults.py``) and the 3,000-byte stub budget, which is all these
assertions need (they check shape and omission, not a specific count).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from hypothesis import given, settings
from hypothesis import strategies as st
from trw_memory.models.memory import Anchor

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.tools._recall_presenter import CLAIM_MAX_CHARS, RECALL_BYTE_BUDGET, claim, present, stub

_STORE_ROWS = 60
_LONG = "sqlite migration gotcha " + "the driver must be pinned before the schema moves " * 8


@pytest.fixture()
def large_project(daemon_checkout: DaemonCheckout) -> Path:
    from tests._path_isolation import set_current_root
    from trw_mcp.state.memory_adapter import store_learning

    set_current_root(daemon_checkout.trw_dir.parent)
    for index in range(_STORE_ROWS):
        store_learning(
            daemon_checkout.trw_dir,
            f"L-row{index:04d}",
            f"{_LONG} variant {index}",
            "full detail only on request " * 20,
            tags=["sqlite", "migration"],
            impact=0.5,
            anchors=[
                Anchor(file="docs/notes.md", symbol_name="intro").model_dump(mode="json"),
                Anchor(file="src/sqlite_driver.py", symbol_name="connect").model_dump(mode="json"),
            ],
        )
    return daemon_checkout.trw_dir.parent


def _recall() -> object:
    from tests.conftest import extract_tool_fn, make_test_server

    return extract_tool_fn(make_test_server("learning"), "trw_recall")


def test_default_recall_over_a_large_store_is_stubs_within_the_byte_budget(large_project: Path) -> None:
    result = _recall()(query="sqlite migration")

    assert len(json.dumps(result, default=str).encode()) <= RECALL_BYTE_BUDGET
    rows = result["learnings"]
    assert rows
    for row in rows:
        assert set(row) == {"id", "claim", "anchor"}
        assert len(row["claim"]) <= CLAIM_MAX_CHARS
        assert "\n" not in row["claim"]
        # The anchor whose path matches a query token wins over the first one.
        assert row["anchor"] == "src/sqlite_driver.py:connect"
    assert result["omitted"] > 0
    assert result["total_matches"] == len(rows) + result["omitted"]
    removed = {"patterns", "context", "compact", "candidate_count", "store_count", "duplicates_collapsed"}
    assert not removed & set(result)
    assert not [key for key in result if key.startswith("tokens_")]


def test_ids_return_full_rows_and_name_the_missing(large_project: Path) -> None:
    result = _recall()(query="ignored entirely", ids=["L-row0001", "L-row0002", "L-missing"])

    assert [row["id"] for row in result["learnings"]] == ["L-row0001", "L-row0002"]
    assert all(row["detail"].startswith("full detail only on request") for row in result["learnings"])
    # Full rows carry the same qualified evidence as ranked recall: stored, never present-tree proof.
    assert all(row["verification_status"] == "unknown" for row in result["learnings"])
    assert not {"q_value", "outcome_history"} & set(result["learnings"][0])
    assert result["missing_ids"] == ["L-missing"]


@pytest.mark.parametrize("option", ["compact", "ultra_compact", "token_budget"])
def test_deleted_shaping_options_are_refused(large_project: Path, option: str) -> None:
    with pytest.raises(ToolError, match="Accepted keys"):
        _recall()(query="sqlite", options={option: 1})


def test_claim_is_one_line_cut_with_an_ellipsis() -> None:
    long = "first line\n" + "x" * 400

    cut = claim(long)

    assert len(cut) == CLAIM_MAX_CHARS
    assert cut.startswith("first line x")
    assert cut.endswith("…")
    assert claim("  short\n claim ") == "short claim"


def test_anchor_falls_back_to_the_first_and_is_absent_without_anchors() -> None:
    row = {"id": "L-a", "summary": "s", "anchors": [{"file": "a.py"}, {"file": "b.py", "symbol_name": "f"}]}

    assert stub(row, ["nomatch"])["anchor"] == "a.py"
    assert stub(row, ["b.py"])["anchor"] == "b.py:f"
    assert "anchor" not in stub({"id": "L-b", "summary": "s"})


def test_the_first_stub_is_kept_by_cutting_the_long_strings_around_it() -> None:
    envelope: dict[str, object] = {"query": "q" * 2_000, "topic_filter_warning": "w" * 2_000}
    rows = [{"id": f"L-{i}", "summary": "y" * 400, "anchors": [{"file": "a/" * 300}]} for i in range(3)]

    stubs = present(envelope, rows, byte_budget=400)

    assert [row["id"] for row in stubs] == ["L-0"]
    assert envelope["omitted"] == 2
    assert len(json.dumps(envelope).encode()) <= 400


def test_a_stub_whose_id_alone_overflows_is_dropped_not_sent_over_budget() -> None:
    envelope: dict[str, object] = {"query": "q"}

    stubs = present(envelope, [{"id": "L-" + "x" * 500, "summary": "s"}], byte_budget=200)

    assert stubs == []
    assert envelope["omitted"] == 1
    assert len(json.dumps(envelope).encode()) <= 200


# Repeated short text reaches the multi-kilobyte worst case Hypothesis rarely draws unaided.
_TEXT = st.builds(lambda unit, times: unit * times, st.text(max_size=8), st.integers(0, 600))


@settings(max_examples=150, deadline=None)
@given(
    query=_TEXT,
    advisories=st.dictionaries(st.sampled_from(["topic_filter_warning", "store_unavailable"]), _TEXT),
    nested=st.dictionaries(st.text(max_size=12), _TEXT, max_size=4),
    rows=st.lists(
        st.fixed_dictionaries(
            {"id": st.text(min_size=1, max_size=40), "summary": _TEXT},
            optional={"anchors": st.lists(st.fixed_dictionaries({"file": _TEXT, "symbol_name": _TEXT}), max_size=3)},
        ),
        max_size=60,
    ),
    budget=st.sampled_from([RECALL_BYTE_BUDGET, 1_500]),
)
def test_the_whole_response_never_exceeds_the_byte_budget(
    query: str, advisories: dict[str, str], nested: dict[str, str], rows: list[dict[str, object]], budget: int
) -> None:
    envelope: dict[str, object] = {"query": query, "total_matches": len(rows), **advisories}
    if nested:
        envelope["remote_recall"] = nested

    stubs = present(envelope, rows, byte_budget=budget)

    assert len(json.dumps(envelope, default=str).encode()) <= budget
    assert len(json.dumps(envelope, default=str, ensure_ascii=False).encode()) <= budget
    assert (len(stubs) >= 1) == bool(rows)
    assert [row["id"] for row in stubs] == [str(row["id"]) for row in rows[: len(stubs)]]


def test_nothing_is_omitted_when_everything_fits() -> None:
    envelope: dict[str, object] = {"query": "q"}

    present(envelope, [{"id": "L-1", "summary": "short"}])

    assert "omitted" not in envelope
