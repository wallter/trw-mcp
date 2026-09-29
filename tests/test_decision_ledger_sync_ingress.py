"""PRD-CORE-334 FR05 on trw-mcp's team pull: a pulled type this build does not know is kept, not dropped.

Driven through the real pull path (``SyncPuller.merge_team_learnings`` into the
daemon store): an unknown ``type`` (what a pre-DECISION client sees in a newer
client's row) lands as PATTERN with the raw value in ``metadata["type_raw"]``,
by the same rule trw-memory's own sync ingress uses.
"""

from __future__ import annotations

import pytest

from tests._memory_fixtures import DaemonCheckout


@pytest.mark.parametrize(
    ("wire_type", "stored_type", "type_raw"),
    [("retrospective", "pattern", "retrospective"), ("decision", "decision", None), ("incident", "incident", None)],
)
def test_a_pulled_learning_keeps_or_degrades_its_type(
    daemon_checkout: DaemonCheckout, wire_type: str, stored_type: str, type_raw: str | None
) -> None:
    from trw_mcp.state._store_selection import selected_store
    from trw_mcp.sync.pull import SyncPuller

    puller = SyncPuller(backend_url="http://example.com", api_key="key", client_id="c", trw_dir=daemon_checkout.trw_dir)
    learning = {"source_learning_id": "remote-1", "summary": "pulled tip", "detail": "d", "type": wire_type}

    merged = puller.merge_team_learnings([learning])

    assert merged.applied == 1
    store, _namespace = selected_store(daemon_checkout.trw_dir)
    stored = store.get("team-sync-remote-1")
    assert stored is not None and stored.type == stored_type
    assert stored.metadata.get("type_raw") == type_raw
