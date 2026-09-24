"""Relevant lexical candidates must survive acquisition before utility ranking.

PRD-CORE-280: ``test_registered_recall_retains_symptom_match_after_acquisition``
routes through the daemon-backed ``daemon_checkout`` (real storage, real
ranking) via the registered ``trw_recall`` tool, including its
``memory_adapter.record_surfaced`` access-tracking side effect.

Only ``test_relevance_query_replays_exact_order_and_filters`` was deleted: it
asserts a raw-connection RETRY-on-``OperationalError`` recovery mechanic
(byte-identical SQL replay after a simulated decode failure), which is SQLite
internals in the same sense as WAL/connection-pool/corruption-recovery tests,
not migratable product behaviour.
"""

import pytest

from tests._memory_fixtures import DaemonCheckout

# test_relevance_query_replays_exact_order_and_filters (formerly here) was
# DELETED, not ported/BLOCKED: it asserted a raw-connection
# retry-after-decode-error mechanic — ``_temporal_fetch._select_stream``
# replaying byte-identical SQL after a simulated ``sqlite3.OperationalError``
# — the same class of test as WAL/connection-pool/corruption-recovery
# mechanics the migration explicitly does not carry forward. See the module
# docstring and the final report.


def test_registered_recall_retains_symptom_match_after_acquisition(
    daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise real storage, ranking and output budget through the daemon-backed tool."""
    import asyncio

    from fastmcp import Client

    from tests import _path_isolation
    from tests.conftest import make_test_server
    from trw_mcp.models.config import get_config

    monkeypatch.setattr(get_config(), "embeddings_enabled", False)
    # The registered ``trw_recall`` tool resolves its trw_dir through
    # ``resolve_trw_dir()``, which (unlike ``resolve_project_root()``) does not
    # honor ``TRW_PROJECT_ROOT`` (tests/_path_isolation.py) — point the isolated
    # stand-in at the checkout daemon_checkout pinned, not the outer tmp_path
    # the autouse ``_isolate_trw_dir`` fixture set it to.
    _path_isolation.set_current_root(daemon_checkout.trw_dir.parent)
    client = daemon_checkout.client
    namespace = daemon_checkout.namespace

    async def seed() -> None:
        for token in ("read-only", "project", "tool", "discovery"):
            for index in range(26):
                await client.store(
                    f"{token} distinct archived investigation number {index}",
                    namespace,
                    entry_id=f"{token}-{index}",
                    importance=0.95,
                )
        await client.store(
            "Telemetry fail-open boundaries must catch FileStateWriter's StateError, not only raw OSError",
            namespace,
            entry_id="L-symptom",
            detail=(
                "Optional MCP security telemetry blocked real tool discovery and recall topic tests "
                "on a read-only project. Catch wrapped persistence errors at the optional telemetry "
                "boundary, preserving warning signals and authorization filters."
            ),
            importance=0.6,
        )

    asyncio.run(seed())

    async def recall():
        async with Client(make_test_server("learning")) as mcp_client:
            result = await mcp_client.call_tool(
                "trw_recall",
                {
                    "query": "read-only project tool discovery fails",
                    "max_results": 5,
                    "options": {
                        "include_tiers": ["project"],
                    },
                },
            )
            assert not result.is_error
            return result.structured_content

    response = asyncio.run(recall())
    assert len(response["learnings"]) <= 5
    assert "L-symptom" in [entry["id"] for entry in response["learnings"]], response["learnings"]
