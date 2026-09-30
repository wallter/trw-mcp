"""SYMLINKED-CLIENT-DIR-MSG: a symlinked client dir or surface is refused with an actionable message.

``update-project`` used to stop at the FIRST symlink with a bare "contains a symlinked directory: <rel>", and
``uninstall`` deleted ``.trw`` and the manifest even when it had refused items, orphaning the residue. The
refusal semantics are unchanged (nothing is followed, skipped or written through); these tests pin the message
and the ``.trw`` retention, over tmp projects only, ending in a whole-tree user-bytes assertion.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

import pytest

from tests._fs_hazards import assert_user_bytes_preserved, snapshot_user_bytes, unreadable
from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.server._subcommands import _run_uninstall, _run_update_project

pytestmark = pytest.mark.integration

_FIX = "replace each symlink with a real directory (copy its contents in), then re-run"


def _tree(root: Path) -> dict[str, str]:
    """Every entry under root (files by sha256, symlinks by link text, dirs), never following a link."""
    out: dict[str, str] = {}
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            full = Path(dirpath) / name
            rel = str(full.relative_to(root))
            if full.is_symlink():
                out[rel] = "link:" + os.readlink(full)
            elif full.is_file():
                out[rel] = hashlib.sha256(full.read_bytes()).hexdigest()
            else:
                out[rel] = "dir"
    return out


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()
    result = init_project(project, ide="all")
    assert not result["errors"], result["errors"]
    return project


def _link_dir_to_outside(project: Path, rel: str, outside: Path) -> Path:
    """Move the installed dir's contents out, put a sentinel beside them, and leave a symlink in its place."""
    real = project / rel
    assert real.is_dir(), f"precondition: {rel} installed"
    real.rename(outside)
    (outside / "user-sentinel.txt").write_bytes(b"mine, do not touch\n")
    real.symlink_to(outside)
    return outside


