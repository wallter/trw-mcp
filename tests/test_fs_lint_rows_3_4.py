"""FS-LINT row 3: a hash-then-delete site routes through ``remove_if_hash`` (HB-2).

Row 3, ``_remove_stale_files_in_kept_dir``, proved TRW authorship from bytes read earlier and then
``unlink``ed the name, so an edit saved in between (or a write through a held fd) was destroyed. The file is
now captured into ``.trw/trash`` and re-verified there. (Row 4, ``retire_legacy_claude_md``, was removed with
the one-time 8.0 CLAUDE.md retirement in REMOVE-S2.)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


def _trash(root: Path) -> list[bytes]:
    trash = root / ".trw" / "trash"
    return sorted(p.read_bytes() for p in trash.glob("*/data")) if trash.is_dir() else []


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
