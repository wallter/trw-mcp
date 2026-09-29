"""``withdraw_cc05_subagent_if_unedited``: only an inspectable, TRW-rendered file is removed."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.channels.claude_code._explorer_subagent import (
    EXPLORER_AGENT_RELPATH,
    install_cc05_subagent,
    withdraw_cc05_subagent_if_unedited,
)


def _install(tmp_path: Path) -> Path:
    install_cc05_subagent(tmp_path)
    target = tmp_path / EXPLORER_AGENT_RELPATH
    assert target.is_file()
    return target


def test_unedited_explorer_is_withdrawn(tmp_path: Path) -> None:
    target = _install(tmp_path)
    assert withdraw_cc05_subagent_if_unedited(tmp_path, None) is True
    assert not target.exists()


def test_user_edited_explorer_is_preserved(tmp_path: Path) -> None:
    target = _install(tmp_path)
    target.write_text(target.read_text(encoding="utf-8") + "\nmy own note\n", encoding="utf-8")
    assert withdraw_cc05_subagent_if_unedited(tmp_path, None) is False
    assert target.exists()


def test_unreadable_explorer_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file we cannot inspect is not proven TRW-owned, so it must not be deleted."""
    target = _install(tmp_path)
    real_read_bytes = Path.read_bytes

    def _deny(self: Path) -> bytes:
        if self == target:
            raise PermissionError("unreadable")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _deny)
    assert withdraw_cc05_subagent_if_unedited(tmp_path, None) is False
    assert target.exists()


def test_a_read_that_fails_after_the_first_is_still_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ownership is decided from ONE read; a later read failing cannot turn 'unknown' into 'unedited'."""
    target = _install(tmp_path)
    real_read_bytes = Path.read_bytes
    reads: list[int] = []

    def _second_read_fails(self: Path) -> bytes:
        if self == target:
            reads.append(1)
            if len(reads) > 1:
                raise PermissionError("unreadable on the second read")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _second_read_fails)
    target.write_text("user edit\n", encoding="utf-8")
    assert withdraw_cc05_subagent_if_unedited(tmp_path, None) is False
    assert target.exists()


def test_manifest_recorded_hash_counts_as_unedited(tmp_path: Path) -> None:
    import hashlib

    target = _install(tmp_path)
    target.write_text("older TRW rendering\n", encoding="utf-8")
    recorded = {EXPLORER_AGENT_RELPATH: hashlib.sha256(target.read_bytes()).hexdigest()}
    assert withdraw_cc05_subagent_if_unedited(tmp_path, recorded) is True
    assert not target.exists()