def _ns(project: Path, **kw: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "target_dir": str(project),
        "dry_run": False,
        "yes": True,
        "user_tier": False,
        "keep_memory": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def test_update_refuses_every_symlinked_client_dir_naming_each_target(tmp_path: Path) -> None:
    project = _project(tmp_path)
    out_a = _link_dir_to_outside(project, ".cursor/skills", tmp_path / "outside-a")
    out_b = _link_dir_to_outside(project, ".claude/hooks", tmp_path / "outside-b")
    outside_before, project_before = _tree(tmp_path / "outside-a"), _tree(project)
    outside_b_before = _tree(out_b)
    user_bytes = snapshot_user_bytes(tmp_path)

    result = update_project(project)

    assert len(result["errors"]) == 1, result["errors"]
    message = result["errors"][0]
    assert message.startswith("Failed to snapshot update targets: transaction directory contains a symlinked directory")
    for rel, target in ((".cursor/skills", out_a), (".claude/hooks", out_b)):
        assert f"{rel} -> {target}" in message
    assert _FIX in message
    assert _tree(out_a) == outside_before and _tree(out_b) == outside_b_before
    assert (out_a / "user-sentinel.txt").read_bytes() == b"mine, do not touch\n"
    assert _tree(project) == project_before, "no partial update"
    assert (project / ".cursor/skills").is_symlink() and (project / ".claude/hooks").is_symlink()
    assert_user_bytes_preserved(user_bytes, tmp_path)


def test_update_cli_exits_1_on_symlinked_dir(tmp_path: Path) -> None:
    project = _project(tmp_path)
    outside = _link_dir_to_outside(project, ".cursor/skills", tmp_path / "outside")
    before, project_before = _tree(outside), _tree(project)
    ns = argparse.Namespace(target_dir=str(project), pip_install=False, dry_run=False, ide=None, reprovision=None)

    with pytest.raises(SystemExit) as exc:
        _run_update_project(ns)

    assert exc.value.code == 1
    assert _tree(outside) == before and _tree(project) == project_before


def test_update_refuses_symlinked_top_level_surface(tmp_path: Path) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside-agents.md"
    outside.write_bytes(b"# the user's real AGENTS.md\n")
    (project / "AGENTS.md").unlink()
    (project / "AGENTS.md").symlink_to(outside)
    project_before, outside_bytes = _tree(project), outside.read_bytes()
    user_bytes = snapshot_user_bytes(tmp_path)

    result = update_project(project)

    assert len(result["errors"]) == 1, result["errors"]
    message = result["errors"][0]
    assert f"AGENTS.md -> {outside}" in message
    assert _FIX in message
    assert outside.read_bytes() == outside_bytes
    assert _tree(project) == project_before
    assert_user_bytes_preserved(user_bytes, tmp_path)


def test_uninstall_with_symlinked_dir_keeps_trw_and_manifest_and_guides(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path)
    outside = _link_dir_to_outside(project, ".claude/hooks", tmp_path / "outside")
    outside_before = _tree(outside)
    trw_before = _tree(project / ".trw")
    user_bytes = snapshot_user_bytes(outside)
    (project / "README.md").write_bytes(b"# the user's readme\n")
    assert (project / ".trw" / "managed-artifacts.yaml").is_file()

    with pytest.raises(SystemExit) as exc:
        _run_uninstall(_ns(project))

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert f".claude/hooks -> {outside}" in captured.err
    assert "replace each symlink with a real directory" in captured.err
    assert (project / ".trw").is_dir()
    assert (project / ".trw" / "managed-artifacts.yaml").is_file()
    assert _tree(project / ".trw") == trw_before, ".trw and the manifest are byte-identical"
    assert not (project / ".cursor" / "skills").exists(), "unrelated TRW files are still removed"
    assert (project / ".claude/hooks").is_symlink()
    assert os.readlink(project / ".claude/hooks") == str(outside)
    assert _tree(outside) == outside_before
    assert (project / "README.md").read_bytes() == b"# the user's readme\n"
    assert_user_bytes_preserved(user_bytes, outside)


def test_uninstall_without_symlinks_still_removes_trw(tmp_path: Path) -> None:
    project = _project(tmp_path)

    _run_uninstall(_ns(project))

    assert not (project / ".trw").exists()
    assert not (project / ".cursor" / "skills").exists()


def test_second_uninstall_after_the_link_is_replaced_removes_trw(tmp_path: Path) -> None:
    project = _project(tmp_path)
    outside = _link_dir_to_outside(project, ".claude/hooks", tmp_path / "outside")
    with pytest.raises(SystemExit):
        _run_uninstall(_ns(project))
    assert (project / ".trw").is_dir()
    user_bytes = snapshot_user_bytes(outside)

    # the user follows the guidance: a real directory holding the same files
    (project / ".claude/hooks").unlink()
    (project / ".claude/hooks").mkdir()
    for f in outside.iterdir():
        (project / ".claude/hooks" / f.name).write_bytes(f.read_bytes())
    sentinel = project / ".claude/hooks" / "user-sentinel.txt"

    _run_uninstall(_ns(project))

    assert not (project / ".trw").exists()
    assert sentinel.read_bytes() == b"mine, do not touch\n", "the user's own file survives"
    assert_user_bytes_preserved(user_bytes, outside)
    assert_user_bytes_preserved(user_bytes, tmp_path)


def test_uninstall_dry_run_reports_that_trw_would_be_kept(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    outside = _link_dir_to_outside(project, ".claude/hooks", tmp_path / "outside")
    project_before, outside_before = _tree(project), _tree(outside)

    _run_uninstall(_ns(project, dry_run=True))

    captured = capsys.readouterr()
    assert ".trw and the manifest would be kept" in captured.out
    assert f".claude/hooks -> {outside}" in captured.err
    assert _tree(project) == project_before and _tree(outside) == outside_before


# ---- review follow-up: refusals that are not symlinks, rc, dry-run coverage, TRW-owned keeps -------------------


def _blank_manifest_hashes(project: Path) -> None:
    """Make every plain surface 'uncovered' (no manifest key under it), as after a lost recorder."""
    import yaml

    path = project / ".trw" / "managed-artifacts.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["content_hashes"] = {}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.mark.parametrize(
    "refusal",
    [
        "path is not under root",
        "path could not be resolved: boom",
        "root could not be resolved: boom",
    ],
)
def test_uncovered_surface_refusal_of_any_kind_keeps_trw_and_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: str
) -> None:
    import trw_mcp.bootstrap._uninstall_manifest as um

    project = _project(tmp_path)
    _blank_manifest_hashes(project)
    real = um.path_refusal
    hit: list[Path] = []

    def fake(path: Path, root: Path) -> str | None:
        if path == project / ".claude" / "hooks":
            hit.append(path)
            return refusal
        return real(path, root)

    monkeypatch.setattr(um, "path_refusal", fake)
    trw_before = _tree(project / ".trw")
    hooks_before = _tree(project / ".claude" / "hooks")

    with pytest.raises(SystemExit) as exc:
        _run_uninstall(_ns(project))

    assert hit, "the refusal was actually exercised"
    assert exc.value.code == 1, "a refused item is a partial uninstall"
    assert _tree(project / ".trw") == trw_before
    assert _tree(project / ".claude" / "hooks") == hooks_before


