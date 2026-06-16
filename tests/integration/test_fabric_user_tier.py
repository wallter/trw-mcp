"""Knowledge-fabric INTEGRATION — federation seam (PRD-CORE-185 FR05->FR06).

Exercises the cross-PRD wiring, not unit internals: a PORTABLE learning written
through the real ``store_learning`` write-router while "in" project-A lands in
the SHARED machine-local user-home store, and a later RECALL while "in" a
*different* project-B trw_dir surfaces it via federation. This proves the write
seam (``_tier_routing.route_tier`` -> ``USER_NAMESPACE`` -> ``get_user_backend``)
and the recall seam (``_memory_recall._federate_user_tier`` -> ``peek/get_user_backend``)
agree on the same machine-local store across genuinely distinct project dirs.

Distinct from ``tests/test_recall_federation.py`` (unit) in three ways:
  * two SEPARATE on-disk project stores (real ``.trw`` dirs), not one repo;
  * the no-cross assertion uses a project-specific (path-bearing) learning that
    the heuristic routes to the PROJECT tier, proving project rows do not leak;
  * the "session_start pays nothing when the user store is absent" property is
    asserted through the REAL session-start recall path
    (``perform_session_recalls`` -> ``recall_learnings``), with a spy on the
    user-store query, so the hot-path guarantee is verified at the seam an
    operator actually hits — not by calling ``recall_learnings`` directly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import structlog

from trw_mcp.models.config import _reset_config, get_config
from trw_mcp.state import memory_adapter
from trw_mcp.state._user_tier import reset_user_backend

logger = structlog.get_logger(__name__)


@pytest.fixture(autouse=True)
def _shared_user_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """One shared machine-local user-home store for the whole test box.

    ``TRW_USER_DIR`` points every repo on the box at the same user store, which
    is the federation substrate. ``XDG_DATA_HOME`` is cleared so the resolver
    cannot fall through to a stray data dir.
    """
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


def _project_trw_dir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name / ".trw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ids(rows: list[dict[str, object]]) -> list[str]:
    return [str(r.get("id")) for r in rows]


def test_portable_learning_federates_across_distinct_project_dirs(tmp_path: Path) -> None:
    """Write portable in project-A; recall surfaces it in a DIFFERENT project-B.

    Seam: write-router routes the portable learning to the shared user store;
    the recall federation in project-B reads that same store. The two project
    stores never see the row, so the only path that can surface it is federation.
    """
    project_a = _project_trw_dir(tmp_path, "project-A")
    project_b = _project_trw_dir(tmp_path, "project-B")

    memory_adapter.store_learning(
        project_a,
        "L-portable",
        "operator prefers frequent commits cadence directive policy",
        "always commit after each logical unit of work across all repos",
        tags=["directive", "workflow"],
        source_type="human",
        impact=0.8,
    )

    # Neither project store holds the row — it routed to the shared user tier.
    assert memory_adapter.get_backend(project_a).get("L-portable") is None
    memory_adapter.reset_backend()
    assert memory_adapter.get_backend(project_b).get("L-portable") is None
    memory_adapter.reset_backend()

    rows = memory_adapter.recall_learnings(project_b, "commits cadence directive", max_results=10)
    assert "L-portable" in _ids(rows), "portable learning written in project-A must federate into project-B recall"


def test_project_specific_learning_does_not_cross_projects(tmp_path: Path) -> None:
    """A project-tier (path-bearing) learning written in A is INVISIBLE in B.

    The write-router classifies a repo-relative path as a strong PROJECT signal,
    so the row stays in project-A's local store. Recall in project-B (a distinct
    .trw dir) federates only the user store, which never received the row.
    """
    project_a = _project_trw_dir(tmp_path, "project-A")
    project_b = _project_trw_dir(tmp_path, "project-B")

    # 'auto' scope + repo-relative path => PROJECT tier (stays local to A).
    memory_adapter.store_learning(
        project_a,
        "L-proj-local",
        "the frobnitz cache lives in src/widget/frobnitz.py and must flush on boot",
        "repo-local detail about src/widget/frobnitz.py boot flush ordering",
        impact=0.9,
    )

    # It IS recallable in project-A (its own store).
    rows_a = memory_adapter.recall_learnings(project_a, "frobnitz cache flush", max_results=10)
    memory_adapter.reset_backend()
    assert "L-proj-local" in _ids(rows_a), "project-local learning must be recallable in its own repo"

    # It is NOT recallable in project-B — no federation can carry a project-tier row.
    rows_b = memory_adapter.recall_learnings(project_b, "frobnitz cache flush", max_results=10)
    assert "L-proj-local" not in _ids(rows_b), (
        "project-specific learning must NOT cross into a different project's recall"
    )


def test_precise_project_hit_outranks_federated_user_hit(tmp_path: Path) -> None:
    """A precise project hit keeps rank 1 above a federated user-tier hit.

    Tier is a re-rank FEATURE, not an override: federation appends user hits but
    a high-impact, on-topic project hit must not be displaced by a weaker
    cross-cutting user hit that merely shares a keyword.
    """
    project = _project_trw_dir(tmp_path, "project-A")

    memory_adapter.store_learning(
        project,
        "L-precise-proj",
        "configure the gizmo retry backoff in src/gizmo/retry.py",
        "the precise project answer about gizmo retry backoff tuning",
        impact=0.95,
        scope="project",
    )
    # Weak, cross-cutting user-tier hit that mentions 'gizmo' only in passing.
    memory_adapter.store_learning(
        project,
        "L-weak-user",
        "operator gizmo policy directive note",
        "low-value cross-cutting noise mentioning gizmo",
        impact=0.2,
        tags=["directive"],
        scope="user",
    )

    rows = memory_adapter.recall_learnings(project, "gizmo retry backoff", max_results=10)
    ids = _ids(rows)
    assert ids, "recall returned nothing"
    assert ids[0] == "L-precise-proj", f"precise project hit must out-rank the federated user hit (got {ids})"


def test_session_start_recall_does_not_scan_absent_user_store(tmp_path: Path) -> None:
    """The REAL session-start recall path pays nothing when the user store is absent.

    Drives ``perform_session_recalls`` (the function ``trw_session_start`` calls)
    with the user tier ENABLED-by-flag but NO user store on disk. Federation must
    short-circuit on the lazy gate (``peek_user_backend`` + on-disk probe) and
    NEVER issue a user-store query — the hot-path NFR. A spy on
    ``_query_user_backend`` proves no synchronous scan happens.
    """
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._ceremony_helpers import perform_session_recalls

    project = _project_trw_dir(tmp_path, "project-A")
    # Project-only write — the user store is never created on disk.
    memory_adapter.store_learning(
        project,
        "L-hotpath",
        "gizmo retry backoff in src/gizmo/retry.py",
        "detail",
        impact=0.9,
        scope="project",
    )
    memory_adapter.reset_backend()
    reset_user_backend()

    config = get_config()
    reader = FileStateReader()

    with patch("trw_mcp.state._memory_recall._query_user_backend") as spy_query:
        learnings, _auto, _extra = perform_session_recalls(project, "gizmo retry backoff", config, reader)

    spy_query.assert_not_called()
    assert "L-hotpath" in _ids(learnings), (
        "session-start recall must still surface the project hit without scanning the user store"
    )
