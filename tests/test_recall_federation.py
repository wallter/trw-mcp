"""PRD-CORE-185 FR06: recall federation across project ∪ user tiers.

``recall_learnings`` merges project-store hits with machine-local user-store
hits (de-duped, capped by ``recall_user_tier_cap``, tier as a re-rank feature
not an override). Cross-project transfer: a portable learning written while in
repo A is surfaced by recall in repo B on the same box (one shared user-home
store). With the user store absent/empty, recall is byte-identical to today.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import _reset_config
from trw_mcp.state import memory_adapter
from trw_mcp.state._user_tier import reset_user_backend


@pytest.fixture(autouse=True)
def _isolated_user_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "true")
    _reset_config()
    memory_adapter.reset_backend()
    reset_user_backend()
    yield
    memory_adapter.reset_backend()
    reset_user_backend()
    _reset_config()


def _trw_dir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name / ".trw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ids(rows: list[dict[str, object]]) -> list[str]:
    return [str(r.get("id")) for r in rows]


def test_cross_project_transfer(tmp_path: Path) -> None:
    """Portable learning written in repo A is recalled in repo B (shared user store)."""
    repo_a = _trw_dir(tmp_path, "repoA")
    repo_b = _trw_dir(tmp_path, "repoB")

    # Write a portable learning while "in" repo A -> routes to the user store.
    memory_adapter.store_learning(
        repo_a,
        "L-xfer",
        "operator prefers frequent commits cadence directive",
        "always commit after each logical unit of work",
        tags=["directive"],
        source_type="human",
    )
    # Project A store must NOT hold it (it went to the user tier).
    assert memory_adapter.get_backend(repo_a).get("L-xfer") is None
    memory_adapter.reset_backend()  # drop project-A singleton

    # Recall in repo B surfaces the user-tier learning via federation.
    rows = memory_adapter.recall_learnings(repo_b, "commits cadence directive", max_results=10)
    assert "L-xfer" in _ids(rows)


def test_project_hit_stays_rank_1(tmp_path: Path) -> None:
    """A precise project hit keeps rank 1 against low-value user hits."""
    repo = _trw_dir(tmp_path, "repo")
    # Precise, high-impact project hit.
    memory_adapter.store_learning(
        repo,
        "L-proj",
        "frobnicate widget alpha config in src/widget.py",
        "the precise project answer about frobnicate widget alpha",
        impact=0.95,
        scope="project",
    )
    # Several low-value user hits that mention 'frobnicate' weakly.
    for i in range(4):
        memory_adapter.store_learning(
            repo,
            f"L-user{i}",
            f"frobnicate note {i}",
            "low value cross-cutting noise",
            impact=0.2,
            tags=["directive"],
            scope="user",
        )
    rows = memory_adapter.recall_learnings(repo, "frobnicate widget alpha", max_results=10)
    ids = _ids(rows)
    assert ids, "recall returned nothing"
    assert ids[0] == "L-proj", f"precise project hit must stay rank 1 (got {ids})"


def test_user_tier_cap_respected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No more than recall_user_tier_cap user hits enter the merged result."""
    monkeypatch.setenv("TRW_RECALL_USER_TIER_CAP", "2")
    _reset_config()
    repo = _trw_dir(tmp_path, "repo")
    for i in range(6):
        memory_adapter.store_learning(
            repo,
            f"L-cap{i}",
            f"portable cadence directive note {i}",
            "cross-cutting",
            tags=["directive"],
            scope="user",
        )
    rows = memory_adapter.recall_learnings(repo, "portable cadence directive note", max_results=20)
    user_hits = [r for r in _ids(rows) if r.startswith("L-cap")]
    assert len(user_hits) <= 2, f"cap=2 must bound user hits (got {len(user_hits)})"


def test_absent_user_store_is_project_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the user tier disabled, recall is project-only (no federation)."""
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "false")
    _reset_config()
    repo = _trw_dir(tmp_path, "repo")
    memory_adapter.store_learning(
        repo,
        "L-proj-only",
        "some project learning about widgets",
        "detail",
        scope="project",
    )
    rows = memory_adapter.recall_learnings(repo, "widgets", max_results=10)
    assert "L-proj-only" in _ids(rows)


def test_dedupe_no_duplicate_ids(tmp_path: Path) -> None:
    """The merged result never contains the same id twice."""
    repo = _trw_dir(tmp_path, "repo")
    memory_adapter.store_learning(repo, "L-p", "project widget thing", "d", scope="project")
    memory_adapter.store_learning(
        repo, "L-u", "portable directive thing", "d", tags=["directive"], scope="user"
    )
    rows = memory_adapter.recall_learnings(repo, "thing", max_results=10)
    ids = _ids(rows)
    assert len(ids) == len(set(ids)), f"duplicate ids in federated result: {ids}"
