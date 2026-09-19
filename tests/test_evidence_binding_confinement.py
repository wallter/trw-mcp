"""CORE-205 FR01/NFR02: filesystem races cannot escape the bound repository."""

from __future__ import annotations

import errno
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import EntryState, ReceiptState
from trw_mcp.state import _evidence_binding as binding


def _preserve_capability_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    # Replacing os.open/stat for deterministic races must not emulate a platform
    # without the actual descriptor-relative APIs available on this test host.
    from trw_mcp.state import _evidence_fs

    monkeypatch.setattr(_evidence_fs, "_safe_read_supported", lambda: True)


@pytest.mark.parametrize("absolute", [False, True])
def test_internal_symlink_preserves_exact_raw_target(tmp_path: Path, absolute: bool) -> None:
    (tmp_path / "real.txt").write_bytes(b"inside")
    raw = str(tmp_path / "real.txt") if absolute else "./real.txt"
    (tmp_path / "link.txt").symlink_to(raw)
    entry = binding.read_content_entry(tmp_path, "link.txt")
    assert entry.state is EntryState.SYMLINK
    assert entry.link_target == raw


def test_internal_parent_symlink_is_usable(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "proof.txt").write_bytes(b"inside")
    (tmp_path / "alias").symlink_to("real", target_is_directory=True)
    entry = binding.read_content_entry(tmp_path, "alias/proof.txt")
    assert entry.state is EntryState.FILE
    assert entry.byte_digest == hashlib.sha256(b"inside").hexdigest()


def test_absolute_internal_link_via_lexical_project_root_alias_preserves_target(tmp_path: Path) -> None:
    root = tmp_path / "real-root"
    root.mkdir()
    alias = tmp_path / "root-alias"
    alias.symlink_to(root, target_is_directory=True)
    (root / "proof.txt").write_bytes(b"inside")
    raw_target = str(alias / "proof.txt")
    (root / "link.txt").symlink_to(raw_target)
    entry = binding.read_content_entry(alias, "link.txt")
    assert entry.state is EntryState.SYMLINK
    assert entry.link_target == raw_target
    run = root / "run"
    (run / "meta").mkdir(parents=True)
    (run / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    scope = binding.mint_run_owned_scope(run, alias, scope_id="alias-scope", operator_paths=("link.txt",))
    outcome = binding.build_content_binding(scope, alias)
    assert outcome.state is ReceiptState.VALID
    assert outcome.binding is not None
    assert outcome.binding.entries[0].link_target == raw_target
    assert binding.content_binding_is_current(outcome.binding, alias).state is ReceiptState.VALID


def test_missing_suffix_under_existing_internal_parent_alias_is_deleted(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to("real", target_is_directory=True)
    assert binding.read_content_entry(tmp_path, "alias/missing/proof.txt").state is EntryState.DELETED


@pytest.mark.parametrize("alias_kind", ["root", "file"])
def test_canonical_root_accepts_unrelated_absolute_alias_into_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias_kind: str
) -> None:
    root = tmp_path / "real"
    root.mkdir()
    (root / "proof.txt").write_bytes(b"inside")
    alias = tmp_path / "unrelated-alias"
    alias.symlink_to(root if alias_kind == "root" else root / "proof.txt")
    raw_target = str(alias / "proof.txt") if alias_kind == "root" else str(alias)
    (root / "link.txt").symlink_to(raw_target)

    def no_payload_read(_fd: int, _size: int) -> str:
        pytest.fail("binding symlink text must not hash a target payload")

    monkeypatch.setattr(binding, "_read_file_digest", no_payload_read)
    entry = binding.read_content_entry(root.resolve(), "link.txt")
    assert entry.state is EntryState.SYMLINK
    assert entry.link_target == raw_target


def test_long_acyclic_external_alias_chain_fails_with_bounded_metadata_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "real"
    root.mkdir()
    (root / "proof.txt").write_bytes(b"inside")
    target = root / "proof.txt"
    # This chain really terminates inside root; rejection is a resource bound,
    # not a broken-target, cycle, or outside-payload shortcut.
    for index in reversed(range(64)):
        alias = tmp_path / f"alias-{index}"
        alias.symlink_to(target)
        target = alias
    (root / "link.txt").symlink_to(target)
    original_readlink = os.readlink
    reads = 0

    def count_readlink(path: str, *args: object, **kwargs: object) -> str:
        nonlocal reads
        reads += 1
        assert reads <= 48, "alias expansion must stop before following the whole chain"
        return original_readlink(path, *args, **kwargs)  # type: ignore[arg-type]

    def no_payload_read(_fd: int, _size: int) -> str:
        pytest.fail("external alias resolution must be metadata-only")

    _preserve_capability_probe(monkeypatch)
    monkeypatch.setattr(os, "readlink", count_readlink)
    monkeypatch.setattr(binding, "_read_file_digest", no_payload_read)
    with pytest.raises(binding.StableReadError):
        binding.read_content_entry(root.resolve(), "link.txt")
    assert 0 < reads <= 48


@pytest.mark.parametrize("kind", ["broken", "cyclic", "outside"])
def test_unsafe_symlinks_are_not_evidence(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"outside")
    target = {"broken": "missing.txt", "cyclic": "link.txt", "outside": str(outside)}[kind]
    (root / "link.txt").symlink_to(target)
    with pytest.raises(binding.StableReadError):
        binding.read_content_entry(root, "link.txt")


def test_parent_swap_never_hashes_outside_inode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    parent = root / "parent"
    parent.mkdir(parents=True)
    (parent / "proof.txt").write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "proof.txt"
    secret.write_bytes(b"outside secret")
    secret_stat = secret.stat()
    original_open = os.open
    original_digest = binding._read_file_digest
    swapped = False
    outside_reads: list[int] = []

    def swap_before_leaf_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if Path(path).name == "proof.txt" and not swapped:
            parent.rename(root / "detached-parent")
            parent.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    def observe_digest(fd: int, size: int) -> str:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) == (secret_stat.st_dev, secret_stat.st_ino):
            outside_reads.append(fd)
        return original_digest(fd, size)

    _preserve_capability_probe(monkeypatch)
    monkeypatch.setattr(os, "open", swap_before_leaf_open)
    monkeypatch.setattr(binding, "_read_file_digest", observe_digest)
    rejected = False
    try:
        binding.read_content_entry(root, "parent/proof.txt")
    except binding.StableReadError:
        rejected = True
    assert swapped, "the intended race must actually execute"
    assert not outside_reads, "post-read rejection cannot undo reading outside the repository"
    assert rejected, "a substituted parent must not produce current evidence"


