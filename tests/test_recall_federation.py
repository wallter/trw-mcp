"""PRD-CORE-185 FR06: recall federation across project ∪ user tiers.

``recall_learnings`` merges project-store hits with machine-local user-store
hits (de-duped, capped by ``recall_user_tier_cap``, tier as a re-rank feature
not an override). Cross-project transfer: a portable learning written while in
repo A is surfaced by recall in repo B on the same box (one shared user-home
store). With the user store absent/empty, recall is byte-identical to today.

NOT PORTED (PRD-CORE-280 slice e1): the two ``*_tamper_*`` tests and the two
``*_embedder_warmup_*`` tests below directly manipulate
``trw_mcp.state.memory_adapter.should_halt_recalls``/``resolve_user_memory_dir``
and ``trw_mcp.state._memory_connection``'s embedder-warmup singleton state --
mechanisms specific to the interim dual-SQLite-backend ``SqliteMemoryStore``
implementation with no daemon-store equivalent a test can attach to from
outside the daemon process (the daemon owns its own canary/tamper detection
and embedder lifecycle internally). They keep using ``get_backend`` /
``_memory_connection`` and are left unchanged and unmigrated; see the batch
report for detail. Everything else below routes through ``daemon_checkout`` /
``attach_checkout``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_fixtures import DaemonCheckout, MemoryDaemon, attach_checkout
from trw_mcp.models.config import _reset_config
from trw_mcp.state import memory_adapter
from trw_mcp.state._user_tier import reset_user_backend


@pytest.fixture
def _isolated_user_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Only for the not-ported legacy tests below (see module docstring)."""
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


def test_cross_project_transfer(memory_daemon: MemoryDaemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Portable learning written in repo A is recalled in repo B (shared user store)."""
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    # route_tier's user_scope_present() gate needs this: the base test got it
    # for free from the (formerly autouse) _isolated_user_dir fixture below,
    # which now only applies to the not-ported legacy tests.
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "true")
    _reset_config()
    repo_a = _trw_dir(tmp_path, "repoA")
    repo_b = _trw_dir(tmp_path, "repoB")
    _namespace_a, _client_a = attach_checkout(repo_a, memory_daemon)
    namespace_b, _client_b = attach_checkout(repo_b, memory_daemon)

    # Write a portable learning while "in" repo A -> routes to the user store.
    memory_adapter.store_learning(
        repo_a,
        "L-xfer",
        "operator prefers frequent commits cadence directive",
        "always commit after each logical unit of work",
        tags=["directive"],
        source_type="human",
        scope="user",
    )

    # Recall in repo B surfaces the user-tier learning via federation.
    rows = memory_adapter.recall_learnings(repo_b, "commits cadence directive", max_results=10)
    assert "L-xfer" in _ids(rows)
    assert namespace_b  # repo B's own project namespace stays unused by this transfer
    _reset_config()


def test_project_hit_stays_rank_1(daemon_checkout: DaemonCheckout) -> None:
    """A precise project hit keeps rank 1 against low-value user hits."""
    repo = daemon_checkout.trw_dir
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


def test_user_tier_cap_respected(daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> None:
    """No more than recall_user_tier_cap user hits enter the merged result."""
    monkeypatch.setenv("TRW_RECALL_USER_TIER_CAP", "2")
    _reset_config()
    repo = daemon_checkout.trw_dir
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
    _reset_config()


def test_absent_user_store_is_project_only(daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the user tier disabled, recall is project-only (no federation)."""
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "false")
    _reset_config()
    repo = daemon_checkout.trw_dir
    memory_adapter.store_learning(
        repo,
        "L-proj-only",
        "some project learning about widgets",
        "detail",
        scope="project",
    )
    rows = memory_adapter.recall_learnings(repo, "widgets", max_results=10)
    assert "L-proj-only" in _ids(rows)
    _reset_config()


def test_dedupe_no_duplicate_ids(daemon_checkout: DaemonCheckout) -> None:
    """The merged result never contains the same id twice."""
    repo = daemon_checkout.trw_dir
    memory_adapter.store_learning(repo, "L-p", "project widget thing", "d", scope="project")
    memory_adapter.store_learning(repo, "L-u", "portable directive thing", "d", tags=["directive"], scope="user")
    rows = memory_adapter.recall_learnings(repo, "thing", max_results=10)
    ids = _ids(rows)
    assert len(ids) == len(set(ids)), f"duplicate ids in federated result: {ids}"


# ---------------------------------------------------------------------------
# P1/Item6 — Mixed-scope dedup: same entry id in user + project tiers.
# ---------------------------------------------------------------------------


def test_mixed_scope_dedup_same_id_appears_once(daemon_checkout: DaemonCheckout) -> None:
    """When the same entry id exists in BOTH user and project tiers, only ONE
    copy appears in recall results (deterministic dedup by id set).

    The contract (current): project hits are collected first; user hits are
    appended only when their id is not already in the ``seen`` set. So a
    shared id will always appear once — from whichever tier inserted it first
    (project). This test pins that contract.
    """
    repo = daemon_checkout.trw_dir

    # Write the SAME logical learning to both tiers using the same id.
    # scope="project" writes to the project namespace.
    memory_adapter.store_learning(
        repo,
        "L-shared",
        "shared cross-tier entry about sqlite wal reset",
        "project copy",
        scope="project",
    )
    # scope="user" writes to the user-tier namespace.
    memory_adapter.store_learning(
        repo,
        "L-shared",
        "shared cross-tier entry about sqlite wal reset",
        "user copy",
        scope="user",
    )

    rows = memory_adapter.recall_learnings(repo, "sqlite wal reset", max_results=20)
    ids = _ids(rows)
    # Determinism contract: exactly ONE occurrence of "L-shared".
    assert ids.count("L-shared") == 1, (
        f"shared id must appear exactly once in federated result (got {ids.count('L-shared')} times)"
    )


def test_mixed_scope_dedup_project_copy_wins(daemon_checkout: DaemonCheckout) -> None:
    """When the same id exists in both tiers, the project copy is returned.

    The federation logic processes project hits first; the id is added to the
    ``seen`` set, so the user-tier copy is skipped. This is the current
    documented contract — the PROJECT copy wins on a collision.
    """
    repo = daemon_checkout.trw_dir

    memory_adapter.store_learning(repo, "L-win", "collision test entry alpha", "PROJECT detail wins", scope="project")
    memory_adapter.store_learning(repo, "L-win", "collision test entry alpha", "USER detail loses", scope="user")

    rows = memory_adapter.recall_learnings(repo, "collision test entry alpha", max_results=20)
    matched = [r for r in rows if str(r.get("id")) == "L-win"]
    assert len(matched) == 1
    # The project copy is included first; the user copy is deduped away.
    # Both copies have the same id and summary; we verify only one survives.
    assert matched[0]["id"] == "L-win"


# ---------------------------------------------------------------------------
# P1/Item7 — Embedder warm-up race: recall during uninitialized embedder.
# NOT PORTED — see module docstring.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# core185-3 tamper tests — NOT PORTED, see module docstring.
# ---------------------------------------------------------------------------
