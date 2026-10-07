"""`backup restore` validates its source before it touches, or even archives, the live store (INC-127, INC-129 a/b/c)."""

from __future__ import annotations

import argparse
import gzip
import hashlib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("governing_project")]


@pytest.fixture(autouse=True)
def _serves_nothing_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The machine's real served store is never consulted: a test says which file it serves with ``_serve``."""
    from trw_mcp.server import _subcommands_backup

    monkeypatch.setattr(_subcommands_backup, "_served_store", lambda: (tmp_path / "served-elsewhere.db").resolve())


def _serve(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    from trw_mcp.server import _subcommands_backup

    monkeypatch.setattr(_subcommands_backup, "_served_store", lambda: db_path.resolve())


def _store(tmp_path: Path) -> Path:
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    db_path = tmp_path / "memory.db"
    backend = SQLiteBackend(db_path)
    backend.store(MemoryEntry(id="M-planted-001", content="must survive a rejected restore", namespace="default"))
    backend.close()
    return db_path


def _restore(db_path: Path, source: Path, **extra: object) -> None:
    from trw_mcp.server import _subcommands_backup

    _subcommands_backup.run_backup(
        argparse.Namespace(
            backup_command="restore",
            yes=True,
            restore_from=str(source),
            namespace="default",
            db=str(db_path),
            **extra,
        )
    )


def _archives(tmp_path: Path) -> list[Path]:
    backups = tmp_path / "memory" / "backups"
    return sorted(backups.glob("*")) if backups.exists() else []


def _refused(tmp_path: Path, source: Path, capsys: pytest.CaptureFixture[str], reason: str) -> None:
    db_path = _store(tmp_path)
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    with pytest.raises(SystemExit) as exit_info:
        _restore(db_path, source)
    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert reason in err.lower() and "traceback" not in err.lower()
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before  # byte-identical
    assert _archives(tmp_path) == []  # no pre-restore archive for a restore that never swapped


def test_a_non_sqlite_gzip_is_refused_before_anything_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junk = tmp_path / "junk.db.gz"
    junk.write_bytes(gzip.compress(b"not sqlite"))
    _refused(tmp_path, junk, capsys, "not a sqlite database")


def test_a_truncated_archive_is_refused_cleanly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cut = tmp_path / "cut.db.gz"
    cut.write_bytes(gzip.compress(b"x" * 100000)[:30])
    _refused(tmp_path, cut, capsys, "truncated")


def test_a_symlinked_archive_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from trw_memory.storage._backup_archive import create_backup_archive

    donor_dir = tmp_path / "donor"
    donor_dir.mkdir()
    real = create_backup_archive(donor_dir, _store(donor_dir)).path
    link = tmp_path / "link.db.gz"
    link.symlink_to(real)
    _refused(tmp_path, link, capsys, "symlink")


def test_a_nonexistent_source_writes_no_archive(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _refused(tmp_path, tmp_path / "missing.db.gz", capsys, "does not exist")


def _project_with_derived_tiers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from trw_mcp.server import _subcommands_backup

    trw_dir = tmp_path / "project" / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "memory" / "warm.jsonl").write_text('{"id": "L-post"}\n{"id": "L-post2"}\n', encoding="utf-8")
    (trw_dir / "memory" / "warm.db").write_bytes(b"warm vector index")
    (trw_dir / "learnings" / "entries" / "2026-01-01-post.yaml").write_text("id: L-post\n", encoding="utf-8")
    (trw_dir / "learnings" / "index.yaml").write_text("entries: []\n", encoding="utf-8")
    monkeypatch.setattr(_subcommands_backup, "_invoking_trw_dir", lambda: trw_dir)
    return trw_dir


def test_a_restore_moves_the_derived_copies_aside_so_recall_matches_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_memory.storage._backup_archive import create_backup_archive

    trw_dir = _project_with_derived_tiers(tmp_path, monkeypatch)
    db_path = _store(tmp_path)
    _serve(monkeypatch, db_path)
    _restore(db_path, create_backup_archive(tmp_path, db_path).path)

    out = capsys.readouterr().out
    assert "Moved aside (not deleted)" in out and "to undo: cp -Rp" in out
    assert not (trw_dir / "memory" / "warm.jsonl").exists() and not (trw_dir / "memory" / "warm.db").exists()
    assert list((trw_dir / "learnings" / "entries").iterdir()) == []  # the directory stays, empty
    assert not (trw_dir / "learnings" / "index.yaml").exists()
    [aside] = [p for p in trw_dir.iterdir() if p.name.startswith("pre-restore-derived-")]
    assert (aside / "memory" / "warm.jsonl").read_text(encoding="utf-8").count("L-post") == 2  # kept, not deleted
    assert (aside / "learnings" / "entries" / "2026-01-01-post.yaml").is_file()


def test_keep_derived_leaves_them_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_memory.storage._backup_archive import create_backup_archive

    trw_dir = _project_with_derived_tiers(tmp_path, monkeypatch)
    db_path = _store(tmp_path)
    _serve(monkeypatch, db_path)
    _restore(db_path, create_backup_archive(tmp_path, db_path).path, keep_derived=True)

    assert "--keep-derived" in capsys.readouterr().out
    assert (trw_dir / "memory" / "warm.jsonl").is_file()
    assert (trw_dir / "learnings" / "entries" / "2026-01-01-post.yaml").is_file()
    assert not [p for p in trw_dir.iterdir() if p.name.startswith("pre-restore-derived-")]


def test_a_symlinked_derived_tier_is_left_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_memory.storage._backup_archive import create_backup_archive

    trw_dir = _project_with_derived_tiers(tmp_path, monkeypatch)
    elsewhere = tmp_path / "elsewhere.jsonl"
    elsewhere.write_text("outside\n", encoding="utf-8")
    (trw_dir / "memory" / "warm.jsonl").unlink()
    (trw_dir / "memory" / "warm.jsonl").symlink_to(elsewhere)
    db_path = _store(tmp_path)
    _serve(monkeypatch, db_path)
    _restore(db_path, create_backup_archive(tmp_path, db_path).path)

    assert "a symlink, not followed" in capsys.readouterr().out
    assert elsewhere.read_text(encoding="utf-8") == "outside\n"


def test_a_restore_with_no_derived_copies_prints_nothing_about_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_memory.storage._backup_archive import create_backup_archive

    from trw_mcp.server import _subcommands_backup

    monkeypatch.setattr(_subcommands_backup, "_invoking_trw_dir", lambda: tmp_path / "empty" / ".trw")
    db_path = _store(tmp_path)
    _restore(db_path, create_backup_archive(tmp_path, db_path).path)
    out = capsys.readouterr().out
    assert "Moved aside" not in out and "NOT restored" not in out


def test_the_per_project_warm_tier_beside_the_store_is_moved_aside_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The daemon keeps each project's warm sidecar under the STORE's directory (found by swarm-e2e's INC-128 check)."""
    from trw_memory.storage._backup_archive import create_backup_archive

    from trw_mcp.server import _subcommands_backup

    monkeypatch.setattr(_subcommands_backup, "_invoking_trw_dir", lambda: None)
    db_path = _store(tmp_path)
    _serve(monkeypatch, db_path)
    warm = tmp_path / "project_demo-1" / "memory" / "warm.jsonl"
    warm.parent.mkdir(parents=True)
    warm.write_text('{"id": "L-postx"}\n', encoding="utf-8")
    (warm.parent / "warm.jsonl.lock").write_text("", encoding="utf-8")
    _restore(db_path, create_backup_archive(tmp_path, db_path).path)

    assert not warm.exists()
    [aside] = [p for p in tmp_path.iterdir() if p.name.startswith("pre-restore-derived-")]
    assert "L-postx" in (aside / "project_demo-1" / "memory" / "warm.jsonl").read_text(encoding="utf-8")
    assert "Moved aside" in capsys.readouterr().out


def _unproven(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, db_path: Path, **extra: object) -> Path:
    from trw_memory.storage._backup_archive import create_backup_archive

    trw_dir = _project_with_derived_tiers(tmp_path, monkeypatch)
    _restore(db_path, create_backup_archive(db_path.parent, db_path).path, **extra)
    return trw_dir


@pytest.mark.parametrize("where", ["external", "inside_checkout", "beside_served_store"])
def test_restoring_a_db_the_project_does_not_serve_leaves_every_projects_tiers_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: str
) -> None:
    """Feedback #166: only a restored file that IS the served store realigns derived tiers; nearness proves nothing."""
    folder = {
        "external": tmp_path / "other",
        "inside_checkout": tmp_path / "project",  # holds the live .trw, but is not the store it serves
        "beside_served_store": tmp_path,  # the served store's own directory
    }[where]
    folder.mkdir(exist_ok=True)
    (folder / "project_demo-1" / "memory").mkdir(parents=True)
    beside = folder / "project_demo-1" / "memory" / "warm.jsonl"
    beside.write_text('{"id": "L-keep"}\n', encoding="utf-8")
    db_path = _store(folder)
    trw_dir = _unproven(tmp_path, monkeypatch, db_path)

    assert beside.is_file() and (trw_dir / "memory" / "warm.jsonl").is_file()
    assert (trw_dir / "learnings" / "entries" / "2026-01-01-post.yaml").is_file()
    assert (trw_dir / "learnings" / "index.yaml").is_file()
    assert not list(tmp_path.rglob("pre-restore-derived-*"))
    assert "is not the store this project serves" in capsys.readouterr().out


def test_an_explicit_db_equal_to_the_served_store_still_realigns_the_serving_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_memory.storage._backup_archive import create_backup_archive

    trw_dir = _project_with_derived_tiers(tmp_path, monkeypatch)
    db_path = _store(tmp_path)
    _serve(monkeypatch, db_path)
    _restore(db_path, create_backup_archive(tmp_path, db_path).path)

    assert not (trw_dir / "memory" / "warm.jsonl").exists()
    assert "Moved aside (not deleted)" in capsys.readouterr().out


def test_keep_derived_with_an_unserved_db_still_touches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "other").mkdir()
    trw_dir = _unproven(tmp_path, monkeypatch, _store(tmp_path / "other"), keep_derived=True)

    assert (trw_dir / "memory" / "warm.jsonl").is_file()
    assert "NOT restored" not in capsys.readouterr().out


def test_an_unresolvable_served_store_touches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server import _subcommands_backup

    monkeypatch.setattr(_subcommands_backup, "_served_store", lambda: None)
    trw_dir = _unproven(tmp_path, monkeypatch, _store(tmp_path))

    assert (trw_dir / "memory" / "warm.jsonl").is_file()
    assert "unresolved" in capsys.readouterr().out
