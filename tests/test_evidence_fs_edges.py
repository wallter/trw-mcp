"""Bounded failure and race contracts for descriptor-anchored evidence reads."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import EntryState, EvidenceLimits
from trw_mcp.state import _evidence_binding as binding
from trw_mcp.state import _evidence_fs as fs


@pytest.mark.parametrize("path", ["", "/file", "a//b", "./a", "a/../b", "a\\b", "a:b", "a\x00b", "a" * 1025, "\ud800"])
def test_invalid_path_is_nonpositive(tmp_path: Path, path: str) -> None:
    with pytest.raises(binding.StableReadError, match=r"^path_invalid$"):
        binding.read_content_entry(tmp_path, path)


def test_missing_safe_capability_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs, "_safe_read_supported", lambda: False)
    with pytest.raises(binding.StableReadError, match=r"^safe_read_unavailable$"):
        binding.read_content_entry(tmp_path, "file")


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (NotImplementedError(), "safe_read_unavailable"),
        (OSError(errno.ELOOP, "loop"), "path_unsafe"),
        (OSError(errno.ENOTDIR, "not directory"), "path_unsafe"),
        (OSError(errno.EIO, "io"), "read_error"),
        (ValueError("invalid"), "path_invalid"),
    ],
)
def test_open_errors_have_nonpositive_public_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, reason: str
) -> None:
    def failed_open(*_args: object, **_kwargs: object) -> int:
        raise error

    monkeypatch.setattr(fs, "_safe_read_supported", lambda: True)
    monkeypatch.setattr(os, "open", failed_open)
    with pytest.raises(binding.StableReadError, match=f"^{reason}$"):
        binding.read_content_entry(tmp_path, "file")


@pytest.mark.parametrize("target", [".", "nested/.."])
def test_directory_link_text_is_preserved(tmp_path: Path, target: str) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "link").symlink_to(target)
    entry = binding.read_content_entry(tmp_path, "link")
    assert entry.state is EntryState.SYMLINK
    assert entry.link_target == target


def test_relative_parent_escape_rejected(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to("..")
    with pytest.raises(binding.StableReadError, match=r"^symlink_escapes_root$"):
        binding.read_content_entry(tmp_path, "link")


def test_regular_file_cannot_be_a_parent(tmp_path: Path) -> None:
    (tmp_path / "file").write_bytes(b"data")
    with pytest.raises(binding.StableReadError, match=r"^path_not_directory$"):
        binding.read_content_entry(tmp_path, "file/child")


def test_oversized_file_is_rejected_before_hashing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "file").write_bytes(b"too large")
    monkeypatch.setattr(EvidenceLimits, "MAX_BOUND_FILE_BYTES", 3)

    def no_hash(_fd: int, _size: int) -> str:
        pytest.fail("oversized file must be rejected before reading payload")

    monkeypatch.setattr(binding, "_read_file_digest", no_hash)
    with pytest.raises(binding.StableReadError, match=r"^bound_file_too_large$"):
        binding.read_content_entry(tmp_path, "file")


def test_oversized_link_text_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "link").symlink_to("long-target")
    monkeypatch.setattr(EvidenceLimits, "MAX_PATH_BYTES", 5)
    with pytest.raises(binding.StableReadError, match=r"^symlink_target_too_large$"):
        binding.read_content_entry(tmp_path, "link")


def test_symlink_to_fifo_is_nonpositive(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "fifo")
    (tmp_path / "link").symlink_to("fifo")
    with pytest.raises(binding.StableReadError, match=r"^path_type_unsupported$"):
        binding.read_content_entry(tmp_path, "link")


def test_missing_root_is_not_deleted_evidence(tmp_path: Path) -> None:
    with pytest.raises(binding.StableReadError, match=r"^unstable_read$"):
        binding.read_content_entry(tmp_path / "missing", "file")


@pytest.mark.parametrize("change", ["grow", "shrink", "unlink"])
def test_file_change_during_digest_is_nonpositive(tmp_path: Path, change: str) -> None:
    target = tmp_path / "file"
    target.write_bytes(b"initial")

    def change_after_digest(fd: int, size: int) -> str:
        digest = binding._read_file_digest(fd, size)
        if change == "unlink":
            target.unlink()
        else:
            target.write_bytes(b"more bytes than before" if change == "grow" else b"x")
        return digest

    with pytest.raises(binding.StableReadError, match=r"^unstable_read$"):
        fs.read_entry(tmp_path, "file", digest_reader=change_after_digest)


def test_root_replacement_invalidates_open_descriptor_observation(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "file").write_bytes(b"old")

    def replace_root(fd: int, size: int) -> str:
        digest = binding._read_file_digest(fd, size)
        root.rename(tmp_path / "detached")
        root.mkdir()
        (root / "file").write_bytes(b"new")
        return digest

    with pytest.raises(binding.StableReadError, match=r"^unstable_read$"):
        fs.read_entry(root, "file", digest_reader=replace_root)


def test_metadata_alias_resolution_step_bound() -> None:
    # Metadata-only helper has its own work bound even when '.' consumes no I/O.
    with pytest.raises(binding.StableReadError, match=r"^symlink_resolution_limit$"):
        fs._resolve_absolute_metadata("/" + "./" * 2049, 40)


def test_external_alias_with_regular_parent_is_nonpositive(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    external = tmp_path / "external"
    external.write_bytes(b"not a directory")
    (root / "link").symlink_to(str(external / "child"))
    with pytest.raises(binding.StableReadError, match=r"^path_not_directory$"):
        binding.read_content_entry(root, "link")


def test_broken_external_alias_is_not_deleted(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(tmp_path / "missing")
    with pytest.raises(binding.StableReadError, match=r"^symlink_broken_or_cyclic$"):
        binding.read_content_entry(root, "link")


def test_absolute_alias_dotdot_resolution_preserves_raw_link(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "file").write_bytes(b"inside")
    outside = tmp_path / "alias-dir"
    outside.mkdir()
    raw = str(outside) + "/../root/file"
    (root / "link").symlink_to(raw)
    entry = binding.read_content_entry(root, "link")
    assert entry.state is EntryState.SYMLINK
    assert entry.link_target == raw
