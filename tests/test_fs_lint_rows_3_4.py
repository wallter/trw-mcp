"""FS-LINT rows 3-4: hash-then-delete sites route through ``remove_if_hash`` (HB-2).

Row 3, ``_remove_stale_files_in_kept_dir``, and row 4, ``retire_legacy_claude_md``, proved TRW authorship from
bytes read earlier and then ``unlink``ed the name, so an edit saved in between (or a write through a held fd)
was destroyed. The file is now captured into ``.trw/trash`` and re-verified there.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests._fs_hazards import open_fd_writer


def _trash(root: Path) -> list[bytes]:
    trash = root / ".trw" / "trash"
    return sorted(p.read_bytes() for p in trash.glob("*/data")) if trash.is_dir() else []


# --- row 4: retire_legacy_claude_md -------------------------------------------------------------------


def _trw_only_claude_md(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "proj"
    root.mkdir()
    path = root / "CLAUDE.md"
    path.write_text("# CLAUDE.md\n\n@AGENTS.md\n", encoding="utf-8")
    return root, path


def test_trw_only_claude_md_moves_to_trash(tmp_path: Path) -> None:
    from trw_mcp.state.claude_md._orphan_strip import retire_legacy_claude_md

    root, path = _trw_only_claude_md(tmp_path)
    assert retire_legacy_claude_md(root) == "removed"
    assert not path.exists()
    assert _trash(root) == [b"# CLAUDE.md\n\n@AGENTS.md\n"]


def test_a_line_added_after_the_check_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Red on the old code: it decided TRW-only, then unlinked whatever the file held by then."""
    from trw_mcp.state.claude_md import _orphan_strip

    root, path = _trw_only_claude_md(tmp_path)
    real = _orphan_strip._is_trw_only

    def decide_then_edit(remaining: str) -> bool:
        verdict = real(remaining)
        path.write_text("@AGENTS.md\n\nMy own project notes.\n", encoding="utf-8")
        return verdict

    monkeypatch.setattr(_orphan_strip, "_is_trw_only", decide_then_edit)
    assert _orphan_strip.retire_legacy_claude_md(root) == "kept"
    assert path.read_text(encoding="utf-8") == "@AGENTS.md\n\nMy own project notes.\n"


def test_a_held_fd_write_after_retire_lands_in_trash(tmp_path: Path) -> None:
    from trw_mcp.state.claude_md._orphan_strip import retire_legacy_claude_md

    root, path = _trw_only_claude_md(tmp_path)
    with open_fd_writer(path) as writer:
        assert retire_legacy_claude_md(root) == "removed"
        writer.write(b"late edit\n")
    assert _trash(root) == [b"late edit\n"]


# --- row 3: _remove_stale_files_in_kept_dir -----------------------------------------------------------


def _kept_skill(tmp_path: Path) -> tuple[Path, Path, dict[str, str], object]:
    import dataclasses

    root = tmp_path / "proj"
    skill = root / ".agents" / "skills" / "trw-keep"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"kept\n")
    dropped = skill / "old.md"
    dropped.write_bytes(b"dropped by the bundle\n")
    manifest = {
        ".agents/skills/trw-keep/SKILL.md": hashlib.sha256(b"kept\n").hexdigest(),
        ".agents/skills/trw-keep/old.md": hashlib.sha256(b"dropped by the bundle\n").hexdigest(),
    }
    surface = dataclasses.replace(next(s for s in _surfaces() if s.client_dir == ".agents/skills"), bundled_files=None)
    return root, dropped, manifest, surface


def _surfaces() -> list[object]:
    """Every ClientArtifactSurface the module defines (module-level or inside a list/tuple)."""
    from trw_mcp.bootstrap import _version_migration_clients as vmc

    kind = vmc.ClientArtifactSurface
    found = [v for v in vars(vmc).values() if isinstance(v, kind)]
    found += [s for v in vars(vmc).values() if isinstance(v, (list, tuple)) for s in v if isinstance(s, kind)]
    return found


def _run_row3(root: Path, surface: object, manifest: dict[str, str], result: dict[str, list[str]]) -> None:
    from trw_mcp.bootstrap._version_migration_clients import _remove_stale_files_in_kept_dir

    skill = root / ".agents" / "skills" / "trw-keep"
    bundled = {".agents/skills/trw-keep/SKILL.md"}
    _remove_stale_files_in_kept_dir(surface, skill, bundled, manifest, result, root)  # type: ignore[arg-type]


def test_a_dropped_file_moves_to_trash_and_the_kept_skill_stays(tmp_path: Path) -> None:
    root, dropped, manifest, surface = _kept_skill(tmp_path)
    _run_row3(root, surface, manifest, {})
    assert not dropped.exists()
    assert (dropped.parent / "SKILL.md").read_bytes() == b"kept\n"
    assert _trash(root) == [b"dropped by the bundle\n"]


def test_an_edit_after_the_hash_check_is_put_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Red on the old code: it hashed, then unlinked whatever the name held at the act."""
    from trw_mcp.bootstrap import _version_migration_clients as vmc

    root, dropped, manifest, surface = _kept_skill(tmp_path)
    real = vmc.remove_if_hash

    def edit_then_remove(path: Path, root_: Path, expected: str, **kw: object):  # type: ignore[no-untyped-def]
        path.write_bytes(b"my edit\n")
        return real(path, root_, expected, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(vmc, "remove_if_hash", edit_then_remove)
    result: dict[str, list[str]] = {}
    _run_row3(root, surface, manifest, result)
    assert dropped.read_bytes() == b"my edit\n"
    assert any("old.md" in w for w in result["warnings"])


@pytest.mark.usefixtures("no_memory_daemon")
def test_retired_claude_md_stays_retired_in_a_real_git_repo(tmp_path: Path) -> None:
    """Red on e547de76: the uncommitted-changes guard restored CLAUDE.md after every capture (trash 1, 2, 3)."""
    import subprocess

    from trw_mcp.bootstrap import init_project, update_project

    root = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "i",
        ],
        check=True,
    )
    assert not init_project(root, ide="claude-code")["errors"]
    claude = root / "CLAUDE.md"
    counts = []
    for _ in range(3):
        claude.write_text("# CLAUDE.md\n\n@AGENTS.md\n", encoding="utf-8") if not counts else None
        result = update_project(root, ide="claude-code")
        counts.append(len(_trash(root)))
        assert not claude.exists()
        if len(counts) == 1:
            assert "CLAUDE.md" in result.get("trashed", [])
    assert counts == [1, 1, 1]
