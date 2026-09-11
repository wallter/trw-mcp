"""Decision-time startup must not reduce a matched learning to its title alone."""

from datetime import datetime, timezone

import pytest
from trw_memory.models.memory import MemoryEntry

from tests._tools_learning_shared import set_project_root  # noqa: F401
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.memory_adapter import get_backend
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._session_recall_helpers import perform_session_recalls


@pytest.mark.parametrize("query", ["*", "retention"])
def test_startup_preserves_short_actionable_learning_body(tmp_path, query):
    trw_dir = tmp_path / ".trw"
    backend = get_backend(trw_dir)
    body = "Preserve the original retention timestamp when retrying; a retry must not extend expiry."
    backend.store(
        MemoryEntry(
            id="L-retention-body",
            content="Retention decision",
            detail=body,
            importance=0.5,
            created_at=datetime.now(timezone.utc),
        )
    )
    config = TRWConfig(recall_max_results=1, session_start_recent_bypass_days=3)
    rows, _, _ = perform_session_recalls(trw_dir, query, config, FileStateReader())
    assert [row["id"] for row in rows] == ["L-retention-body"]
    assert body in rows[0].get("detail", "")
    assert backend.get("L-retention-body", namespace="default").detail == body


def test_fair_unicode_body_allowance_and_zero_share():
    from trw_mcp.tools._session_recall_content import carry_focused_content, project_focused_content
    from trw_mcp.tools._session_recall_helpers import _SESSION_START_COMPACT_FIELDS

    bodies = ["短" * 10, "🌱" * 5000, "é" * 5000]
    rows = [{"id": str(i), "summary": "claim", "detail": body} for i, body in enumerate(bodies)]
    result = project_focused_content(carry_focused_content(rows), _SESSION_START_COMPACT_FIELDS)
    assert [len(row["detail"]) for row in result] == [10, 1356, 682]
    assert [row["detail_truncated"] for row in result] == [False, True, True]
    assert [row["id"] for row in result] == ["0", "1", "2"]
    assert sum(len(row["detail"]) for row in result) == 2048
    assert all(row["detail"] == body[: len(row["detail"])] for row, body in zip(result, bodies, strict=True))
    crowded = carry_focused_content([{"id": str(i), "detail": "xx"} for i in range(2049)])
    result = project_focused_content(crowded, _SESSION_START_COMPACT_FIELDS)
    assert result[-1]["detail"] == ""
    assert result[-1]["detail_truncated"] is True
    assert sum(len(row["detail"]) for row in result) == 2048


@pytest.mark.parametrize("pressure", [False, True])
def test_actual_focused_projection_pressure_trim_and_single_acquisition(tmp_path, monkeypatch, pressure):
    from unittest.mock import Mock

    from trw_memory.models.memory import Assertion, AssertionType

    from trw_mcp.state import recall_factories
    from trw_mcp.tools._session_start_trim import trim_session_start_payload

    trw_dir = tmp_path / ".trw"
    backend = get_backend(trw_dir)
    now = datetime.now(timezone.utc)
    for index, body in enumerate(["Short actionable body", "🌱" * 10000]):
        backend.store(
            MemoryEntry(
                id=f"L-body-{index}",
                content=f"Retention policy {index}",
                detail=body,
                importance=0.8,
                created_at=now,
                updated_at=now,
                assertions=[
                    Assertion(
                        type=AssertionType.GREP_PRESENT,
                        pattern="x",
                        target="*.py",
                        last_result=True,
                        last_verified_at=now,
                    )
                ],
            )
        )
    actual = recall_factories._default_recall()
    spy = Mock(wraps=actual)
    monkeypatch.setattr(recall_factories, "_default_recall", lambda: spy)
    monkeypatch.setattr("trw_mcp.tools._session_recall_helpers._session_start_defers", lambda *args: pressure)
    rows, _, extra = perform_session_recalls(trw_dir, "retention", TRWConfig(recall_max_results=2), FileStateReader())
    assert spy.call_count == 2  # one focused query plus the existing baseline, no refetch
    assert [(call.kwargs["query"], call.kwargs["compact"]) for call in spy.call_args_list] == [
        ("retention", False),
        ("*", True),
    ]
    assert len(rows) == 2  # focused/baseline duplicates retain the focused body
    assert sum(len(row["detail"]) for row in rows) == 2048
    assert all(row["verification_evidence"]["observation"] == "pass" for row in rows)
    assert all(row["verification_evidence"]["current_tree_verified"] is False for row in rows)
    assert all("q_value" not in row and "_session_focused_detail" not in row for row in rows)
    assert next(row for row in rows if row["id"] == "L-body-0")["detail"] == "Short actionable body"
    assert extra.get("response_compacted", False) is pressure
    for verbose in [False, True]:
        payload = {"learnings": rows, "success": True, "errors": []}
        trimmed = trim_session_start_payload(payload, verbose=verbose)
        assert trimmed["learnings"] == rows
    assert backend.get("L-body-1", namespace="default").detail == "🌱" * 10000


def test_query_miss_baseline_unchanged(tmp_path):
    trw_dir = tmp_path / ".trw"
    get_backend(trw_dir).store(
        MemoryEntry(
            id="L-baseline",
            content="General baseline",
            detail="Not a query match",
            importance=0.8,
            created_at=datetime.now(timezone.utc),
        )
    )
    rows, _, extra = perform_session_recalls(
        trw_dir, "zzzz-no-match", TRWConfig(recall_max_results=1), FileStateReader()
    )
    assert [row["id"] for row in rows] == ["L-baseline"]
    assert "detail" not in rows[0]
    assert extra["query_matched"] == 0
    assert extra["query_advisory"]


def test_full_body_does_not_change_compact_scoring_inputs():
    from trw_memory.models.memory import Assertion, AssertionType

    from trw_mcp.scoring import rank_by_utility
    from trw_mcp.state._memory_transforms import _memory_to_learning_dict
    from trw_mcp.tools._recall_assertion_verification import _verify_assertions
    from trw_mcp.tools._session_recall_content import carry_focused_content

    now = datetime.now(timezone.utc)
    entries = [
        MemoryEntry(
            id=str(i),
            content="Same summary",
            detail="retention " * (i * 100),
            importance=0.8,
            tags=[str(tag) for tag in range(20)],
            created_at=now,
            updated_at=now,
            q_value=float(i),
            access_count=i * 100,
            assertions=[
                Assertion(
                    type=AssertionType.GREP_PRESENT,
                    pattern="x",
                    target="*.py",
                    last_result=bool(i),
                    last_verified_at=now,
                )
            ],
        )
        for i in range(2)
    ]
    old = [dict(_memory_to_learning_dict(entry, compact=True)) for entry in entries]
    new = carry_focused_content([dict(_memory_to_learning_dict(entry)) for entry in entries])
    for before, after in zip(old, new, strict=True):
        comparable = {k: v for k, v in after.items() if k != "_session_focused_detail"}
        # Full and compact owner projections encode dates as datetime vs ISO.
        comparable["assertions"] = [
            Assertion.model_validate(row).model_dump(mode="json") for row in comparable["assertions"]
        ]
        assert comparable == before
    config = TRWConfig()
    expected = _verify_assertions(old, ["retention"], config, rank_by_utility)
    actual = _verify_assertions(new, ["retention"], config, rank_by_utility)
    assert [(row["id"], row["combined_score"]) for row in actual] == [
        (row["id"], row["combined_score"]) for row in expected
    ]
    assert actual[0]["verification_evidence"]["observation"] == "pass"