def test_permission_failure_is_not_deletion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "proof.txt").write_bytes(b"inside")
    original_lstat, original_stat = os.lstat, os.stat

    def denied_lstat(path: str, *args: object, **kwargs: object) -> os.stat_result:
        if Path(path).name == "proof.txt":
            raise PermissionError(errno.EACCES, "injected permission error")
        return original_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

    def denied_stat(path: str, *args: object, **kwargs: object) -> os.stat_result:
        if not isinstance(path, int) and Path(path).name == "proof.txt":
            raise PermissionError(errno.EACCES, "injected permission error")
        return original_stat(path, *args, **kwargs)  # type: ignore[arg-type]

    _preserve_capability_probe(monkeypatch)
    monkeypatch.setattr(os, "lstat", denied_lstat)
    monkeypatch.setattr(os, "stat", denied_stat)
    with pytest.raises(binding.StableReadError):
        binding.read_content_entry(tmp_path, "proof.txt")


def test_genuine_missing_parent_is_deleted(tmp_path: Path) -> None:
    assert binding.read_content_entry(tmp_path, "missing/proof.txt").state is EntryState.DELETED


def test_fifo_read_is_bounded_and_nonpositive(tmp_path: Path) -> None:
    fifo = tmp_path / "proof.fifo"
    os.mkfifo(fifo)
    script = """
import sys
from pathlib import Path
from trw_mcp.state._evidence_binding import StableReadError, read_content_entry
try:
    read_content_entry(Path(sys.argv[1]), 'proof.fifo')
except StableReadError:
    print('rejected')
else:
    raise AssertionError('FIFO accepted')
"""
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ}
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "rejected"


def test_continuous_link_retargeting_is_nonpositive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("first.txt", "other.txt"):
        (tmp_path / name).write_bytes(b"inside")
    link = tmp_path / "link.txt"
    link.symlink_to("first.txt")
    original_readlink = os.readlink
    retargets = 0

    def retarget_after_read(path: str, *args: object, **kwargs: object) -> str:
        nonlocal retargets
        target = original_readlink(path, *args, **kwargs)  # type: ignore[arg-type]
        if Path(path).name == "link.txt":
            link.unlink()
            link.symlink_to("other.txt" if target == "first.txt" else "first.txt")
            retargets += 1
        return target

    _preserve_capability_probe(monkeypatch)
    monkeypatch.setattr(os, "readlink", retarget_after_read)
    with pytest.raises(binding.StableReadError):
        binding.read_content_entry(tmp_path, "link.txt")
    assert retargets > 0
