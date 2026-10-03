"""``withdraw_cc05_subagent_if_unedited``: only an inspectable, TRW-rendered file is removed."""

from __future__ import annotations

import os
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


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")
def test_unreadable_explorer_is_preserved(tmp_path: Path) -> None:
    """A file we cannot inspect is not proven TRW-owned, so it must not be deleted."""
    target = _install(tmp_path)
    target.chmod(0)
    try:
        assert withdraw_cc05_subagent_if_unedited(tmp_path, None) is False
    finally:
        target.chmod(0o644)
    assert target.exists()


def test_an_edited_explorer_is_kept_and_named_when_a_result_is_passed(tmp_path: Path) -> None:
    target = _install(tmp_path)
    target.write_text("user edit\n", encoding="utf-8")
    result: dict[str, list[str]] = {}
    assert withdraw_cc05_subagent_if_unedited(tmp_path, None, result) is False
    assert target.read_text(encoding="utf-8") == "user edit\n"
    assert any(w.endswith(f"rm {EXPLORER_AGENT_RELPATH}") for w in result["warnings"])


def test_manifest_recorded_hash_counts_as_unedited(tmp_path: Path) -> None:
    import hashlib

    target = _install(tmp_path)
    target.write_text("older TRW rendering\n", encoding="utf-8")
    recorded = {EXPLORER_AGENT_RELPATH: hashlib.sha256(target.read_bytes()).hexdigest()}
    assert withdraw_cc05_subagent_if_unedited(tmp_path, recorded) is True
    assert not target.exists()
