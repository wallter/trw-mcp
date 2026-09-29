"""PRD-CORE-334 FR01-FR03: record a decision, then ask ``trw_recall`` for exactly one type.

Every test goes through the PUBLIC tools against the real daemon store
(``daemon_checkout``): ``trw_learn(type="decision")`` must pass the tool's own
enum guard, and ``trw_recall(options={"record_type": ...})`` must return only
rows of that type -- including one older than the listing the recall reads,
which only a filter inside the store query can find.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp.exceptions import ToolError

from tests._memory_fixtures import DaemonCheckout
from tests._path_isolation import set_current_root
from tests._tools_learning_shared import _get_tools, set_project_root  # noqa: F401

_KINDS = ("incident", "decision", "pattern", "convention", "hypothesis", "workaround")


def _seed(checkout: DaemonCheckout, entry_id: str, kind: str) -> None:
    from trw_mcp.state.memory_adapter import store_learning

    store_learning(checkout.trw_dir, entry_id, f"ledger {kind} row {entry_id}", f"detail {entry_id}", type=kind)


def _ids(result: Any) -> set[str]:
    return {str(stub["id"]) for stub in result["learnings"]}


@pytest.fixture
def tools(daemon_checkout: DaemonCheckout) -> dict[str, Any]:
    set_current_root(daemon_checkout.trw_dir.parent)
    return _get_tools()


@pytest.fixture
def mixed(daemon_checkout: DaemonCheckout, tools: dict[str, Any]) -> dict[str, str]:
    """One row of each type; the decision is written through the public ``trw_learn`` tool (FR01)."""
    ids = {}
    for kind in _KINDS:
        if kind == "decision":
            answer = tools["trw_learn"].fn(summary="ledger decision: use the daemon", detail="why", type="decision")
            assert answer["status"] == "recorded", answer
            ids[kind] = str(answer["learning_id"])
        else:
            ids[kind] = f"L-{kind}"
            _seed(daemon_checkout, ids[kind], kind)
    return ids


def test_trw_learn_records_a_decision_that_reads_back_as_one(
    daemon_checkout: DaemonCheckout, tools: dict[str, Any], mixed: dict[str, str]
) -> None:
    row = tools["trw_recall"].fn(ids=[mixed["decision"]])["learnings"][0]

    assert row["type"] == "decision"


def test_a_decision_writes_its_yaml_backup_too(tools: dict[str, Any]) -> None:
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        answer = tools["trw_learn"].fn(summary="ledger decision two", detail="why two", type="decision")

    assert answer["status"] == "recorded"
    assert [log for log in logs if log["event"] == "learn_db_write_failed"] == []


@pytest.mark.parametrize("query", ["*", "ledger"])
@pytest.mark.parametrize("kind", ["incident", "decision"])
def test_record_type_returns_only_that_type(
    tools: dict[str, Any], mixed: dict[str, str], query: str, kind: str
) -> None:
    result = tools["trw_recall"].fn(query=query, options={"record_type": kind})

    assert _ids(result) == {mixed[kind]}


def test_omitting_record_type_is_unfiltered(tools: dict[str, Any], mixed: dict[str, str]) -> None:
    assert _ids(tools["trw_recall"].fn(query="*")) == set(mixed.values())


def test_a_decision_older_than_the_listing_is_still_found(
    daemon_checkout: DaemonCheckout, tools: dict[str, Any]
) -> None:
    # max_results=1 lists the 5 newest rows; the one decision is older than 8 patterns.
    _seed(daemon_checkout, "L-old-decision", "decision")
    for index in range(8):
        _seed(daemon_checkout, f"L-new-{index}", "pattern")

    result = tools["trw_recall"].fn(query="*", max_results=1, options={"record_type": "decision"})

    assert _ids(result) == {"L-old-decision"}


@pytest.mark.parametrize("value", ["decisions", "DECISION", ""])
def test_an_invalid_record_type_is_refused_naming_the_field(tools: dict[str, Any], value: str) -> None:
    with pytest.raises(ToolError, match="record_type"):
        tools["trw_recall"].fn(query="*", options={"record_type": value})


@pytest.mark.parametrize("mode", [{"ids": ["L-x"]}, {"graph_id": "L-x"}])
def test_ids_and_graph_modes_refuse_record_type(tools: dict[str, Any], mode: dict[str, object]) -> None:
    with pytest.raises(ToolError, match="record_type"):
        tools["trw_recall"].fn(options={"record_type": "decision"}, **mode)


def test_shared_results_are_filtered_by_their_type(
    tools: dict[str, Any], mixed: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = [
        {"id": "S-decision", "summary": "[shared] ledger one", "type": "decision", "source": "shared", "impact": 0.9},
        {"id": "S-incident", "summary": "[shared] ledger two", "type": "incident", "source": "shared", "impact": 0.9},
        {"id": "S-untyped", "summary": "[shared] ledger three", "source": "shared", "impact": 0.9},
    ]
    monkeypatch.setattr(
        "trw_mcp.tools._recall_impl._augment_with_remote", lambda _query, rows: ([*rows, *shared], None)
    )

    filtered = _ids(tools["trw_recall"].fn(query="ledger", options={"record_type": "decision"}))
    unfiltered = _ids(tools["trw_recall"].fn(query="ledger"))

    assert filtered == {mixed["decision"], "S-decision"}
    assert {"S-decision", "S-incident", "S-untyped"} <= unfiltered


def test_a_daemon_refusing_the_filter_is_an_error_not_unfiltered_rows(
    tools: dict[str, Any], mixed: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # What a daemon without ``types`` answers: FastMCP refuses the unknown argument.
    from trw_memory.daemon.client import DaemonClient

    real = DaemonClient.call_tool

    async def refusing(self: DaemonClient, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if (arguments or {}).get("types"):
            raise ToolError(f"Error calling tool {name!r}: Unexpected keyword argument 'types'")
        return await real(self, name, arguments)

    monkeypatch.setattr(DaemonClient, "call_tool", refusing)

    for query in ("*", "ledger"):
        with pytest.raises(ToolError, match="types"):
            tools["trw_recall"].fn(query=query, options={"record_type": "decision"})
    assert _ids(tools["trw_recall"].fn(query="*")) == set(mixed.values())
