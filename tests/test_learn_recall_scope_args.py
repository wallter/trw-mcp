"""PRD-CORE-185 FR07: tool-surface ``scope`` + tier-scoped recall.

``trw_learn`` exposes ``scope: Literal["auto","project","user"] = "auto"`` that
threads to ``store_learning(scope=...)`` (the routing already exists; FR07 just
exposes + threads it). ``trw_recall`` exposes ``include_tiers`` so a caller can
restrict recall to project-only (exclude the user tier) while the default
includes the user tier when a user-scope store is present.

These tests exercise the REAL tool closures (not the adapter directly) so they
prove the param is wired end-to-end through ``execute_learn`` / ``execute_recall``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config import _reset_config
from trw_mcp.state import memory_adapter
from trw_mcp.state._user_tier import reset_user_backend

from .conftest import extract_tool_fn, make_test_server


@pytest.fixture(autouse=True)
def _isolated_user_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "true")
    # Pin the project root so the tool's resolve_trw_dir() lands in tmp.
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path / "proj"))
    (tmp_path / "proj" / ".trw").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path / "proj")
    _reset_config()
    memory_adapter.reset_backend()
    reset_user_backend()
    yield
    memory_adapter.reset_backend()
    reset_user_backend()
    _reset_config()


def _project_trw_dir(tmp_path: Path) -> Path:
    return tmp_path / "proj" / ".trw"


def _user_db_exists() -> bool:
    from trw_mcp.state._user_paths import resolve_user_memory_dir

    return (resolve_user_memory_dir(create=False) / "memory.db").exists()


# --------------------------------------------------------------------------- #
# trw_learn(scope=...) routing through the tool surface
# --------------------------------------------------------------------------- #


def _learn(**kwargs: Any) -> Any:
    server = make_test_server("learning")
    fn = extract_tool_fn(server, "trw_learn")
    return fn(**kwargs)


def _recall(**kwargs: Any) -> Any:
    server = make_test_server("learning")
    fn = extract_tool_fn(server, "trw_recall")
    return fn(**kwargs)


def test_scope_user_routes_to_user_store(tmp_path: Path) -> None:
    """``scope='user'`` forces the user tier even for project-looking content."""
    res = _learn(
        summary="patch the widget in src/widget.py for the bug",
        detail="this would normally classify project, but scope=user overrides",
        scope="user",
    )
    lid = res["learning_id"]
    # Not in the project store...
    assert memory_adapter.get_backend(_project_trw_dir(tmp_path)).get(lid) is None
    # ...and the user store exists + holds it.
    assert _user_db_exists()
    from trw_mcp.state._user_tier import get_user_backend

    assert get_user_backend().get(lid) is not None


def test_scope_project_overrides_portable(tmp_path: Path) -> None:
    """``scope='project'`` forces the project tier even for portable content."""
    res = _learn(
        summary="operator directive: always commit frequently",
        detail="portable-looking, but scope=project pins it to the project store",
        tags=["directive"],
        source_type="human",
        scope="project",
    )
    lid = res["learning_id"]
    assert memory_adapter.get_backend(_project_trw_dir(tmp_path)).get(lid) is not None


def test_scope_auto_portable_routes_user(tmp_path: Path) -> None:
    """Default ``scope='auto'`` keeps the automatic heuristic (portable -> user)."""
    res = _learn(
        summary="operator directive prefers larger ollama models everywhere",
        detail="cross-cutting workflow directive, repo-agnostic",
        tags=["directive"],
        source_type="human",
    )
    lid = res["learning_id"]
    assert memory_adapter.get_backend(_project_trw_dir(tmp_path)).get(lid) is None
    from trw_mcp.state._user_tier import get_user_backend

    assert get_user_backend().get(lid) is not None


def test_scope_auto_project_stays_project(tmp_path: Path) -> None:
    """Default ``scope='auto'`` routes project-specific content to project."""
    res = _learn(
        summary="bug in trw_mcp/state/foo.py:42 needs a guard",
        detail="repo-relative path is a project signal",
    )
    lid = res["learning_id"]
    assert memory_adapter.get_backend(_project_trw_dir(tmp_path)).get(lid) is not None


# --------------------------------------------------------------------------- #
# trw_recall(include_tiers=...) tier scoping
# --------------------------------------------------------------------------- #


def _ids(result: Any) -> list[str]:
    return [str(r.get("id")) for r in result.get("learnings", [])]


def test_recall_default_includes_user_tier(tmp_path: Path) -> None:
    """Default recall federates the user tier when a user store is present."""
    res = _learn(
        summary="operator directive cadence: commit frequently across repos",
        detail="portable directive that lands in the user tier",
        tags=["directive"],
        source_type="human",
    )
    user_lid = res["learning_id"]
    # The learning lives in the user store, not the project store.
    from trw_mcp.state._user_tier import get_user_backend

    assert get_user_backend().get(user_lid) is not None
    # Default recall (no include_tiers) federates it in from the user tier.
    out = _recall(query="commit frequently cadence directive", max_results=10)
    assert user_lid in _ids(out), "expected the user-tier learning to surface by default"


def test_recall_include_tiers_project_excludes_user(tmp_path: Path) -> None:
    """``include_tiers=['project']`` returns project-only (user tier excluded)."""
    user_res = _learn(
        summary="operator directive cadence: commit frequently across repos",
        detail="portable directive that lands in the user tier",
        tags=["directive"],
        source_type="human",
    )
    user_lid = user_res["learning_id"]
    # A project-only learning that also matches the query.
    proj_res = _learn(
        summary="commit frequently when touching src/widget.py in this repo",
        detail="repo-specific note",
    )
    proj_lid = proj_res["learning_id"]

    res = _recall(
        query="commit frequently",
        max_results=10,
        include_tiers=["project"],
    )
    ids = _ids(res)
    assert proj_lid in ids
    assert user_lid not in ids, "user tier must be excluded when include_tiers=['project']"


def test_recall_include_tiers_both_includes_user(tmp_path: Path) -> None:
    """Explicit ``include_tiers=['project','user']`` federates the user tier."""
    user_res = _learn(
        summary="operator directive cadence: commit frequently across repos",
        detail="portable directive that lands in the user tier",
        tags=["directive"],
        source_type="human",
    )
    user_lid = user_res["learning_id"]
    res = _recall(
        query="commit frequently cadence directive",
        max_results=10,
        include_tiers=["project", "user"],
    )
    assert user_lid in _ids(res)
