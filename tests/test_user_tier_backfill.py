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
