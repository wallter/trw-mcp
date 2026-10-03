"""FS-LINT row 3: ``_remove_stale_files_in_kept_dir`` retires a dropped file in place (HB-2).

A file TRW recorded and the bundle dropped is deleted when its bytes still match the record (or git holds it
clean); an edited one is kept and named with the command that removes it. (Row 4, ``retire_legacy_claude_md``, was removed with
the one-time 8.0 CLAUDE.md retirement in REMOVE-S2.)
"""

from __future__ import annotations

import hashlib
from pathlib import Path


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


def test_a_dropped_file_is_deleted_in_place_and_the_kept_skill_stays(tmp_path: Path) -> None:
    root, dropped, manifest, surface = _kept_skill(tmp_path)
    result: dict[str, list[str]] = {}
    _run_row3(root, surface, manifest, result)
    assert not dropped.exists()
    assert (dropped.parent / "SKILL.md").read_bytes() == b"kept\n"
    assert result["retired"] == [".agents/skills/trw-keep/old.md"]
    assert _trash(root) == []


def test_an_edited_dropped_file_is_kept_and_named_with_the_removal_command(tmp_path: Path) -> None:
    root, dropped, manifest, surface = _kept_skill(tmp_path)
    dropped.write_bytes(b"my edit\n")
    result: dict[str, list[str]] = {}
    _run_row3(root, surface, manifest, result)
    assert dropped.read_bytes() == b"my edit\n"
    assert any(w.endswith("rm .agents/skills/trw-keep/old.md") for w in result["warnings"])
