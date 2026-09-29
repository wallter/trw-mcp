"""The stale skill/agent/command sweeps remove through ``remove_tree_if_hash`` (HB-2).

The ownership proof (``preserve_unowned`` / the shipped-bytes compare) used to be taken early and followed by
``rmtree``/``unlink`` later, so an edit saved in between, a file added to the directory, or a write through a
held fd was destroyed. Now every file is re-hashed and captured into ``.trw/trash`` at the act, and
directories are only ``rmdir``ed, so anything unproven keeps its bytes and its directory.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests._fs_hazards import open_fd_writer

_A = b"# trw skill\n"
_B = b"ref.md\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _skill(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    root = tmp_path / "proj"
    skill = root / ".claude" / "skills" / "trw-old"
    (skill / "refs").mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(_A)
    (skill / "refs" / "ref.md").write_bytes(_B)
    manifest = {"trw-old/SKILL.md": _sha(_A), "trw-old/refs/ref.md": _sha(_B)}
    return root, skill, manifest


def _trash(root: Path) -> list[bytes]:
    return sorted(p.read_bytes() for p in (root / ".trw" / "trash").glob("*/data"))


def _allowed(manifest: dict[str, str], root: Path):  # type: ignore[no-untyped-def]
    from trw_mcp.bootstrap._version_migration_predecessors import recorded_digests

    return lambda f: recorded_digests(f, manifest, root)


def test_unedited_tree_moves_to_trash_and_its_dirs_go(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._safe_remove import remove_tree_if_hash

    root, skill, manifest = _skill(tmp_path)
    assert remove_tree_if_hash(skill, root, _allowed(manifest, root)) == []
    assert not skill.exists()
    assert _trash(root) == sorted([_A, _B])


def test_an_edited_file_keeps_its_bytes_and_its_directory(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._safe_remove import remove_tree_if_hash

    root, skill, manifest = _skill(tmp_path)
    (skill / "refs" / "ref.md").write_bytes(b"my edit\n")
    kept = remove_tree_if_hash(skill, root, _allowed(manifest, root))
    assert kept == [".claude/skills/trw-old/refs/ref.md (not TRW's unchanged bytes)"]
    assert (skill / "refs" / "ref.md").read_bytes() == b"my edit\n"
    assert not (skill / "SKILL.md").exists()
    assert _trash(root) == [_A]


def test_a_file_added_during_the_sweep_keeps_the_directory(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._safe_remove import remove_tree_if_hash

    root, skill, manifest = _skill(tmp_path)
    base = _allowed(manifest, root)

    def add_then_allow(f: Path) -> set[str]:
        new = skill / "notes.md"
        if not new.exists():
            new.write_bytes(b"added after the listing\n")
        return base(f)

    kept = remove_tree_if_hash(skill, root, add_then_allow)
    assert (skill / "notes.md").read_bytes() == b"added after the listing\n"
    assert kept  # the directory stays, and says why


def test_a_symlink_inside_the_tree_is_kept_and_not_followed(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._safe_remove import remove_tree_if_hash

    root, skill, manifest = _skill(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_bytes(_A)
    (skill / "link.md").symlink_to(outside)
    kept = remove_tree_if_hash(skill, root, _allowed(manifest, root))
    assert any("link.md (not a regular file" in k for k in kept)
    assert (skill / "link.md").is_symlink()
    assert outside.read_bytes() == _A


def test_a_late_write_through_a_held_fd_lands_in_trash(tmp_path: Path) -> None:
    """Red on the old sweep: rmtree unlinked the inode the writer held."""
    from trw_mcp.bootstrap._version_migration_predecessors import remove_proven

    root, skill, manifest = _skill(tmp_path)
    with open_fd_writer(skill / "SKILL.md") as writer:
        remove_proven(skill, manifest, root, {})
        writer.write(b"late write\n")
    assert b"late write\n" in _trash(root)


def test_predecessor_sweep_keeps_an_edit_saved_after_the_ownership_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Red on the old sweep: preserve_unowned proved ownership, then rmtree deleted the later edit."""
    from trw_mcp.bootstrap import _version_migration_predecessors as preds

    root, skill, manifest = _skill(tmp_path)
    (root / ".claude" / "skills" / "trw-new").mkdir()
    real = preds.preserve_unowned

    def check_then_edit(
        artifact: Path, hashes: dict[str, str] | None, target: Path, result: dict[str, list[str]]
    ) -> bool:
        verdict = real(artifact, hashes, target, result)
        (skill / "SKILL.md").write_bytes(b"edited after the check\n")
        return verdict

    monkeypatch.setattr(preds, "preserve_unowned", check_then_edit)
    result: dict[str, list[str]] = {}
    preds._migrate_predecessor_set(
        root / ".claude" / "skills",
        {"trw-old": "trw-new"},
        result,
        is_dir_artifact=True,
        log_event="x",
        manifest_hashes=manifest,
        target_dir=root,
    )
    assert (skill / "SKILL.md").read_bytes() == b"edited after the check\n"
    assert any("SKILL.md" in w for w in result["warnings"])


