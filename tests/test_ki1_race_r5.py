"""FB-01-KI1-RACE r5: codex r1 KIs on r4 (81bc87b9f3), each red-first.

KI1a  the ledger re-read the path, so bytes a concurrent writer put there before the record were trusted;
KI1b  proof-then-unlink left a window between the proof read and the unlink;
KI2   one refused capture aborted the rollback and stranded files captured before it;
KI3   a FIFO in the surface stalled the rollback (the proof read it);
KI4   framework promotions (_replace_durable) bypassed the ledger.
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_ki1a_the_ledger_records_the_bytes_written_not_a_reread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp import _checkout_write
    from trw_mcp.state import persistence

    target = tmp_path / "f.yaml"
    real_record = persistence.record_run_write

    def foreign_lands_first(path: Path, *args: object, **kwargs: object) -> None:
        path.write_bytes(b"foreign\n")  # a concurrent writer replaces the file before the record
        real_record(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(persistence, "record_run_write", foreign_lands_first)
    with _checkout_write.recording_writes():
        persistence.FileStateWriter().write_text(target, "ours\n")
        recorded = _checkout_write.written_this_run(target)

    assert recorded == _sha(b"ours\n"), "the ledger trusted bytes this run did not write"


def test_ki1b_a_writer_landing_after_the_proof_is_never_unlinked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _restore_proof
    from trw_mcp.bootstrap._update_transaction import _restore_transaction_file

    root, snap = tmp_path / "root", tmp_path / "snap"
    rel = ".claude/hooks/x.sh"
    for base, body in ((root, b"same\n"), (snap, b"same\n")):
        (base / rel).parent.mkdir(parents=True)
        (base / rel).write_bytes(body)
    real_read = Path.read_bytes
    state = {"n": 0}

    def writer_after_proof(self: Path) -> bytes:
        data = real_read(self)
        if self == root / rel and state["n"] == 0:
            state["n"] += 1
            self.write_bytes(b"writer\n")  # lands right after the proof read
        return data

    monkeypatch.setattr(Path, "read_bytes", writer_after_proof)
    notes: list[str] = []
    _restore_transaction_file(root, snap, rel, notes)
    monkeypatch.undo()
    survivors = [p for p in root.rglob("*") if p.is_file() and p.read_bytes() == b"writer\n"]
    assert state["n"] == 1 and survivors, "the writer's bytes were unlinked after the proof"
    del _restore_proof


def test_ki2_one_refused_capture_never_strands_the_rest_of_the_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _trash, _update_project
    from trw_mcp.bootstrap._trash import Removal

    root, snap = tmp_path / "root", tmp_path / "snap"
    a, b = ".claude/hooks/a.sh", ".claude/hooks/b.sh"
    for base in (root, snap):
        (base / ".claude" / "hooks").mkdir(parents=True)
    (snap / a).write_bytes(b"a-before\n")
    (snap / b).write_bytes(b"b-before\n")
    (root / a).write_bytes(b"a-foreign\n")
    (root / b).write_bytes(b"b-foreign\n")
    real = _trash.remove_if_hash

    def refuse_b(path: Path, r: Path, sha: str, *, key: str | None = None) -> Removal:
        if path.name == "b.sh":
            return Removal(key, path, "kept", path, None, "refused for the test")
        return real(path, r, sha, key=key)

    monkeypatch.setattr(_trash, "remove_if_hash", refuse_b)
    result: dict[str, list[str]] = {"warnings": [], "errors": []}

    _update_project._rollback(root, snap, result)

    assert (root / a).read_bytes() == b"a-before\n", "A was stranded: the rollback stopped at B"
    assert [p for p in (root / ".trw" / "trash").rglob("*") if p.is_file() and p.read_bytes() == b"a-foreign\n"]
    assert (root / b).read_bytes() == b"b-foreign\n", "B's bytes must stay where they are"
    assert any(b in w for w in result["warnings"])


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_ki3_a_fifo_never_stalls_the_rollback(tmp_path: Path) -> None:
    from trw_mcp.bootstrap import _update_project

    root, snap = tmp_path / "root", tmp_path / "snap"
    for base in (root, snap):
        (base / ".claude" / "hooks").mkdir(parents=True)
    fifo = root / ".claude" / "hooks" / "pipe"
    os.mkfifo(fifo)
    result: dict[str, list[str]] = {"warnings": [], "errors": []}
    worker = threading.Thread(target=_update_project._rollback, args=(root, snap, result), daemon=True)
    worker.start()
    worker.join(10)
    if worker.is_alive():
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(5)
        pytest.fail("the rollback blocked reading a FIFO")
    assert fifo.exists(), "a special file the rollback cannot prove is left in place"


def test_ki4_a_framework_promotion_is_recorded_in_the_ledger(tmp_path: Path) -> None:
    from trw_mcp import _checkout_write, framework_deployment

    src, dst = tmp_path / "staged", tmp_path / "promoted"
    src.write_bytes(b"framework body\n")
    with _checkout_write.recording_writes():
        framework_deployment._replace_durable(src, dst)
        assert _checkout_write.written_this_run(dst) == _sha(b"framework body\n")


def test_a_writer_landing_between_the_clear_and_the_copy_back_keeps_its_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lead review N1 (r5 form): the copy-back creates only absent names, so it never replaces a late writer."""
    from trw_mcp import _checkout_write
    from trw_mcp.bootstrap import _restore_proof, _update_project

    root, snap = tmp_path / "root", tmp_path / "snap"
    rel = ".claude/hooks/x.sh"
    for base in (root, snap):
        (base / ".claude" / "hooks").mkdir(parents=True)
    (snap / rel).write_bytes(b"before\n")
    real_restore = _restore_proof.restore_snapshot_exclusive

    def writer_lands_first(*args: object, **kwargs: object) -> None:
        (root / rel).write_bytes(b"late writer\n")  # the name was just cleared; a writer takes it
        real_restore(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_restore_proof, "restore_snapshot_exclusive", writer_lands_first)
    result: dict[str, list[str]] = {"warnings": [], "errors": []}
    with _checkout_write.recording_writes():
        _checkout_write.write_checkout_file(root, root / rel, b"this run\n")
        _update_project._rollback(root, snap, result)

    assert (root / rel).read_bytes() == b"late writer\n", "the copy-back replaced a late writer"
    assert any(rel in w for w in result["warnings"])