def test_uncovered_symlinked_dir_exits_1_and_keeps_trw(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    outside = _link_dir_to_outside(project, ".cursor/skills", tmp_path / "outside")
    _blank_manifest_hashes(project)
    trw_before, outside_before = _tree(project / ".trw"), _tree(outside)
    user_bytes = snapshot_user_bytes(outside)

    with pytest.raises(SystemExit) as exc:
        _run_uninstall(_ns(project))

    assert exc.value.code == 1
    assert f".cursor/skills -> {outside}" in capsys.readouterr().err
    assert _tree(project / ".trw") == trw_before
    assert _tree(outside) == outside_before
    assert_user_bytes_preserved(user_bytes, tmp_path)


@pytest.mark.parametrize("surface", ["AGENTS.md", ".mcp.json"])
def test_uninstall_and_dry_run_with_symlinked_managed_or_merged_surface_keep_trw(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], surface: str
) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside" / "file"
    outside.parent.mkdir()
    outside.write_bytes((project / surface).read_bytes())
    (project / surface).unlink()
    (project / surface).symlink_to(outside)
    outside_before = outside.read_bytes()
    trw_before, project_before = _tree(project / ".trw"), _tree(project)
    user_bytes = snapshot_user_bytes(outside.parent)

    _run_uninstall(_ns(project, dry_run=True))
    dry = capsys.readouterr()
    assert ".trw and the manifest would be kept" in dry.out
    assert f"{surface} -> {outside}" in dry.err
    assert _tree(project) == project_before

    with pytest.raises(SystemExit) as exc:
        _run_uninstall(_ns(project))

    assert exc.value.code == 1
    assert f"{surface} -> {outside}" in capsys.readouterr().err
    assert _tree(project / ".trw") == trw_before
    assert outside.read_bytes() == outside_before
    assert (project / surface).is_symlink()
    assert_user_bytes_preserved(user_bytes, tmp_path)


