"""``remove_tree_if_hash`` keeps and NAMES anything it cannot prove is TRW's unchanged bytes.

Two keep paths the neighbouring suites (``test_remove_if_hash_tree`` / ``test_tree_harden``) leave open:
a file that changes between the hash and the capture, and an entry that cannot be read.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

_A = b"# trw skill\n"
_B = b"ref.md\n"


def _skill(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "proj"
    skill = root / ".claude" / "skills" / "trw-old"
    (skill / "refs").mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(_A)
    (skill / "refs" / "ref.md").write_bytes(_B)
    return root, skill


def _allowed(_f: Path) -> set[str]:
    return {hashlib.sha256(_A).hexdigest(), hashlib.sha256(_B).hexdigest()}


def _trash(root: Path) -> list[bytes]:
    return sorted(p.read_bytes() for p in (root / ".trw" / "trash").glob("*/data"))


def test_a_file_edited_between_hash_and_capture_is_kept_and_named(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._safe_remove import remove_tree_if_hash

    root, skill = _skill(tmp_path)
    edited = b"saved between the hash and the capture\n"

    def edit_after_hash(f: Path) -> set[str]:
        if f.name == "SKILL.md":
            f.write_bytes(edited)  # the digest was taken from the old bytes; the capture sees these
        return _allowed(f)

    kept = remove_tree_if_hash(skill, root, edit_after_hash)

    assert len(kept) == 1
    assert kept[0].startswith(".claude/skills/trw-old/SKILL.md (")
    assert (skill / "SKILL.md").read_bytes() == edited  # the user's bytes are still at their name
    assert not (skill / "refs" / "ref.md").exists()  # the untouched sibling was still removed (into trash)
    assert _B in _trash(root)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")
def test_an_unreadable_file_is_kept_and_named_and_the_sweep_continues(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._safe_remove import remove_tree_if_hash

    root, skill = _skill(tmp_path)
    locked = skill / "SKILL.md"
    locked.chmod(0)
    try:
        kept = remove_tree_if_hash(skill, root, _allowed)
    finally:
        locked.chmod(0o644)

    assert len(kept) == 1
    assert kept[0].startswith(".claude/skills/trw-old/SKILL.md (unreadable: ")
    assert locked.read_bytes() == _A
    assert not (skill / "refs" / "ref.md").exists()
    assert _trash(root) == [_B]


def test_a_directory_listing_failure_at_rmdir_time_never_deletes_a_kept_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-safe: if the second directory listing fails, only the top is tried; a non-empty one stays and says so."""
    from trw_mcp.bootstrap._safe_remove import remove_tree_if_hash

    root, skill = _skill(tmp_path)
    real_rglob = Path.rglob
    calls = {"n": 0}

    def second_listing_fails(self: Path, pattern: str, **kw: object):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 2:
            raise PermissionError(13, "denied")
        return real_rglob(self, pattern, **kw)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rglob", second_listing_fails)
        kept = remove_tree_if_hash(skill, root, _allowed)

    assert kept == [".claude/skills/trw-old (not empty after removal)"]
    assert skill.is_dir() and (skill / "refs").is_dir()
    assert _trash(root) == sorted([_A, _B])  # the proven files were captured, nothing was lost
