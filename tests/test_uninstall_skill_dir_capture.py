"""UNINSTALL-SKILL-DIR-CAPTURE: a skill file edited after its bytes were proven TRW's survives uninstall.

Skill-directory files were hashed, then unlinked with ``safe_remove``: an edit saved between the two was deleted.
They now go through ``remove_if_hash`` against the proven digest (capture, re-hash, link back on a mismatch).
"""

from __future__ import annotations

import argparse
import contextlib
from pathlib import Path

import pytest

from trw_mcp.bootstrap import _trash, _uninstall_skill_dir, init_project
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]


def _uninstall(project: Path) -> None:
    """A kept TRW-recorded file makes uninstall exit 1 by design; the assertions are on the bytes."""
    with contextlib.suppress(SystemExit):
        _run_uninstall(
            argparse.Namespace(
                target_dir=str(project), dry_run=False, yes=True, user_tier=False, keep_memory=False, ide=None
            )
        )


# The capture needs dir-fd renames and no-replace links; where remove_if_hash declines them it keeps every file.
_CAPTURE_SUPPORTED = pytest.mark.skipif(
    bool(_trash._UNSUPPORTED), reason=str(_trash._UNSUPPORTED)
)  # skip-category: platform


@_CAPTURE_SUPPORTED
@pytest.mark.parametrize("name", ["SKILL.md", "other"])
def test_a_skill_file_edited_after_its_proof_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    skill_files = sorted(p for p in (tmp_path / ".claude" / "skills").rglob("*") if p.is_file())
    victim = next(p for p in skill_files if (p.name == "SKILL.md") == (name == "SKILL.md"))
    real = _uninstall_skill_dir._not_owned_reason

    def proven_then_edited(path: Path, *args: object) -> str | None:
        why = real(path, *args)  # type: ignore[arg-type]
        if path == victim and why is None:
            path.write_text(path.read_text(encoding="utf-8") + "\nMy note, saved just now.\n", encoding="utf-8")
        return why

    monkeypatch.setattr(_uninstall_skill_dir, "_not_owned_reason", proven_then_edited)
    _uninstall(tmp_path)

    assert victim.is_file() and "My note, saved just now." in victim.read_text(encoding="utf-8")
    assert "changed during uninstall" in capsys.readouterr().out, "the kept file is reported, not silently skipped"


def test_an_uncovered_surface_whose_bytes_stay_in_trash_names_where_they_are(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r3 KI: a copy retained in trash is named, as apply_removal names it; never silently dropped."""
    from trw_mcp.bootstrap import _uninstall_manifest
    from trw_mcp.bootstrap._trash import Removal

    target = tmp_path
    surface = target / "REVIEW.md"
    surface.write_text("x", encoding="utf-8")
    retained = target / ".trw" / "trash" / "20260930T000000Z-abc" / "data"
    monkeypatch.setattr(
        _uninstall_manifest,
        "remove_if_hash",
        lambda path, *_a, **_k: Removal("REVIEW.md", path, "retained", None, retained, "could not link back"),
    )
    u = _uninstall_manifest.SurfaceDisposition("REVIEW.md", surface, "remove", "", "0" * 64)

    status, detail = _uninstall_manifest.remove_judged_surface(u, target, {})

    assert status == "error" and str(retained) in detail


def test_a_skill_file_whose_folder_moved_mid_removal_names_where_the_copy_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex KI on SKILL-DIR-CAPTURE: the bytes are not at the path, so the failure names the retained copy."""
    from trw_mcp.bootstrap._trash import Removal

    path = tmp_path / ".claude" / "skills" / "demo" / "refs" / "a.md"
    retained = tmp_path / ".trw" / "trash" / "20260930T000000Z-abc" / "data"
    monkeypatch.setattr(
        _uninstall_skill_dir,
        "remove_if_hash",
        lambda p, *_a, **_k: Removal("refs/a.md", p, "kept", None, retained, "bytes differ; the parent moved"),
    )
    kept: list[tuple[Path, str]] = []
    failures: list[tuple[Path, str]] = []

    _uninstall_skill_dir._remove_proven(path, Path("refs/a.md"), tmp_path, "0" * 64, {}, kept, failures)

    assert kept == [] and failures == [(path, f"kept (bytes differ; the parent moved); a copy is in {retained}")]