def test_trw_owned_file_kept_inside_a_removed_skill_dir_keeps_trw(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A symlink inside a TRW skill dir is kept (may be TRW's), so its manifest record must survive for a re-run."""
    project = _project(tmp_path)
    skill = next(p.parent for p in sorted((project / ".claude" / "skills").rglob("SKILL.md")))
    outside = tmp_path / "outside" / "note.md"
    outside.parent.mkdir()
    outside.write_bytes(b"user's note\n")
    (skill / "linked-note.md").symlink_to(outside)
    trw_before = _tree(project / ".trw")
    user_bytes = snapshot_user_bytes(outside.parent)

    with pytest.raises(SystemExit) as exc:
        _run_uninstall(_ns(project))

    assert exc.value.code == 1, "keeping .trw is a partial uninstall: a script must not go on to rm it"
    err = capsys.readouterr().err
    assert f"Kept .trw because TRW files remain under {skill.relative_to(project)}" in err
    assert (skill / "linked-note.md").is_symlink()
    assert outside.read_bytes() == b"user's note\n"
    assert _tree(project / ".trw") == trw_before, "the manifest record for the kept TRW skill stays for a re-run"
    assert_user_bytes_preserved(user_bytes, tmp_path)


def test_edited_auxiliary_skill_file_is_kept_like_any_preserved_edit_rc0_and_trw_removed(tmp_path: Path) -> None:
    """An edit never changes on a re-run, so it must not hold .trw (and rc 1) forever."""
    project = _project(tmp_path)
    aux = next(p for p in sorted((project / ".claude" / "skills").rglob("*")) if p.is_file() and p.name != "SKILL.md")
    aux.write_bytes(aux.read_bytes() + b"\nmy own edit\n")
    edited = aux.read_bytes()
    user_bytes = {hashlib.sha256(edited).hexdigest(): [aux.name]}

    _run_uninstall(_ns(project))  # no SystemExit: rc 0

    assert aux.read_bytes() == edited, "the user's edit survives byte-identical"
    assert not (project / ".trw").exists()
    assert_user_bytes_preserved(user_bytes, project)

    _run_uninstall(_ns(project))  # a second run is a no-op, rc 0
    assert aux.read_bytes() == edited


def test_managed_refusal_that_is_not_a_symlink_prints_the_real_reason_and_no_symlink_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import trw_mcp.server._subcommands_lifecycle as lc
    import trw_mcp.server._uninstall_report as lc_report

    project = _project(tmp_path)
    real = lc_report.path_refusal
    monkeypatch.setattr(
        lc_report, "path_refusal", lambda p, r: "path is not under root" if p == project / "AGENTS.md" else real(p, r)
    )
    monkeypatch.setattr(
        lc, "_remove_managed_block_file", lambda p, r, dry_run: "refused" if p.name == "AGENTS.md" else None
    )
    trw_before = _tree(project / ".trw")

    with pytest.raises(SystemExit) as exc:
        _run_uninstall(_ns(project))

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "Error updating AGENTS.md: path is not under root" in captured.out
    assert "refused (symlink)" not in captured.out
    assert "Refused, because these paths are symlinks" not in captured.err
    assert _tree(project / ".trw") == trw_before


# ---- every _RERUN_CAN_CHANGE member, through the real _run_uninstall -------------------------------------------

_SKILL = ".claude/skills/trw-audit"  # ships an auxiliary file besides SKILL.md
_AUX = "audit-framework.md"
_SKILL_KEY = "trw-audit/SKILL.md"  # manifest keys are relative to the skills dir


def _plant_symlink(skill: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    outside = tmp_path / "user" / "linked-target.md"
    outside.write_bytes(b"user's linked note\n")
    (skill / "linked-note.md").symlink_to(outside)
    return skill / "linked-note.md"


def _plant_fifo(skill: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    os.mkfifo(skill / "pipe")
    return skill / "pipe"


def _plant_changed_mid_run(skill: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Drive the race for real: SKILL.md is edited right after its bytes are proven TRW's, before the capture."""
    import trw_mcp.bootstrap._uninstall_skill_dir as sd  # the skill-dir walk proves ownership here

    real = sd._not_owned_reason
    target = skill / "SKILL.md"

    def proven_then_edited(path: Path, *args: object) -> str | None:
        why = real(path, *args)  # type: ignore[arg-type]
        if path == target and why is None:
            target.write_bytes(target.read_bytes() + b"\nsaved mid-run\n")
        return why

    monkeypatch.setattr(sd, "_not_owned_reason", proven_then_edited)
    return target


_RERUN_CASES = {
    "symlink": _plant_symlink,
    "not a regular file": _plant_fifo,
    "changed during uninstall": _plant_changed_mid_run,
}


def test_rerun_cases_cover_exactly_the_rerun_can_change_set() -> None:
    from trw_mcp.bootstrap._uninstall_manifest import _RERUN_CAN_CHANGE

    assert set(_RERUN_CASES) | {"unreadable"} == set(_RERUN_CAN_CHANGE)


@pytest.mark.parametrize("reason", ["symlink", "unreadable", "not a regular file", "changed during uninstall"])
def test_every_rerun_can_change_reason_keeps_trw_exits_1_and_names_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], reason: str
) -> None:
    import contextlib

    import yaml

    project = _project(tmp_path)
    skill = project / _SKILL
    assert (skill / _AUX).is_file(), "precondition: the skill ships an auxiliary file"
    (tmp_path / "user").mkdir()
    (tmp_path / "user" / "notes.md").write_bytes(b"user notes\n")
    user_bytes = snapshot_user_bytes(tmp_path / "user")
    guard: contextlib.AbstractContextManager[None] = contextlib.nullcontext()
    if reason == "unreadable":
        planted = skill / _AUX
        guard = unreadable(planted)
    else:
        planted = _RERUN_CASES[reason](skill, monkeypatch, tmp_path)
    trw_before = _tree(project / ".trw")

    with guard:
        with pytest.raises(SystemExit) as exc:
            _run_uninstall(_ns(project))

    assert exc.value.code == 1
    out = capsys.readouterr()
    assert f"{planted.relative_to(project)} ({reason})" in out.out, "the emitted reason is the set literal"
    assert "Kept .trw because TRW files remain under .claude/skills/trw-audit" in out.err
    # A capture linked back after a mid-run edit keeps its record in .trw/trash (remove_if_hash never unlinks;
    # its data is the user's inode, not a copy); everything else under .trw is untouched.
    after = {k: v for k, v in _tree(project / ".trw").items() if k != "trash" and not k.startswith("trash/")}
    assert after == trw_before, ".trw byte-identical outside the capture area"
    manifest = yaml.safe_load((project / ".trw" / "managed-artifacts.yaml").read_text(encoding="utf-8"))
    assert _SKILL_KEY in manifest["content_hashes"], "the manifest record stays for the re-run"
    assert planted.exists() or planted.is_symlink()
    # Scoped to the user's tree: the whole-tmp walk would open the FIFO and block.
    assert_user_bytes_preserved(user_bytes, tmp_path / "user")


def test_edited_skill_md_is_a_final_reason_rc0_and_trw_removed(tmp_path: Path) -> None:
    project = _project(tmp_path)
    skill_md = project / _SKILL / "SKILL.md"
    skill_md.write_bytes(skill_md.read_bytes() + b"\nmy edit\n")
    edited = skill_md.read_bytes()
    user_bytes = {hashlib.sha256(edited).hexdigest(): ["SKILL.md"]}

    _run_uninstall(_ns(project))  # no SystemExit: rc 0

    assert skill_md.read_bytes() == edited
    assert not (project / ".trw").exists()
    assert_user_bytes_preserved(user_bytes, project)
