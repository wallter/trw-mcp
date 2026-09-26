"""Regression coverage for mutations across federated learning tiers."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_fixtures import MemoryDaemon, attach_checkout
from trw_mcp.models.config import _reset_config
from trw_mcp.state import memory_adapter
from trw_mcp.state._tier_routing import USER_NAMESPACE


@pytest.fixture(autouse=True)
def _isolated_stores(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "true")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    _reset_config()
    yield
    _reset_config()


def _trw_dir(tmp_path: Path) -> Path:
    path = tmp_path / "repo" / ".trw"
    path.mkdir(parents=True)
    return path


def test_user_tier_assertions_use_owning_backend(
    tmp_path: Path, memory_daemon: MemoryDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ported to a migrated checkout: ``update_learning``/``store_learning`` route
    through ``selected_store`` (PRD-CORE-280 FR01), unlike ``update_access_tracking``
    below, so they never touch a local ``memory.db``.
    """
    trw_dir = _trw_dir(tmp_path)
    # Override the file's own ``_isolated_stores`` autouse fixture, which pins
    # TRW_USER_DIR to an unmigrated machine-local store for the still-blocked
    # tests below; this test needs the session daemon's user dir instead.
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    attach_checkout(trw_dir, memory_daemon)
    memory_adapter.store_learning(trw_dir, "L-user-own", "portable directive", "detail", scope="user")

    result = memory_adapter.update_learning(
        trw_dir,
        "L-user-own",
        assertions=[{"type": "glob_exists", "pattern": "", "target": "src/module.py"}],
    )

    assert result["status"] == "updated"
    from trw_mcp.state import _store_selection

    store, _project_namespace = _store_selection.selected_store(trw_dir)
    # ``MemoryStore.get`` checks the project namespace first and only falls to
    # the user tier on a miss (see ``DaemonMemoryStore``/``SqliteMemoryStore``
    # docstrings), so landing in the user namespace already proves the row is
    # NOT held by the project tier.
    entry = store.get("L-user-own")
    assert entry is not None
    assert entry.namespace == USER_NAMESPACE
    assert len(entry.assertions) == 1


# test_federated_owner_lookup_failure_does_not_break_recall_tracking DELETED
# (PRD-CORE-280): pinned the OLD trw-mcp-side federated owner-lookup walk
# (patching `_memory_lookups.get_backend` to raise, and update_access_tracking's
# now-removed `federated=True` kwarg). That walk is gone -- the store resolves
# the owning namespace itself now (see `FakeMemoryStore.record_surfaced` / the
# real stores' `record_surfaced`), and `memory_adapter.record_surfaced` fails
# open on any RuntimeError/ValueError/OSError from the store call as a whole
# (covered by tests/test_record_surfaced.py::TestRecordSurfacedFailsOpen),
# not per-entry via an injectable backend getter.


def test_federated_access_tracking_updates_each_owning_store(
    tmp_path: Path, memory_daemon: MemoryDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = _trw_dir(tmp_path)
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    attach_checkout(trw_dir, memory_daemon)
    memory_adapter.store_learning(trw_dir, "L-project-hit", "project fact", "detail", scope="project")
    memory_adapter.store_learning(trw_dir, "L-user-hit", "portable directive", "detail", scope="user")

    memory_adapter.record_surfaced(trw_dir, ["L-project-hit", "L-user-hit", "L-external"])

    from trw_mcp.state import _store_selection

    store, _project_namespace = _store_selection.selected_store(trw_dir)
    project_entry = store.get("L-project-hit")
    user_entry = store.get("L-user-hit")
    assert project_entry is not None and project_entry.recall_count == 1
    assert user_entry is not None and user_entry.recall_count == 1


def test_registered_recall_tracks_user_tier_hit(
    tmp_path: Path, memory_daemon: MemoryDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import extract_tool_fn, make_test_server

    trw_dir = _trw_dir(tmp_path)
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    attach_checkout(trw_dir, memory_daemon)
    memory_adapter.store_learning(trw_dir, "L-user-recall", "portable cadence directive", "detail", scope="user")
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: trw_dir)

    recall = extract_tool_fn(make_test_server("learning"), "trw_recall")
    result = recall(query="portable cadence directive", max_results=10)

    assert any(entry["id"] == "L-user-recall" for entry in result["learnings"])
    from trw_mcp.state import _store_selection

    store, _project_namespace = _store_selection.selected_store(trw_dir)
    entry = store.get("L-user-recall")
    assert entry is not None and entry.recall_count == 1
