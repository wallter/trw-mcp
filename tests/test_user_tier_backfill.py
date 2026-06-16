"""PRD-CORE-185 FR08: opt-in, non-destructive backfill / reclassification.

``reclassify_to_user_tier`` reclassifies EXISTING high-portability project-tier
learnings into the machine-local user tier. Properties:

* **Default OFF** — only runs when explicitly invoked (it is not wired into any
  normal session path).
* **Non-destructive** — copies qualifying entries into the user store; NEVER
  deletes project-tier data unless an explicit ``move=True`` confirmation flag
  is set.
* **Idempotent** — re-running is a no-op (already-promoted entries skipped).
* **Dry-run** — reports candidates without writing.
* Reuses the FR05 portability heuristic (does not fork the classifier) plus a
  conservative impact floor.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import _reset_config
from trw_mcp.state import memory_adapter
from trw_mcp.state._tier_routing import USER_NAMESPACE
from trw_mcp.state._user_tier import get_user_backend, reset_user_backend


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


def _trw_dir(tmp_path: Path) -> Path:
    d = tmp_path / "repo" / ".trw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _seed_project(trw_dir: Path, lid: str, summary: str, detail: str, **kw: object) -> None:
    """Write directly into the PROJECT store (scope='project' forces it)."""
    memory_adapter.store_learning(trw_dir, lid, summary, detail, scope="project", **kw)


def _user_ids() -> list[str]:
    backend = get_user_backend()
    return [e.id for e in backend.list_entries(namespace=USER_NAMESPACE, limit=1000)]


# --------------------------------------------------------------------------- #
# Dry-run reports, writes nothing
# --------------------------------------------------------------------------- #


def test_dry_run_reports_candidates_writes_nothing(tmp_path: Path) -> None:
    trw_dir = _trw_dir(tmp_path)
    _seed_project(
        trw_dir,
        "L-portable",
        "operator directive: always commit frequently across all repos",
        "portable cross-cutting workflow directive",
        tags=["directive"],
        source_type="human",
        impact=0.8,
    )
    reset_user_backend()

    report = memory_adapter.reclassify_to_user_tier(trw_dir, dry_run=True)
    assert "L-portable" in report["candidates"]
    assert report["promoted"] == []  # nothing written in dry-run
    # The user store holds nothing.
    assert _user_ids() == []
    # Project entry untouched.
    assert memory_adapter.get_backend(trw_dir).get("L-portable") is not None


# --------------------------------------------------------------------------- #
# Real run copies portable entries, never deletes project data
# --------------------------------------------------------------------------- #


def test_real_run_copies_portable_keeps_project(tmp_path: Path) -> None:
    trw_dir = _trw_dir(tmp_path)
    _seed_project(
        trw_dir,
        "L-portable",
        "operator directive prefers larger models for everything",
        "portable cross-cutting directive",
        tags=["directive"],
        source_type="human",
        impact=0.8,
    )
    # A clearly project-specific entry must NOT be promoted.
    _seed_project(
        trw_dir,
        "L-projspecific",
        "fix the guard in trw_mcp/state/foo.py:42 for this repo",
        "repo-relative path keeps it project-tier",
        impact=0.8,
    )
    reset_user_backend()

    report = memory_adapter.reclassify_to_user_tier(trw_dir, dry_run=False)
    assert "L-portable" in report["promoted"]
    assert "L-projspecific" not in report["promoted"]
    # Copied into the user store...
    assert "L-portable" in _user_ids()
    # ...and NOT deleted from the project store (non-destructive copy).
    assert memory_adapter.get_backend(trw_dir).get("L-portable") is not None
    assert memory_adapter.get_backend(trw_dir).get("L-projspecific") is not None


# --------------------------------------------------------------------------- #
# Impact floor filters low-value portable entries
# --------------------------------------------------------------------------- #


def test_impact_floor_excludes_low_value(tmp_path: Path) -> None:
    trw_dir = _trw_dir(tmp_path)
    _seed_project(
        trw_dir,
        "L-lowval",
        "operator directive: minor portable note",
        "portable but low impact",
        tags=["directive"],
        source_type="human",
        impact=0.1,
    )
    reset_user_backend()

    report = memory_adapter.reclassify_to_user_tier(trw_dir, dry_run=False, min_impact=0.5)
    assert "L-lowval" not in report["promoted"]
    assert "L-lowval" not in _user_ids()


# --------------------------------------------------------------------------- #
# Idempotent: re-running promotes nothing new
# --------------------------------------------------------------------------- #


def test_idempotent(tmp_path: Path) -> None:
    trw_dir = _trw_dir(tmp_path)
    _seed_project(
        trw_dir,
        "L-portable",
        "operator directive: commit frequently everywhere",
        "portable directive",
        tags=["directive"],
        source_type="human",
        impact=0.8,
    )
    reset_user_backend()

    first = memory_adapter.reclassify_to_user_tier(trw_dir, dry_run=False)
    assert "L-portable" in first["promoted"]
    ids_after_first = sorted(_user_ids())

    # Second run must be a no-op (already promoted -> skipped).
    second = memory_adapter.reclassify_to_user_tier(trw_dir, dry_run=False)
    assert second["promoted"] == []
    assert "L-portable" in second["skipped"]
    assert sorted(_user_ids()) == ids_after_first  # no duplicates


def test_idempotent_when_user_store_exceeds_scan_limit(tmp_path: Path) -> None:
    """core185-5: the user-store dedup fetch must not be truncated by ``limit``.

    A long-lived box-wide user store accumulates entries from MANY projects. When
    those (newer) unrelated user entries push an already-promoted project copy
    beyond the ``limit`` window, the dedup set must still contain it -- otherwise
    the next backfill RE-PROMOTES it (duplicate). Regression guard: the user-id
    fetch previously shared ``limit`` with the project scan.
    """
    from trw_memory.models.memory import MemoryEntry

    trw_dir = _trw_dir(tmp_path)
    _seed_project(
        trw_dir,
        "L-port-old",
        "operator directive: commit frequently everywhere",
        "portable directive",
        tags=["directive"],
        source_type="human",
        impact=0.8,
    )
    reset_user_backend()

    # First pass promotes the project entry into the user store.
    first = memory_adapter.reclassify_to_user_tier(trw_dir, dry_run=False, limit=100)
    assert "L-port-old" in first["promoted"]

    # Simulate a box-wide store that accumulated NEWER unrelated user entries
    # from other projects -- these sort ahead of the promoted copy (updated_at
    # DESC), so a truncated user-id fetch would miss the promoted L-port-old.
    user_backend = get_user_backend()
    for i in range(10):
        user_backend.store(
            MemoryEntry(
                id=f"U-other{i}",
                content=f"unrelated user note {i}",
                detail="from another repo",
                namespace=USER_NAMESPACE,
            )
        )
    ids_after_first = sorted(_user_ids())

    # Re-run with limit=6: enough to scan past the 5 project canaries to
    # L-port-old (so it IS a candidate), but a truncated user-id fetch (limit=6
    # against 11 user entries) returns only the 6 NEWEST (the U-other*), MISSING
    # the promoted L-port-old -> the bug re-promotes it.
    second = memory_adapter.reclassify_to_user_tier(trw_dir, dry_run=False, limit=6)
    assert second["promoted"] == [], f"already-promoted entry was re-promoted: {second['promoted']}"
    assert "L-port-old" in second["skipped"]
    assert sorted(_user_ids()) == ids_after_first, "user store gained duplicate entries"


# --------------------------------------------------------------------------- #
# Move flag deletes project entry only with explicit confirmation
# --------------------------------------------------------------------------- #


def test_move_flag_deletes_project_entry(tmp_path: Path) -> None:
    trw_dir = _trw_dir(tmp_path)
    _seed_project(
        trw_dir,
        "L-portable",
        "operator directive: commit frequently everywhere",
        "portable directive",
        tags=["directive"],
        source_type="human",
        impact=0.8,
    )
    reset_user_backend()

    report = memory_adapter.reclassify_to_user_tier(trw_dir, dry_run=False, move=True)
    assert "L-portable" in report["promoted"]
    assert "L-portable" in _user_ids()
    # With move=True the project copy is removed (explicit confirmation).
    assert memory_adapter.get_backend(trw_dir).get("L-portable") is None


# --------------------------------------------------------------------------- #
# PRD-CORE-185: update_learning must reach the USER store, not only project.
# Regression for the bug where update_learning queried ONLY the project backend
# and returned not_found for a user-tier entry (so user learnings were
# permanently un-updatable).
# --------------------------------------------------------------------------- #


def test_update_learning_updates_user_tier_entry(tmp_path: Path) -> None:
    """A user-tier learning can be updated (was silently not_found before)."""
    trw_dir = _trw_dir(tmp_path)
    # Force the entry into the box-wide USER store.
    memory_adapter.store_learning(
        trw_dir,
        "L-user-upd",
        "operator directive: prefer larger models everywhere",
        "portable cross-cutting directive",
        scope="user",
        tags=["directive"],
        source_type="human",
        impact=0.5,
    )
    # Sanity: it landed in the user store, not the project store.
    assert "L-user-upd" in _user_ids()
    assert memory_adapter.get_backend(trw_dir).get("L-user-upd") is None

    result = memory_adapter.update_learning(trw_dir, "L-user-upd", impact=0.9, status="resolved")
    assert result["status"] == "updated", result

    # The user-store row actually changed.
    updated = get_user_backend().get("L-user-upd")
    assert updated is not None
    assert updated.importance == 0.9
    assert str(updated.status) in {"resolved", "MemoryStatus.RESOLVED"}


def test_update_learning_still_updates_project_tier_entry(tmp_path: Path) -> None:
    """Project-tier updates remain byte-identical (project backend wins first)."""
    trw_dir = _trw_dir(tmp_path)
    _seed_project(trw_dir, "L-proj-upd", "project finding in trw_mcp/state/x.py:1", "d", impact=0.4)
    assert memory_adapter.get_backend(trw_dir).get("L-proj-upd") is not None

    result = memory_adapter.update_learning(trw_dir, "L-proj-upd", impact=0.7)
    assert result["status"] == "updated", result
    assert memory_adapter.get_backend(trw_dir).get("L-proj-upd").importance == 0.7


def test_update_learning_missing_in_both_returns_not_found(tmp_path: Path) -> None:
    """A truly absent id still returns not_found after consulting both stores."""
    trw_dir = _trw_dir(tmp_path)
    result = memory_adapter.update_learning(trw_dir, "L-nope", impact=0.5)
    assert result["status"] == "not_found"


# --------------------------------------------------------------------------- #
# update_learning enum validation (defense-in-depth at the state layer).
# --------------------------------------------------------------------------- #


def test_update_learning_rejects_invalid_confidence(tmp_path: Path) -> None:
    trw_dir = _trw_dir(tmp_path)
    _seed_project(trw_dir, "L-conf", "s", "d")
    result = memory_adapter.update_learning(trw_dir, "L-conf", confidence="bogus")
    assert result["status"] == "invalid"
    assert "confidence" in result["error"]
    # Nothing persisted.
    assert memory_adapter.get_backend(trw_dir).get("L-conf").confidence != "bogus"


def test_update_learning_accepts_valid_confidence(tmp_path: Path) -> None:
    trw_dir = _trw_dir(tmp_path)
    _seed_project(trw_dir, "L-conf-ok", "s", "d")
    result = memory_adapter.update_learning(trw_dir, "L-conf-ok", confidence="high")
    assert result["status"] == "updated"


def test_update_learning_rejects_invalid_protection_tier(tmp_path: Path) -> None:
    trw_dir = _trw_dir(tmp_path)
    _seed_project(trw_dir, "L-tier", "s", "d")
    result = memory_adapter.update_learning(trw_dir, "L-tier", protection_tier="ultra")
    assert result["status"] == "invalid"
    assert "protection_tier" in result["error"]