def test_retire_disabled_skill_keeps_an_edit_saved_after_the_compare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Red on the old retire: it byte-compared, then rmtree'd whatever was there."""
    from trw_mcp.bootstrap import _optional_skills
    from trw_mcp.bootstrap import _safe_remove as sr

    root = tmp_path / "proj"
    dest_root = root / ".claude" / "skills"
    name = next(iter(_optional_skills.CONDITIONAL_SKILLS))
    dest = dest_root / name
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_bytes(_A)
    monkeypatch.setattr(_optional_skills, "skill_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr("trw_mcp.bootstrap._client_skills.skill_files", lambda *_a, **_k: [("SKILL.md", _A)])
    real = sr.remove_tree_if_hash

    def edit_then_remove(artifact: Path, root_: Path, allowed):  # type: ignore[no-untyped-def]
        (artifact / "SKILL.md").write_bytes(b"edited at the act\n")
        return real(artifact, root_, allowed)

    monkeypatch.setattr(_optional_skills, "remove_tree_if_hash", edit_then_remove)
    result: dict[str, list[str]] = {}
    _optional_skills.retire_disabled_skills(dest_root, tmp_path, result, ".claude/skills", project_root=root)
    assert (dest / "SKILL.md").read_bytes() == b"edited at the act\n"
    assert any(w.startswith(f".claude/skills/{name}/SKILL.md") for w in result["warnings"])


@pytest.mark.parametrize("extra", ["user_file", "nested_same_name"])
def test_retire_disabled_skill_trashes_the_shipped_skill_md_and_keeps_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: str
) -> None:
    """SKILL-DIR-ANCHOR ruling (canary F2): an unmodified shipped SKILL.md must not stay live beside user files."""
    from trw_mcp.bootstrap import _optional_skills

    root = tmp_path / "proj"
    dest_root = root / ".claude" / "skills"
    name = next(iter(_optional_skills.CONDITIONAL_SKILLS))
    dest = dest_root / name
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_bytes(_A)
    if extra == "user_file":
        mine = dest / "notes.md"
        mine.write_bytes(b"mine\n")
    else:
        (dest / "sub").mkdir()
        mine = dest / "sub" / "SKILL.md"
        mine.write_bytes(_A)  # same bytes and name as the shipped file, but TRW never shipped this path
    monkeypatch.setattr(_optional_skills, "skill_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr("trw_mcp.bootstrap._client_skills.skill_files", lambda *_a, **_k: [("SKILL.md", _A)])
    result: dict[str, list[str]] = {}
    _optional_skills.retire_disabled_skills(dest_root, tmp_path, result, ".claude/skills", project_root=root)
    assert not (dest / "SKILL.md").exists()
    assert _trash(root) == [_A]
    assert mine.read_bytes() == (b"mine\n" if extra == "user_file" else _A)
    assert any(mine.name in w for w in result["warnings"])
