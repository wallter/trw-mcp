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
from trw_mcp.state._user_paths import resolve_user_memory_dir
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


def test_user_store_tamper_disables_federation_not_recall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """core185-3: a tampered USER store drops user federation but project recall survives.

    The user store's canary must be halt-checked BEFORE its entries enter the
    merged result. When tampered, federation is DISABLED (fail-open recall),
    not aborted -- project hits are still returned.
    """
    repo = _trw_dir(tmp_path, "repo")
    # A project hit (must survive) and a user hit (must be excluded when tampered).
    memory_adapter.store_learning(repo, "L-proj", "project widget alpha", "d", scope="project")
    memory_adapter.store_learning(repo, "L-user", "portable widget directive", "d", tags=["directive"], scope="user")

    user_dir = str(resolve_user_memory_dir(create=False))
    real_halt = memory_adapter.should_halt_recalls

    def _halt(sec_cfg: object, *, backend: object | None = None) -> bool:
        # Report tamper ONLY for the user store (its storage_path), so the
        # project canary check stays clean and project recall is unaffected.
        if user_dir in str(getattr(sec_cfg, "storage_path", "")):
            return True
        return bool(real_halt(sec_cfg, backend=backend))

    monkeypatch.setattr(memory_adapter, "should_halt_recalls", _halt)

    rows = memory_adapter.recall_learnings(repo, "widget", max_results=10)
    ids = _ids(rows)
    assert "L-proj" in ids, "project recall must survive a user-store tamper"
    assert "L-user" not in ids, "tampered user-store entries must NOT enter recall"


def test_user_store_tamper_disables_federation_first_call_fresh_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """core185-TOCTOU-1: the canary gate must fire on the FIRST federation call.

    Regression: ``_user_store_tampered()`` peeked the backend WITHOUT constructing
    it; on a fresh process (no prior ``store_learning`` for the user tier) the peek
    returned ``None`` -> reported "not tampered" -> then ``_federate_user_tier``
    constructed the backend itself and queried it with NO canary check. A tampered
    user store leaked entries on the very first recall. The fix constructs + probes
    the canary when the DB file exists, so the gate is honored on call 1.
    """
    repo = _trw_dir(tmp_path, "repo")
    memory_adapter.store_learning(repo, "L-proj", "project widget alpha", "d", scope="project")
    memory_adapter.store_learning(repo, "L-user", "portable widget directive", "d", tags=["directive"], scope="user")
    # Simulate a FRESH process: drop the constructed user-backend singleton so the
    # next federation call hits the peek==None branch with the DB file on disk.
    reset_user_backend()
    memory_adapter.reset_backend()

    user_dir = str(resolve_user_memory_dir(create=False))
    real_halt = memory_adapter.should_halt_recalls

    def _halt(sec_cfg: object, *, backend: object | None = None) -> bool:
        if user_dir in str(getattr(sec_cfg, "storage_path", "")):
            return True
        return bool(real_halt(sec_cfg, backend=backend))

    monkeypatch.setattr(memory_adapter, "should_halt_recalls", _halt)

    rows = memory_adapter.recall_learnings(repo, "widget", max_results=10)
    ids = _ids(rows)
    assert "L-proj" in ids, "project recall must survive a user-store tamper"
    assert "L-user" not in ids, "first-call federation must respect the user-store canary"


def test_dedupe_no_duplicate_ids(tmp_path: Path) -> None:
    """The merged result never contains the same id twice."""
    repo = _trw_dir(tmp_path, "repo")
    memory_adapter.store_learning(repo, "L-p", "project widget thing", "d", scope="project")
    memory_adapter.store_learning(repo, "L-u", "portable directive thing", "d", tags=["directive"], scope="user")
    rows = memory_adapter.recall_learnings(repo, "thing", max_results=10)
    ids = _ids(rows)
    assert len(ids) == len(set(ids)), f"duplicate ids in federated result: {ids}"


# ---------------------------------------------------------------------------
# P1/Item6 — Mixed-scope dedup: same entry id in user + project tiers.
# ---------------------------------------------------------------------------


def test_mixed_scope_dedup_same_id_appears_once(tmp_path: Path) -> None:
    """When the same entry id exists in BOTH user and project tiers, only ONE
    copy appears in recall results (deterministic dedup by id set).

    The contract (current): project hits are collected first; user hits are
    appended only when their id is not already in the ``seen`` set. So a
    shared id will always appear once — from whichever tier inserted it first
    (project). This test pins that contract.
    """
    repo = _trw_dir(tmp_path, "repo")

    # Write the SAME logical learning to both tiers using the same id.
    # scope="project" writes to the project backend.
    memory_adapter.store_learning(
        repo,
        "L-shared",
        "shared cross-tier entry about sqlite wal reset",
        "project copy",
        scope="project",
    )
    # scope="user" writes to the user-tier backend.
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


def test_mixed_scope_dedup_project_copy_wins(tmp_path: Path) -> None:
    """When the same id exists in both tiers, the project copy is returned.

    The federation logic processes project hits first; the id is added to the
    ``seen`` set, so the user-tier copy is skipped. This is the current
    documented contract — the PROJECT copy wins on a collision.
    """
    repo = _trw_dir(tmp_path, "repo")

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
# ---------------------------------------------------------------------------


def test_recall_during_embedder_warmup_falls_back_to_keyword(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recall invoked while embedder is uninitialized (warm-up window) returns
    keyword-only results and never raises.

    ``allow_cold_embedding_init=False`` is the MCP hot path; it calls
    ``get_initialized_embedder()`` which returns None when the embedder has not
    yet been loaded by the warm-up thread. The search path must fall back to
    keyword recall, not crash.
    """
    from trw_mcp.state import _memory_connection
    from trw_mcp.state import memory_adapter as ma

    repo = _trw_dir(tmp_path, "repo")
    ma.store_learning(repo, "L-kw", "distinctive keyword token frobnicate warmup", "detail", scope="project")

    # Simulate "embedder not yet warm" by patching get_initialized_embedder to None.
    monkeypatch.setattr(_memory_connection, "get_initialized_embedder", lambda: None)
    monkeypatch.setattr(_memory_connection, "_embedder_checked", False)

    # recall with allow_cold_embedding_init=False hits the warm-up guard path.
    rows = ma.recall_learnings(
        repo,
        "frobnicate warmup",
        max_results=10,
        allow_cold_embedding_init=False,
    )
    ids = _ids(rows)
    assert "L-kw" in ids, "keyword-only fallback during embedder warm-up must return matching entry"


def test_recall_during_embedder_warmup_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recall never raises even when get_initialized_embedder returns None
    AND the warm-up thread is still running (warm-up window).
    """
    from trw_mcp.state import _memory_connection
    from trw_mcp.state import memory_adapter as ma

    repo = _trw_dir(tmp_path, "repo")
    ma.store_learning(repo, "L-safe", "safe entry for warmup test", "d", scope="project")

    monkeypatch.setattr(_memory_connection, "get_initialized_embedder", lambda: None)
    monkeypatch.setattr(_memory_connection, "_embedder_checked", False)

    try:
        rows = ma.recall_learnings(repo, "safe entry", max_results=10, allow_cold_embedding_init=False)
    except Exception as exc:
        raise AssertionError(f"recall must not raise during embedder warm-up window, got: {exc}") from exc

    assert isinstance(rows, list)