def _preserved(root: Path, data: bytes, warnings: list[str]) -> bool:
    named = [w.split(" is at ", 1)[1] for w in warnings if "pre-update version is at" in w]
    return any(Path(n).read_bytes() == data for n in named)


def test_a_refused_rollback_copy_back_preserves_the_pre_update_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r2 lead #1: a late writer keeps the name, and the snapshot (discarded next) is the only copy of U."""
    from trw_mcp import _checkout_write
    from trw_mcp.bootstrap import _restore_proof, _update_project

    root, snap = tmp_path / "root", tmp_path / "snap"
    rel = ".claude/hooks/x.sh"
    for base in (root, snap):
        (base / ".claude" / "hooks").mkdir(parents=True)
    (snap / rel).write_bytes(b"user pre-update\n")
    real_restore = _restore_proof.restore_snapshot_exclusive

    def writer_lands_first(*args: object, **kwargs: object) -> None:
        (root / rel).write_bytes(b"late writer\n")
        real_restore(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_restore_proof, "restore_snapshot_exclusive", writer_lands_first)
    result: dict[str, list[str]] = {"warnings": [], "errors": []}
    with _checkout_write.recording_writes():
        _checkout_write.write_checkout_file(root, root / rel, b"this run\n")
        _update_project._rollback(root, snap, result)

    assert (root / rel).read_bytes() == b"late writer\n"
    assert _preserved(root, b"user pre-update\n", result["warnings"]), result["warnings"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_a_kept_name_in_the_dirty_restore_preserves_the_pre_update_bytes(tmp_path: Path) -> None:
    """A FIFO (KEPT) at the name: the dirty restore cannot put U back, so U goes to .trw/trash, named."""
    from trw_mcp.bootstrap._update_transaction import _restore_transaction_file

    root, snap = tmp_path / "root", tmp_path / "snap"
    rel = ".claude/hooks/x.sh"
    for base in (root, snap):
        (base / ".claude" / "hooks").mkdir(parents=True)
    (snap / rel).write_bytes(b"user pre-update\n")
    os.mkfifo(root / rel)  # not a regular file: KEPT
    notes: list[str] = []
    _restore_transaction_file(root, snap, rel, notes)
    assert (root / rel).is_fifo()
    assert _preserved(root, b"user pre-update\n", notes), notes


@pytest.mark.parametrize("rename_full", [False, True], ids=["rename_ok", "apfs_rename_needs_space"])
def test_a_full_disk_still_clears_this_runs_own_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rename_full: bool
) -> None:
    """Lead r5c #1: the trash capture needs new space; on ENOSPC TRW's own write is cleared in place instead."""
    import errno

    from trw_mcp import _checkout_write
    from trw_mcp.bootstrap import _trash
    from trw_mcp.bootstrap._update_transaction import _restore_transaction_file

    root, snap = tmp_path / "root", tmp_path / "snap"
    rel = ".claude/hooks/x.sh"
    for base in (root, snap):
        (base / ".claude" / "hooks").mkdir(parents=True)
    (snap / rel).write_bytes(b"user pre-update\n")

    def disk_full(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(_trash, "_write_meta", disk_full)
    real_rename = os.rename
    if rename_full:  # a copy-on-write volume refuses even the same-directory rename aside

        def rename(src: object, dst: object, *a: object, **k: object) -> None:
            if ".trw-" in str(dst):
                raise OSError(errno.ENOSPC, "No space left on device")
            real_rename(src, dst, *a, **k)  # type: ignore[arg-type]

        monkeypatch.setattr(os, "rename", rename)
    notes: list[str] = []
    with _checkout_write.recording_writes():
        _checkout_write.write_checkout_file(root, root / rel, b"this run\n")
        _restore_transaction_file(root, snap, rel, notes)

    assert (root / rel).read_bytes() == b"user pre-update\n", notes
    assert not any("appeared" in n for n in notes), notes
    leftovers = [p for p in root.rglob("*") if p.is_file() and p != root / rel]
    assert leftovers == [], f"a refused capture left files behind: {leftovers}"


def test_a_copy_back_that_fails_midway_leaves_no_half_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lead r5c #2: a write error after the exclusive create unlinks the file this call made, nothing else."""
    import errno
    import shutil as real_shutil

    from trw_mcp.bootstrap import _restore_proof

    src, dest = tmp_path / "src", tmp_path / "dest"
    src.write_bytes(b"x" * 600)
    real = real_shutil.copyfileobj

    def half_then_full(fsrc, fdst, *a, **k):  # type: ignore[no-untyped-def]
        fdst.write(fsrc.read(300))
        fdst.flush()
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(_restore_proof.shutil, "copyfileobj", half_then_full)
    with pytest.raises(OSError):
        _restore_proof.copy_back_exclusive(src, dest, "dest", [])
    monkeypatch.setattr(_restore_proof.shutil, "copyfileobj", real)
    assert not dest.exists(), "a half-written file was left at the name"
