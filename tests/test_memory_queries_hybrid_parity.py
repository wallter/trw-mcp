"""PRD-CORE-292 FR03: expired transient entries are excluded on every MCP recall path.

The hybrid-fusion parity cases that lived here ran trw-mcp's own search over an
in-process SQLite store; that search moved to the memory daemon with PRD-CORE-280
e3, and trw-memory's retrieval tests own it now.
"""

from __future__ import annotations

from pathlib import Path

from ._memory_fixtures import DaemonCheckout

# ---------------------------------------------------------------------------
# PRD-CORE-292 FR03 — expired transient entries are excluded on every MCP path.
# ---------------------------------------------------------------------------


class TestExpiryAdmission:
    """MemoryClient.recall's exclude_expired default, at recall_learnings' single boundary.

    The validity prior already closes a row whose own ``expires`` field has passed
    (PRD-CORE-244). What it does not see is expiry carried in ``metadata`` -- the
    shape SourcePolicy reads -- nor an expired transient row re-admitted by
    ``include_superseded``. Only lifecycle/episodic families expire here.

    PRD-CORE-280 slice e: ``test_historical_as_of_judges_expiry_at_that_instant``
    and ``test_exact_day_is_still_valid`` are DELETED (batch 23b). Both needed a
    ``valid_from`` set in the past (2018) so an ``as_of`` of 2019-2020 still
    admits the row; ``store_learning``/``StoreRequest`` (the only route to a
    store once ``get_backend``/``SQLiteBackend`` are gone) has no ``valid_from``
    passthrough, so porting is not possible without a new store-API field. The
    day-exclusive as_of/expiry semantics they guarded are covered by
    trw-memory's own suite (``tests/test_validity_aware_recall.py``,
    ``tests/test_temporal_selection.py``, ``tests/test_client_temporal_fallback.py``).
    """

    def _seed(self, trw_dir: Path) -> None:
        from trw_mcp.state.memory_adapter import store_learning

        rows = {
            "L-expired": ("lifecycle", "2020-01-01"),
            "L-fresh": ("lifecycle", "2999-01-01"),
            "L-rule": ("instruction_rule", "2020-01-01"),
            "L-episode": ("episodic", "2020-01-01"),
        }
        for eid, (kind, expires) in rows.items():
            store_learning(
                trw_dir,
                eid,
                f"zebra finder handoff note {eid}",
                "d",
                metadata={"source_kind": kind, "expires": expires},
            )

    def test_expired_transient_rows_are_excluded_wildcard(self, daemon_checkout: DaemonCheckout) -> None:
        """Wildcard listing: the daemon fixture has no embedder, this is the real keyword/admission path."""
        from trw_mcp.state.memory_adapter import recall_learnings

        self._seed(daemon_checkout.trw_dir)
        ids = {str(r["id"]) for r in recall_learnings(daemon_checkout.trw_dir, "*", max_results=25)}
        assert "L-expired" not in ids and "L-episode" not in ids, ids
        assert {"L-fresh", "L-rule"} <= ids, ids
