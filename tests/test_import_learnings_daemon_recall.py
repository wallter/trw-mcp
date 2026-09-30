"""IMPORT-LEARNINGS-DAEMON-RECALL: an imported learning survives the real daemon and comes back from the production recall.

``test_import_learnings_store`` proves the import against ``FakeMemoryStore``, whose recall is a substring match over a
dict, so daemon persistence and the registered ``trw_recall`` tool were never exercised end to end. This runs the same
import into the session daemon (``daemon_checkout``) and reads it back through the REAL registered tool.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.export import import_learnings


@pytest.fixture()
def checkout(daemon_checkout: DaemonCheckout) -> DaemonCheckout:
    """The checkout whose ``.trw`` the registered tool resolves (see test_recall_ids_admission for why)."""
    from tests import _path_isolation

    _path_isolation.set_current_root(daemon_checkout.trw_dir.parent)
    (daemon_checkout.trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    return daemon_checkout


def _source(tmp_path: Path, entries: list[dict[str, object]]) -> Path:
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"metadata": {"project": "elsewhere"}, "learnings": entries}), encoding="utf-8")
    return path


def _recall(**kwargs: Any) -> dict[str, Any]:
    from tests.conftest import extract_tool_fn, make_test_server

    return extract_tool_fn(make_test_server("learning"), "trw_recall")(**kwargs)


def test_an_imported_learning_is_persisted_by_the_daemon_and_recalled_by_the_production_tool(
    checkout: DaemonCheckout, tmp_path: Path
) -> None:
    summary = "Quokka calibration needs a warm start before the first read"
    src = _source(tmp_path, [{"summary": summary, "detail": "imported detail", "impact": 0.8, "tags": ["imported"]}])

    result = import_learnings(src, checkout.trw_dir.parent)

    assert (result["status"], result["imported"], result["refused"]) == ("ok", 1, 0)
    [learning_id] = result["imported_ids"]
    held = asyncio.run(checkout.client.get(learning_id, checkout.namespace))  # the daemon holds it, not just YAML
    assert held.get("status") == "ok"

    by_query = _recall(query="quokka calibration")
    assert learning_id in {row["id"] for row in by_query["learnings"]}
    by_id = _recall(query="", ids=[learning_id])
    assert [row["id"] for row in by_id["learnings"]] == [learning_id]
    assert "missing_ids" not in by_id


def test_a_dry_run_leaves_nothing_for_the_daemon_to_recall(checkout: DaemonCheckout, tmp_path: Path) -> None:
    src = _source(tmp_path, [{"summary": "Zanzibar ledger is append only", "detail": "d", "impact": 0.8}])

    result = import_learnings(src, checkout.trw_dir.parent, dry_run=True)

    assert result["imported_ids"] == []
    assert _recall(query="zanzibar ledger")["learnings"] == []
