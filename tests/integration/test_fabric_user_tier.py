"""Knowledge-fabric INTEGRATION — the user tier across checkouts (PRD-CORE-185 FR05->FR06, PRD-CORE-280 FR06).

Exercises the cross-PRD wiring, not unit internals: a PORTABLE learning written
through the real ``store_learning`` write-router while "in" checkout A lands in
the daemon's ``user:local`` namespace, and a later RECALL while "in" a
*different* checkout B surfaces it. Both checkouts are pinned to their own
project namespace on ONE memory daemon and granted ``user:local``, so the write
seam (``_tier_routing.route_tier`` -> ``USER_NAMESPACE``) and the recall seam
(the store's project-then-user recall) agree on the same user namespace.

Two genuinely distinct checkouts, not one repo; the no-cross assertion uses a
project-specific (path-bearing) learning that the heuristic routes to the
PROJECT tier, proving project rows do not leak.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests._memory_fixtures import DaemonCheckout, MemoryDaemon, attach_checkout
from trw_mcp.state import memory_adapter
from trw_mcp.state._tier_routing import USER_NAMESPACE


def _ids(rows: list[dict[str, object]]) -> list[str]:
    return [str(r.get("id")) for r in rows]


@pytest.fixture
def other_checkout(daemon_checkout: DaemonCheckout, memory_daemon: MemoryDaemon, tmp_path: Path) -> Path:
    """A second checkout on the same daemon: its own project namespace, the same ``user:local``."""
    trw_dir = tmp_path / "other" / ".trw"
    attach_checkout(trw_dir, memory_daemon)
    return trw_dir


def test_portable_learning_federates_across_distinct_checkouts(
    daemon_checkout: DaemonCheckout, other_checkout: Path
) -> None:
    """Write portable in checkout A; recall surfaces it in a DIFFERENT checkout B.

    The write-router routes the portable learning to ``user:local``. Neither
    project namespace holds the row, so the only path that can surface it in B is
    the user tier.
    """
    project_a = daemon_checkout.trw_dir

    memory_adapter.store_learning(
        project_a,
        "L-portable",
        "operator prefers frequent commits cadence directive policy",
        "always commit after each logical unit of work across all repos",
        tags=["directive", "workflow"],
        source_type="human",
        impact=0.8,
    )

    rows = memory_adapter.recall_learnings(other_checkout, "commits cadence directive", max_results=10)
    assert "L-portable" in _ids(rows), "a portable learning written in A must surface in B's recall"
    held = asyncio.run(daemon_checkout.client.get("L-portable", USER_NAMESPACE))
    assert held.get("status") == "ok", "the row lives in user:local, not in either project namespace"


def test_project_specific_learning_does_not_cross_checkouts(
    daemon_checkout: DaemonCheckout, other_checkout: Path
) -> None:
    """A project-tier (path-bearing) learning written in A is INVISIBLE in B.

    The write-router classifies a repo-relative path as a strong PROJECT signal,
    so the row stays in A's project namespace, which B's grant does not reach.
    """
    project_a = daemon_checkout.trw_dir

    memory_adapter.store_learning(
        project_a,
        "L-proj-local",
        "the frobnitz cache lives in src/widget/frobnitz.py and must flush on boot",
        "repo-local detail about src/widget/frobnitz.py boot flush ordering",
        impact=0.9,
    )

    rows_a = memory_adapter.recall_learnings(project_a, "frobnitz cache flush", max_results=10)
    assert "L-proj-local" in _ids(rows_a), "a project-local learning must be recallable in its own checkout"

    rows_b = memory_adapter.recall_learnings(other_checkout, "frobnitz cache flush", max_results=10)
    assert "L-proj-local" not in _ids(rows_b), "a project-specific learning must NOT cross into another checkout"


def test_precise_project_hit_outranks_federated_user_hit(daemon_checkout: DaemonCheckout) -> None:
    """A precise project hit keeps rank 1 above a weaker user-tier hit that shares a keyword."""
    project = daemon_checkout.trw_dir

    memory_adapter.store_learning(
        project,
        "L-precise-proj",
        "configure the gizmo retry backoff in src/gizmo/retry.py",
        "the precise project answer about gizmo retry backoff tuning",
        impact=0.95,
        scope="project",
    )
    memory_adapter.store_learning(
        project,
        "L-weak-user",
        "operator gizmo policy directive note",
        "low-value cross-cutting noise mentioning gizmo",
        impact=0.2,
        tags=["directive"],
        scope="user",
    )

    ids = _ids(memory_adapter.recall_learnings(project, "gizmo retry backoff", max_results=10))
    assert ids, "recall returned nothing"
    assert ids[0] == "L-precise-proj", f"the precise project hit must out-rank the user hit (got {ids})"
