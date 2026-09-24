"""Session start reports embedding coverage as the daemon measures it (6.0.0, codex BLOCK on 6cd6a7bee).

The daemon owns recall's model and its store's vectors, so trw-mcp's own embedder
says nothing about either: it loads only for learn-time dedup. Session start
therefore takes coverage from the pipeline-health probe over ``memory_status``'s
health block, and never reports an MCP-side "initialization deferred" state.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.state._store_selection import NamespaceHealth
from trw_mcp.tools import _pipeline_health as ph


def _health(*, embedded: int | None, entries: int) -> NamespaceHealth:
    return {
        "entries": entries,
        "synced": 0,
        "edges": 5,
        "has_relations": True,
        "embedded": embedded,
        "max_recall_count": 3,
    }


def _refuse_local_model() -> None:
    raise AssertionError("session start must not ask trw-mcp's embedder about the daemon's vectors")


@pytest.fixture
def no_local_model() -> Iterator[None]:
    with (
        patch("trw_mcp.state._memory_connection.get_embedder", side_effect=_refuse_local_model),
        patch("trw_mcp.state._memory_connection.get_initialized_embedder", side_effect=_refuse_local_model),
    ):
        yield


def _session_start(health: NamespaceHealth | None, *, verbose: bool = False) -> dict[str, object]:
    fn = extract_tool_fn(make_test_server("ceremony"), "trw_session_start")
    measured = patch.object(ph, "store_health", return_value=health)
    if health is None:
        from trw_mcp.state._store_selection import StoreUnavailableError

        measured = patch.object(ph, "store_health", side_effect=StoreUnavailableError("the daemon is unreachable"))
    with measured:
        return dict(fn(ctx=None, query="*", verbose=verbose))


@pytest.mark.usefixtures("no_local_model")
def test_a_healthy_daemon_reports_its_coverage_and_no_advisory() -> None:
    result = _session_start(_health(embedded=99, entries=100), verbose=True)

    assert result["embeddings_coverage_ratio"] == 0.99
    assert "embeddings_advisory" not in result
    assert "embed_health" not in result
    assert "embedding_coverage" not in str(result.get("pipeline_health_advisory", ""))
    assert "deferred" not in json.dumps(result, default=str)


@pytest.mark.usefixtures("no_local_model")
def test_low_daemon_coverage_is_reported_through_pipeline_health() -> None:
    result = _session_start(_health(embedded=1, entries=100))

    assert result["embeddings_coverage_ratio"] == 0.01
    assert "embedding_coverage" in str(result["pipeline_health_advisory"])


@pytest.mark.usefixtures("no_local_model")
@pytest.mark.parametrize(
    "health",
    [_health(embedded=None, entries=100), None],
    ids=["store-keeps-no-vectors", "daemon-unreachable"],
)
def test_unmeasured_coverage_reports_no_ratio(health: NamespaceHealth | None) -> None:
    result = _session_start(health)

    assert "embeddings_coverage_ratio" not in result
    assert "embedding_coverage" in str(result["pipeline_health_advisory"])
