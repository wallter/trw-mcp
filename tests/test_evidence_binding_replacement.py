"""CORE-205 FR01: opened bytes must still belong to the named file after hashing."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import EntryState
from trw_mcp.state import _evidence_binding as binding
from trw_mcp.state import _evidence_fs


@pytest.mark.parametrize("replace_every_read", [False, True], ids=["single-replacement", "persistent-replacement"])
def test_atomic_replacement_cannot_return_detached_inode_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_every_read: bool
) -> None:
    target = tmp_path / "proof.txt"
    target.write_bytes(b"old evidence")
    replacement_bytes = b"new evidence"
    original_digest = binding._read_file_digest
    reads = 0

    def replace_after_hash(fd: int, size: int) -> str:
        nonlocal reads
        digest = original_digest(fd, size)
        reads += 1
        if reads == 1 or replace_every_read:
            replacement = tmp_path / "replacement.txt"
            replacement.write_bytes(replacement_bytes)
            # os.replace changes the named inode without changing the open fd.
            os.replace(replacement, target)
        return digest

    monkeypatch.setattr(binding, "_read_file_digest", replace_after_hash)
    if replace_every_read:
        with pytest.raises(binding.StableReadError, match=r"^unstable_read$") as error:
            binding.read_content_entry(tmp_path, "proof.txt")
        assert error.value.reason_code == "unstable_read"
        assert reads == 3, "FR01 allows the initial attempt and exactly two retries"
    else:
        entry = binding.read_content_entry(tmp_path, "proof.txt")
        assert entry.state is EntryState.FILE
        assert entry.byte_digest == hashlib.sha256(replacement_bytes).hexdigest()
        assert entry.byte_digest != hashlib.sha256(b"old evidence").hexdigest()
        assert reads == 2, "one replacement requires a new stable read"


def test_lstat_to_open_directory_replacement_never_returns_file_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "proof.txt"
    target.write_bytes(b"initial regular file")
    original_open = os.open
    replaced = False

    def replace_before_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal replaced
        if Path(path).name == target.name and not replaced:
            target.unlink()
            target.mkdir()
            replaced = True
        return original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    # The injected wrapper still uses the host's descriptor-relative open.
    monkeypatch.setattr(_evidence_fs, "_safe_read_supported", lambda: True)
    monkeypatch.setattr(binding.os, "open", replace_before_open)
    with pytest.raises(binding.StableReadError, match=r"^path_type_(raced|unsupported)$"):
        binding.read_content_entry(tmp_path, "proof.txt")
    assert replaced


def test_lstat_to_open_regular_replacement_requires_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "proof.txt"
    target.write_bytes(b"old evidence")
    replacement_bytes = b"new evidence"
    original_open = os.open
    opens = 0

    def replace_before_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal opens
        if Path(path).name == target.name:
            opens += 1
            if opens == 1:
                replacement = tmp_path / "replacement.txt"
                replacement.write_bytes(replacement_bytes)
                os.replace(replacement, target)
        return original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    # The injected wrapper still uses the host's descriptor-relative open.
    monkeypatch.setattr(_evidence_fs, "_safe_read_supported", lambda: True)
    monkeypatch.setattr(binding.os, "open", replace_before_open)
    entry = binding.read_content_entry(tmp_path, "proof.txt")
    assert entry.state is EntryState.FILE
    assert entry.byte_digest == hashlib.sha256(replacement_bytes).hexdigest()
    assert opens == 2, "initial lstat and opened inode must agree before accepting a read"
