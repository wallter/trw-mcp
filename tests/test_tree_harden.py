"""Hardening of ``remove_tree_if_hash``: the pre-hash never blocks or reads unbounded, and an inspection
error keeps the artifact and reports it instead of aborting the update."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

_A = b"# trw skill\n"


def _skill(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "proj"
    skill = root / ".claude" / "skills" / "trw-old"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(_A)
    return root, skill


def _allow_a(_f: Path) -> set[str]:
    return {hashlib.sha256(_A).hexdigest()}


def test_a_fifo_in_the_tree_is_kept_and_never_blocks(tmp_path: Path) -> None:
    """Red on the old pre-hash: read_bytes() on a FIFO with no writer blocks forever."""
    from trw_mcp.bootstrap._safe_remove import remove_tree_if_hash

    root, skill = _skill(tmp_path)
    os.mkfifo(skill / "pipe")
    kept = remove_tree_if_hash(skill, root, _allow_a)
    assert kept == [".claude/skills/trw-old/pipe (not a regular file)"]
    assert (skill / "pipe").exists()


def test_a_file_over_the_size_cap_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import _trash_tree
    from trw_mcp.bootstrap._safe_remove import remove_tree_if_hash

    root, skill = _skill(tmp_path)
    monkeypatch.setattr(_trash_tree, "_HASH_CAP", len(_A) - 1)
    kept = remove_tree_if_hash(skill, root, _allow_a)
    assert kept[0].startswith(".claude/skills/trw-old/SKILL.md (larger than the ")
    assert kept[0].endswith(" MiB cap)")
    assert (skill / "SKILL.md").read_bytes() == _A


def test_an_inspection_error_keeps_the_artifact_and_reports_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Red on the old helper: an OSError while listing escaped and aborted the whole update."""
    from trw_mcp.bootstrap._safe_remove import remove_tree_if_hash

    root, skill = _skill(tmp_path)

    def denied(self: Path, pattern: str, **_kw: object):  # type: ignore[no-untyped-def]
        raise PermissionError(13, "denied")

    with monkeypatch.context() as patch:  # scoped: pytest's own tmp_path cleanup uses rglob too
        patch.setattr(Path, "rglob", denied)
        kept = remove_tree_if_hash(skill, root, _allow_a)
    assert kept == [".claude/skills/trw-old (could not inspect: [Errno 13] denied)"]
    assert (skill / "SKILL.md").read_bytes() == _A
