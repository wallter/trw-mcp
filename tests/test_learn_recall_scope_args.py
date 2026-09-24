"""PRD-CORE-185 FR07: tool-surface ``scope`` + tier-scoped recall.

``trw_learn`` exposes ``scope: Literal["auto","project","user"] = "auto"`` that
threads to ``store_learning(scope=...)`` (the routing already exists; FR07 just
exposes + threads it). ``trw_recall`` exposes ``include_tiers`` so a caller can
restrict recall to project-only (exclude the user tier) while the default
includes the user tier when a user-scope store is present.

These tests exercise the REAL tool closures (not the adapter directly) so they
prove the param is wired end-to-end through ``execute_learn`` / ``execute_recall``.

All tests below (``trw_learn(scope=...)`` routing and
``trw_recall(include_tiers=...)`` tier scoping) drive the REAL registered
tool closures against the daemon-backed checkout (``daemon_checkout``)
rather than an in-process ``memory.db``. The tool resolves ``trw_dir`` via
``resolve_trw_dir()``, which this suite's process-wide path-isolation
stand-in (``tests/_path_isolation.py``) answers from its own
``current_root()`` rather than ``TRW_PROJECT_ROOT`` -- so the
``daemon_project`` fixture repoints ``current_root()`` at the checkout's own
root (``tests/_path_isolation.set_current_root``) so the tool closure lands
on the daemon-migrated checkout. Project-vs-user landing is asserted by
reading the daemon's own project/``user:local`` namespaces back through
``daemon_checkout.client``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests import _path_isolation
from tests._memory_fixtures import DaemonCheckout
from trw_mcp.state._tier_routing import USER_NAMESPACE

from .conftest import extract_tool_fn, make_test_server

# --------------------------------------------------------------------------- #
# trw_learn(scope=...) routing: PRD-CORE-280 daemon-backed checkout
# --------------------------------------------------------------------------- #


@pytest.fixture()
def daemon_project(daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> DaemonCheckout:
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "true")
    _path_isolation.set_current_root(daemon_checkout.trw_dir.parent)
    return daemon_checkout


def _learn_scope(daemon_project: DaemonCheckout, **kwargs: Any) -> dict[str, object]:
    fn = extract_tool_fn(make_test_server("learning"), "trw_learn")
    return dict(fn(**kwargs))


def _in_namespace(daemon_project: DaemonCheckout, entry_id: str, namespace: str) -> bool:
    async def _do() -> bool:
        result = await daemon_project.client.get(entry_id, namespace)
        return result.get("status") == "ok"

    return asyncio.run(_do())


def test_scope_user_routes_to_user_store(daemon_project: DaemonCheckout) -> None:
    """``scope='user'`` forces the user tier even for project-looking content."""
    res = _learn_scope(
        daemon_project,
        summary="patch the widget in src/widget.py for the bug",
        detail="this would normally classify project, but scope=user overrides",
        scope="user",
    )
    lid = str(res["learning_id"])
    assert not _in_namespace(daemon_project, lid, daemon_project.namespace)
    assert _in_namespace(daemon_project, lid, USER_NAMESPACE)


def test_scope_project_overrides_portable(daemon_project: DaemonCheckout) -> None:
    """``scope='project'`` forces the project tier even for portable content."""
    res = _learn_scope(
        daemon_project,
        summary="operator directive: always commit frequently",
        detail="portable-looking, but scope=project pins it to the project store",
        tags=["directive"],
        metadata={"source_type": "human"},
        scope="project",
    )
    lid = str(res["learning_id"])
    assert _in_namespace(daemon_project, lid, daemon_project.namespace)


def test_scope_auto_portable_routes_user(daemon_project: DaemonCheckout) -> None:
    """Default ``scope='auto'`` keeps the automatic heuristic (portable -> user)."""
    res = _learn_scope(
        daemon_project,
        summary="operator directive prefers larger ollama models everywhere",
        detail="cross-cutting workflow directive, repo-agnostic",
        tags=["directive"],
        metadata={"source_type": "human"},
    )
    lid = str(res["learning_id"])
    assert not _in_namespace(daemon_project, lid, daemon_project.namespace)
    assert _in_namespace(daemon_project, lid, USER_NAMESPACE)


def test_scope_auto_project_stays_project(daemon_project: DaemonCheckout) -> None:
    """Default ``scope='auto'`` routes project-specific content to project."""
    res = _learn_scope(
        daemon_project,
        summary="bug in trw_mcp/state/foo.py:42 needs a guard",
        detail="repo-relative path is a project signal",
    )
    lid = str(res["learning_id"])
    assert _in_namespace(daemon_project, lid, daemon_project.namespace)


# --------------------------------------------------------------------------- #
# trw_recall(include_tiers=...) tier scoping
# --------------------------------------------------------------------------- #


def _recall(daemon_project: DaemonCheckout, **kwargs: Any) -> Any:
    server = make_test_server("learning")
    fn = extract_tool_fn(server, "trw_recall")
    return fn(**kwargs)


def _ids(result: Any) -> list[str]:
    return [str(r.get("id")) for r in result.get("learnings", [])]


def test_recall_default_includes_user_tier(daemon_project: DaemonCheckout) -> None:
    """Default recall federates the user tier when a user store is present."""
    res = _learn_scope(
        daemon_project,
        summary="operator directive cadence: commit frequently across repos",
        detail="portable directive that lands in the user tier",
        tags=["directive"],
        metadata={"source_type": "human"},
    )
    user_lid = str(res["learning_id"])
    # The learning lives in the user store, not the project store.
    assert _in_namespace(daemon_project, user_lid, USER_NAMESPACE)
    assert not _in_namespace(daemon_project, user_lid, daemon_project.namespace)
    # Default recall (no include_tiers) federates it in from the user tier.
    out = _recall(daemon_project, query="commit frequently cadence directive", max_results=10)
    assert user_lid in _ids(out), "expected the user-tier learning to surface by default"


def test_recall_include_tiers_project_excludes_user(daemon_project: DaemonCheckout) -> None:
    """``include_tiers=['project']`` returns project-only (user tier excluded)."""
    user_res = _learn_scope(
        daemon_project,
        summary="operator directive cadence: commit frequently across repos",
        detail="portable directive that lands in the user tier",
        tags=["directive"],
        metadata={"source_type": "human"},
    )
    user_lid = str(user_res["learning_id"])
    # A project-only learning that also matches the query.
    proj_res = _learn_scope(
        daemon_project,
        summary="commit frequently when touching src/widget.py in this repo",
        detail="repo-specific note",
    )
    proj_lid = str(proj_res["learning_id"])

    res = _recall(
        daemon_project,
        query="commit frequently",
        max_results=10,
        options={"include_tiers": ["project"]},
    )
    ids = _ids(res)
    assert proj_lid in ids
    assert user_lid not in ids, "user tier must be excluded when include_tiers=['project']"


def test_recall_include_tiers_both_includes_user(daemon_project: DaemonCheckout) -> None:
    """Explicit ``include_tiers=['project','user']`` federates the user tier."""
    user_res = _learn_scope(
        daemon_project,
        summary="operator directive cadence: commit frequently across repos",
        detail="portable directive that lands in the user tier",
        tags=["directive"],
        metadata={"source_type": "human"},
    )
    user_lid = str(user_res["learning_id"])
    res = _recall(
        daemon_project,
        query="commit frequently cadence directive",
        max_results=10,
        options={"include_tiers": ["project", "user"]},
    )
    assert user_lid in _ids(res)
