"""learning L-LhQe -- the ``memory_warm_legacy`` doctor row.

Flags a leftover pre-fix per-namespace ``warm.db`` (unused, safe to delete --
the warm cache rebuilds from the canonical backend) and a stranded cold
archive (DATA, never delete -- move or copy it) at the OLD tier root. This
row itself only ever reads the filesystem. Exercised through the real doctor
catalogue (``_doctor_core``), so a row that exists but is never registered
fails here, same shape as ``test_doctor_wal_line.py``.

Codex r2: the row must use trw-memory's own ``legacy_tier_dirs`` mapping, so a
healthy, fully-migrated store (where ``storage_path`` and
``memory_single_store_path``'s directory already coincide -- the normal daemon
layout) never treats its ACTIVE per-namespace directories as legacy orphans.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("stub_cli_version_probes")


@pytest.fixture(autouse=True)
def _isolate_user_memory_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("MEMORY_SINGLE_STORE_PATH", raising=False)
    monkeypatch.delenv("MEMORY_STORAGE_PATH", raising=False)


def _user_memory_dir(tmp_path: Path) -> Path:
    """``resolve_user_memory_dir()`` under the isolated ``TRW_USER_DIR``."""
    return tmp_path / "memory"


def _row(target: Path, name: str = "memory_warm_legacy") -> object:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._subcommands_doctor import _doctor_core

    results = _doctor_core(target, TRWConfig())
    matches = [r for r in results if r.name == name]
    assert matches, f"doctor produced no {name} row; got {[r.name for r in results]}"
    return matches[0]


def _project(tmp_path: Path) -> Path:
    target = tmp_path / "proj"
    (target / ".trw").mkdir(parents=True, exist_ok=True)
    return target


def test_no_single_store_configured_is_a_clean_pass(tmp_path: Path) -> None:
    target = _project(tmp_path)

    row = _row(target)

    assert row.status == "PASS"
    assert "memory_single_store_path" in row.message


def test_no_leftover_orphan_is_a_clean_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_SINGLE_STORE_PATH", str(_user_memory_dir(tmp_path) / "memory.db"))
    target = _project(tmp_path)

    row = _row(target)

    assert row.status == "PASS"
    assert "no leftover legacy" in row.message


def test_a_healthy_migrated_store_is_a_clean_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex r2, case (a): the normal daemon layout (storage_path == the single
    store's own directory) means every per-namespace dir found IS the active
    target, never a legacy orphan. Fails on ff9f70f4f, which flagged these."""
    user_dir = _user_memory_dir(tmp_path)
    monkeypatch.setenv("MEMORY_SINGLE_STORE_PATH", str(user_dir / "memory.db"))
    target = _project(tmp_path)
    active_warm = user_dir / "default" / "memory" / "warm.db"
    active_warm.parent.mkdir(parents=True, exist_ok=True)
    active_warm.write_bytes(b"an ACTIVE warm.db, not a legacy orphan")
    active_cold = user_dir / "default" / "entries"
    active_cold.mkdir(parents=True, exist_ok=True)
    (active_cold / "archived-entry.yaml").write_text("id: M-1\n", encoding="utf-8")

    row = _row(target)

    assert row.status == "PASS", row.message
    assert "no leftover legacy" in row.message
    assert active_warm.exists()
    assert (active_cold / "archived-entry.yaml").exists()


def test_a_leftover_orphan_warns_and_names_both_the_legacy_and_target_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r2, case (b): a real legacy leftover, at a root that DIVERGES from
    the single store's own directory."""
    user_dir = _user_memory_dir(tmp_path)
    diverged_single_store = tmp_path / "elsewhere" / "memory.db"
    monkeypatch.setenv("MEMORY_SINGLE_STORE_PATH", str(diverged_single_store))
    target = _project(tmp_path)
    orphan = user_dir / "default" / "memory" / "warm.db"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"legacy sqlite bytes")

    row = _row(target)

    assert row.status == "WARN"
    assert str(orphan) in row.message
    assert "rebuilds automatically from the canonical store" in row.message
    assert "you may delete it" in row.message
    assert orphan.exists(), "the doctor row must never delete the orphan it reports"


def test_a_stranded_cold_archive_at_a_diverged_root_warns_and_names_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r2, case (c): a stranded cold archive at a legacy dir that differs from the target."""
    user_dir = _user_memory_dir(tmp_path)
    diverged_single_store = tmp_path / "elsewhere" / "memory.db"
    monkeypatch.setenv("MEMORY_SINGLE_STORE_PATH", str(diverged_single_store))
    target = _project(tmp_path)
    cold_dir = user_dir / "default" / "entries"
    cold_dir.mkdir(parents=True, exist_ok=True)
    (cold_dir / "archived-entry.yaml").write_text("id: M-1\n", encoding="utf-8")
    expected_target = tmp_path / "elsewhere" / "default" / "entries"

    row = _row(target)

    assert row.status == "WARN"
    assert str(cold_dir) in row.message
    assert str(expected_target) in row.message
    assert "archived entries not yet visible" in row.message
    assert "never delete it" in row.message
    assert (cold_dir / "archived-entry.yaml").exists(), "the doctor row must never touch the archive it reports"
