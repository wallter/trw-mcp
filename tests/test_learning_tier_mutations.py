"""Regression coverage for mutations across federated learning tiers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from trw_mcp.models.config import _reset_config
from trw_mcp.state import memory_adapter
from trw_mcp.state._memory_lookups import update_access_tracking
from trw_mcp.state._user_tier import get_user_backend, reset_user_backend


@pytest.fixture(autouse=True)
def _isolated_stores(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "true")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    _reset_config()
    memory_adapter.reset_backend()
    reset_user_backend()
    yield
    memory_adapter.reset_backend()
    reset_user_backend()
    _reset_config()


def _trw_dir(tmp_path: Path) -> Path:
    path = tmp_path / "repo" / ".trw"
    path.mkdir(parents=True)
    return path


def test_user_tier_assertions_and_feedback_use_owning_backend(tmp_path: Path) -> None:
    trw_dir = _trw_dir(tmp_path)
    memory_adapter.store_learning(trw_dir, "L-user-own", "portable directive", "detail", scope="user")

    result = memory_adapter.update_learning(
        trw_dir,
        "L-user-own",
        assertions=[{"type": "glob_exists", "pattern": "", "target": "src/module.py"}],
        feedback="helpful",
    )

    assert result["status"] == "updated"
    entry = get_user_backend().get("L-user-own")
    assert entry is not None
    assert entry.helpful_count == 1
    assert len(entry.assertions) == 1
    assert memory_adapter.get_backend(trw_dir).get("L-user-own") is None


def test_feedback_increment_is_atomic_under_concurrency(tmp_path: Path) -> None:
    trw_dir = _trw_dir(tmp_path)
    memory_adapter.store_learning(trw_dir, "L-votes", "project fact", "detail", scope="project")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: memory_adapter.update_learning(trw_dir, "L-votes", feedback="helpful"),
                range(32),
            )
        )

    assert all(result["status"] == "updated" for result in results)
    entry = memory_adapter.get_backend(trw_dir).get("L-votes")
    assert entry is not None
    assert entry.helpful_count == 32


def test_federated_access_tracking_updates_each_owning_store(tmp_path: Path) -> None:
    trw_dir = _trw_dir(tmp_path)
    memory_adapter.store_learning(trw_dir, "L-project-hit", "project fact", "detail", scope="project")
    memory_adapter.store_learning(trw_dir, "L-user-hit", "portable directive", "detail", scope="user")

    update_access_tracking(trw_dir, ["L-project-hit", "L-user-hit", "L-external"], federated=True)

    project_entry = memory_adapter.get_backend(trw_dir).get("L-project-hit")
    user_entry = get_user_backend().get("L-user-hit")
    assert project_entry is not None and project_entry.recall_count == 1
    assert user_entry is not None and user_entry.recall_count == 1


def test_registered_recall_tracks_user_tier_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.conftest import extract_tool_fn, make_test_server

    trw_dir = _trw_dir(tmp_path)
    memory_adapter.store_learning(trw_dir, "L-user-recall", "portable cadence directive", "detail", scope="user")
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: trw_dir)

    recall = extract_tool_fn(make_test_server("learning"), "trw_recall")
    result = recall(query="portable cadence directive", max_results=10)

    assert any(entry["id"] == "L-user-recall" for entry in result["learnings"])
    entry = get_user_backend().get("L-user-recall")
    assert entry is not None and entry.recall_count == 1


def test_federated_owner_lookup_failure_does_not_break_recall_tracking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenBackend:
        def get(self, _learning_id: str) -> object:
            raise OSError("store unavailable")

    monkeypatch.setattr("trw_mcp.state._memory_lookups.get_backend", lambda _trw_dir: BrokenBackend())

    update_access_tracking(_trw_dir(tmp_path), ["L-unavailable"], federated=True)


def test_direct_adapter_rejects_invalid_feedback(tmp_path: Path) -> None:
    trw_dir = _trw_dir(tmp_path)
    memory_adapter.store_learning(trw_dir, "L-feedback", "project fact", "detail", scope="project")

    result = memory_adapter.update_learning(trw_dir, "L-feedback", feedback="maybe")

    assert result["status"] == "invalid"
    entry = memory_adapter.get_backend(trw_dir).get("L-feedback")
    assert entry is not None and entry.unhelpful_count == 0
