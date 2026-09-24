"""F5 root-cause B: the forced knowledge-graph backfill keeps its resume point beside the checkout.

The store builds each page's edges (its enrichment, deadline, canary skip and
per-row fail-open are pinned by ``trw-memory/tests/test_graph_backfill_page.py``).
Here the sweep must resume where the last call stopped, record itself complete
once the store says the listing ran out, and stay a no-op after that.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests._memory_fixtures import FAKE_NAMESPACE, DaemonCheckout
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state.memory_adapter import backfill_graph


def _sweep_state(trw_dir: Path, namespace: str = FAKE_NAMESPACE) -> dict[str, object]:
    raw = json.loads((trw_dir / "memory" / "graph-backfill.json").read_text(encoding="utf-8"))
    state: dict[str, object] = raw["namespaces"][namespace]
    return state


def _corpus(store: FakeMemoryStore) -> None:
    for index in (1, 2, 3):
        store.put(f"note {index}", FAKE_NAMESPACE, {"entry_id": f"L-bf-{index}"})


def test_a_bounded_sweep_resumes_after_the_last_row_until_the_store_runs_out(
    fake_memory_store: FakeMemoryStore, tmp_path: Path
) -> None:
    _corpus(fake_memory_store)
    trw_dir = tmp_path / ".trw"

    cursors = []
    for _ in range(3):
        assert backfill_graph(trw_dir, limit=1)["processed"] == 1
        cursors.append(_sweep_state(trw_dir)["entry_id"])
        assert _sweep_state(trw_dir)["complete"] is False

    assert backfill_graph(trw_dir, limit=1)["processed"] == 0
    assert _sweep_state(trw_dir)["complete"] is True
    assert sorted(cursors) == ["L-bf-1", "L-bf-2", "L-bf-3"]  # type: ignore[type-var]


def test_a_complete_sweep_does_not_call_the_store_again(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    _corpus(fake_memory_store)
    trw_dir = tmp_path / ".trw"
    assert backfill_graph(trw_dir)["processed"] == 3
    calls = len(fake_memory_store.calls)

    assert backfill_graph(trw_dir) == {"processed": 0, "edges_built": 0, "skipped": 0, "failed": 0}
    assert len(fake_memory_store.calls) == calls


def test_the_deadline_goes_to_the_store(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    backfill_graph(tmp_path / ".trw", deadline_seconds=2.0)

    assert fake_memory_store.calls[-1] == ("graph_backfill", (FAKE_NAMESPACE, None, 10_000, 2.0))


def test_the_daemon_sweeps_a_checkouts_rows_through_to_complete(daemon_checkout: DaemonCheckout) -> None:
    from trw_mcp.state.memory_adapter import store_learning

    for index in (1, 2):
        store_learning(daemon_checkout.trw_dir, f"L-dbf-{index}", f"Daemon swept note {index}", "detail")

    assert backfill_graph(daemon_checkout.trw_dir)["processed"] == 2
    assert _sweep_state(daemon_checkout.trw_dir, daemon_checkout.namespace)["complete"] is True
    assert backfill_graph(daemon_checkout.trw_dir)["processed"] == 0
