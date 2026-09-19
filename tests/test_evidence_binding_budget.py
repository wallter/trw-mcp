"""CORE-205 NFR03: cumulative byte limits reject work before payload hashing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import EntryState, EvidenceLimits, ReceiptState, RunOwnedScope
from trw_mcp.state import _evidence_binding as binding

from ._evidence_factories import write_journal


def _scope(root: Path, files: dict[str, bytes | None]) -> RunOwnedScope:
    for name, content in files.items():
        if content is not None:
            (root / name).write_bytes(content)
    run = root / "run"
    write_journal(run, [str(root / name) for name in files])
    return binding.mint_run_owned_scope(run, root, scope_id="budget-scope")


@pytest.fixture
def measured_hash_reads(monkeypatch: pytest.MonkeyPatch) -> tuple[list[int], list[int]]:
    original_digest = binding._read_file_digest
    original_read = os.read
    active_fds: set[int] = set()
    requested_sizes: list[int] = []
    actual_read_sizes: list[int] = []

    def measured_read(fd: int, count: int) -> bytes:
        result = original_read(fd, count)
        if fd in active_fds:
            actual_read_sizes.append(len(result))
        return result

    def measured_digest(fd: int, size: int) -> str:
        requested_sizes.append(size)
        active_fds.add(fd)
        try:
            return original_digest(fd, size)
        finally:
            active_fds.remove(fd)

    monkeypatch.setattr(os, "read", measured_read)
    monkeypatch.setattr(binding, "_read_file_digest", measured_digest)
    return requested_sizes, actual_read_sizes


def test_exact_total_boundary_build_and_freshness_are_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, measured_hash_reads: tuple[list[int], list[int]]
) -> None:
    scope = _scope(tmp_path, {"a": b"1234", "b": b"5678"})
    monkeypatch.setattr(EvidenceLimits, "MAX_TOTAL_BOUND_BYTES", 8)
    requested, actual = measured_hash_reads
    result = binding.build_content_binding(scope, tmp_path)
    assert result.state is ReceiptState.VALID
    assert result.binding is not None
    assert requested == [4, 4]
    assert sum(actual) == 8
    requested.clear()
    actual.clear()
    assert binding.content_binding_is_current(result.binding, tmp_path).state is ReceiptState.VALID
    assert requested == [4, 4]
    assert sum(actual) == 8


def test_total_overflow_rejected_before_hashing_overbudget_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, measured_hash_reads: tuple[list[int], list[int]]
) -> None:
    scope = _scope(tmp_path, {"a": b"123456", "b": b"abcdef"})
    monkeypatch.setattr(EvidenceLimits, "MAX_TOTAL_BOUND_BYTES", 8)
    requested, actual = measured_hash_reads
    result = binding.build_content_binding(scope, tmp_path)
    assert requested == [6], "second file cannot be hashed before enforcing the cumulative limit"
    assert sum(actual) == 6
    assert result.state is ReceiptState.INVALID
    assert result.reason_code == "bound_total_bytes_exceeded"
    assert result.binding is None


def test_freshness_growth_rejected_before_hashing_overbudget_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, measured_hash_reads: tuple[list[int], list[int]]
) -> None:
    scope = _scope(tmp_path, {"a": b"1234", "b": b"5678"})
    monkeypatch.setattr(EvidenceLimits, "MAX_TOTAL_BOUND_BYTES", 8)
    original = binding.build_content_binding(scope, tmp_path)
    assert original.state is ReceiptState.VALID
    assert original.binding is not None
    (tmp_path / "b").write_bytes(b"grown beyond remaining budget")
    requested, actual = measured_hash_reads
    requested.clear()
    actual.clear()
    result = binding.content_binding_is_current(original.binding, tmp_path)
    assert requested == [4]
    assert sum(actual) == 4
    assert result.state is ReceiptState.INVALID
    assert result.reason_code == "bound_total_bytes_exceeded"
    assert result.binding is None


def test_per_file_limit_still_precedes_payload_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, measured_hash_reads: tuple[list[int], list[int]]
) -> None:
    scope = _scope(tmp_path, {"a": b"123456"})
    monkeypatch.setattr(EvidenceLimits, "MAX_TOTAL_BOUND_BYTES", 8)
    monkeypatch.setattr(EvidenceLimits, "MAX_BOUND_FILE_BYTES", 5)
    result = binding.build_content_binding(scope, tmp_path)
    assert measured_hash_reads == ([], [])
    assert result.state is ReceiptState.INVALID
    assert result.reason_code == "bound_file_too_large"


def test_link_and_deleted_entries_do_not_consume_payload_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, measured_hash_reads: tuple[list[int], list[int]]
) -> None:
    scope = _scope(tmp_path, {"a": b"12345678", "b-link": None, "c-deleted": None, "d-empty": b""})
    (tmp_path / "b-link").symlink_to("a")
    monkeypatch.setattr(EvidenceLimits, "MAX_TOTAL_BOUND_BYTES", 8)
    requested, actual = measured_hash_reads
    result = binding.build_content_binding(scope, tmp_path)
    assert result.state is ReceiptState.VALID
    assert result.binding is not None
    assert requested == [8, 0]
    assert sum(actual) == 8

    assert [entry.state for entry in result.binding.entries] == [
        EntryState.FILE,
        EntryState.SYMLINK,
        EntryState.DELETED,
        EntryState.FILE,
    ]
    requested.clear()
    actual.clear()
    assert binding.content_binding_is_current(result.binding, tmp_path).state is ReceiptState.VALID
    assert requested == [8, 0]
    assert sum(actual) == 8


@pytest.mark.parametrize("consumer", ["build", "freshness"])
def test_retry_work_is_cumulatively_bounded_when_changed_files_shrink_to_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    measured_hash_reads: tuple[list[int], list[int]],
    consumer: str,
) -> None:
    scope = _scope(tmp_path, dict.fromkeys(("a", "b", "c", "d"), b""))
    monkeypatch.setattr(EvidenceLimits, "MAX_TOTAL_BOUND_BYTES", 8)
    initial = binding.build_content_binding(scope, tmp_path)
    assert initial.state is ReceiptState.VALID
    assert initial.binding is not None
    paths_by_inode: dict[tuple[int, int], Path] = {}
    for name in ("a", "b", "c", "d"):
        path = tmp_path / name
        path.write_bytes(b"12345678")
        info = path.stat()
        paths_by_inode[(info.st_dev, info.st_ino)] = path
    measured_digest = binding._read_file_digest

    def hash_then_shrink(fd: int, size: int) -> str:
        digest = measured_digest(fd, size)
        if size:
            info = os.fstat(fd)
            paths_by_inode[(info.st_dev, info.st_ino)].write_bytes(b"")
        return digest

    monkeypatch.setattr(binding, "_read_file_digest", hash_then_shrink)
    requested, actual = measured_hash_reads
    requested.clear()
    actual.clear()
    if consumer == "build":
        result = binding.build_content_binding(scope, tmp_path)
    else:
        result = binding.content_binding_is_current(initial.binding, tmp_path)
    assert requested == [8, 0, 8, 0, 8, 0], "retry work cannot reset when an empty retry succeeds"
    assert sum(actual) == 24, "three times the accepted budget bounds cumulative attempted payload work"
    assert (tmp_path / "d").read_bytes() == b"12345678", "fourth oversized attempt must never reach hashing"
    assert result.state is ReceiptState.INVALID
    assert result.reason_code == "bound_read_budget_exceeded"
    assert result.binding is None
