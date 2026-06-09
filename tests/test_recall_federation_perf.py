"""PRD-CORE-185 FR06 / NFR01: federation is lazy + bounded (no hot-path regression).

The ``trw_session_start`` recall hot path (~0.9s budget) MUST pay nothing for
federation when the user tier is disabled or the user store is absent/empty.
These tests assert the gate short-circuits BEFORE any user-store query work --
no synchronous full-store scan -- so session_start latency is unaffected.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import _reset_config
from trw_mcp.state import memory_adapter
from trw_mcp.state._user_tier import reset_user_backend


@pytest.fixture(autouse=True)
def _isolated_user_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    _reset_config()
    memory_adapter.reset_backend()
    reset_user_backend()
    yield
    memory_adapter.reset_backend()
    reset_user_backend()
    _reset_config()


def _trw_dir(tmp_path: Path) -> Path:
    d = tmp_path / "repo" / ".trw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_no_user_query_when_tier_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """User tier disabled -> federation never queries the user backend."""
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "false")
    _reset_config()
    repo = _trw_dir(tmp_path)
    memory_adapter.store_learning(repo, "L-a", "project widget thing", "d", scope="project")

    with patch("trw_mcp.state._memory_recall._query_user_backend") as q:
        rows = memory_adapter.recall_learnings(repo, "widget", max_results=10)
    q.assert_not_called()
    assert "L-a" in [str(r.get("id")) for r in rows]


def test_no_backend_construction_when_store_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier flag on but no user store on disk -> no user backend is CONSTRUCTED.

    The gate uses ``peek_user_backend`` (never constructs) + an on-disk probe;
    when neither is satisfied, ``get_user_backend`` (the heavy constructor) must
    not be called from the recall path.
    """
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "true")
    _reset_config()
    reset_user_backend()  # ensure no live singleton
    repo = _trw_dir(tmp_path)
    # Project-only write (stays in project store; user store never created).
    memory_adapter.store_learning(repo, "L-p", "project widget thing", "d", scope="project")
    reset_user_backend()

    with patch("trw_mcp.state._user_tier.get_user_backend") as ctor:
        memory_adapter.recall_learnings(repo, "widget", max_results=10)
    ctor.assert_not_called()


def test_federation_failure_falls_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A federation error degrades to project-only recall (NFR04 fail-open)."""
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "true")
    _reset_config()
    repo = _trw_dir(tmp_path)
    memory_adapter.store_learning(repo, "L-proj", "project widget thing", "d", scope="project")

    with patch(
        "trw_mcp.state._memory_recall.user_scope_present",
        side_effect=RuntimeError("boom"),
    ):
        rows = memory_adapter.recall_learnings(repo, "widget", max_results=10)
    # Recall still returns the project hit despite the federation error.
    assert "L-proj" in [str(r.get("id")) for r in rows]
