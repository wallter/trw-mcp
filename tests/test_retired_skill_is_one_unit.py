"""A retired or curated-out skill is retired as one unit and reported once (feedback sub_1-sANJtIT9H-nx7q items 2, 3).

Before the fix a kept retired skill was named once per file and again per directory, and a skill dir could lose its
``SKILL.md`` while a TRW-shipped companion (``.github/skills/trw-commit/PR-TEMPLATE.md``) stayed behind.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.bootstrap._retired_artifacts import retired_artifact_notices
from trw_mcp.bootstrap._version_migration_clients import _remove_stale_client_artifacts


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ship(root: Path, rel: str, data: bytes) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return _sha(data)


def test_a_curated_out_skill_goes_with_its_trw_shipped_companion(tmp_path: Path) -> None:
    """trw-commit is shipped, but not to Copilot: SKILL.md and PR-TEMPLATE.md (the shipped bytes) both go."""
    from trw_mcp.bootstrap._utils import _DATA_DIR

    shipped = (_DATA_DIR / "skills" / "trw-commit" / "PR-TEMPLATE.md").read_bytes()
    skill = b"copilot rendering of the skill\n"
    _ship(tmp_path, ".github/skills/trw-commit/SKILL.md", skill)
    _ship(tmp_path, ".github/skills/trw-commit/PR-TEMPLATE.md", shipped)
    result: dict[str, list[str]] = {"warnings": []}
    _remove_stale_client_artifacts(
        tmp_path, result, manifest_hashes={".github/skills/trw-commit/SKILL.md": _sha(skill)}
    )
    assert not (tmp_path / ".github" / "skills" / "trw-commit").exists()
    assert sorted(result["retired"]) == [
        ".github/skills/trw-commit/PR-TEMPLATE.md",
        ".github/skills/trw-commit/SKILL.md",
    ]


def test_a_user_file_in_a_retired_skill_keeps_the_whole_skill_and_deletes_nothing(tmp_path: Path) -> None:
    skill = b"old skill\n"
    _ship(tmp_path, ".agents/skills/trw-decision/SKILL.md", skill)
    _ship(tmp_path, ".agents/skills/trw-decision/my-notes.md", b"mine\n")
    result: dict[str, list[str]] = {"warnings": []}
    _remove_stale_client_artifacts(
        tmp_path, result, manifest_hashes={".agents/skills/trw-decision/SKILL.md": _sha(skill)}
    )
    assert (tmp_path / ".agents/skills/trw-decision/SKILL.md").read_bytes() == skill  # not half removed
    assert (tmp_path / ".agents/skills/trw-decision/my-notes.md").read_bytes() == b"mine\n"
    assert not result.get("retired")
    assert result["retired_kept"] == [".agents/skills/trw-decision"]


def test_a_unrecorded_companion_of_a_retired_skill_keeps_the_whole_skill(tmp_path: Path) -> None:
    """A retired skill is not in the bundle, so a companion with no record cannot be proven TRW's."""
    skill = b"old skill\n"
    _ship(tmp_path, ".github/skills/trw-sprint-finish/SKILL.md", skill)
    _ship(tmp_path, ".github/skills/trw-sprint-finish/TEMPLATE.md", b"template\n")
    result: dict[str, list[str]] = {"warnings": []}
    _remove_stale_client_artifacts(
        tmp_path, result, manifest_hashes={".github/skills/trw-sprint-finish/SKILL.md": _sha(skill)}
    )
    assert (tmp_path / ".github/skills/trw-sprint-finish/SKILL.md").is_file()
    assert (tmp_path / ".github/skills/trw-sprint-finish/TEMPLATE.md").is_file()


def test_a_kept_retired_skill_is_one_line_not_one_per_file_plus_one_per_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.bootstrap._retired_artifacts import _RETIRED_SKILL_WHY
    from trw_mcp.server._update_report import report_kept, report_removed

    skill = b"old skill\n"
    for client in (".agents/skills", ".github/skills"):
        _ship(tmp_path, f"{client}/trw-decision/SKILL.md", skill)
        _ship(tmp_path, f"{client}/trw-decision/my-notes.md", b"mine\n")
    result: dict[str, list[str]] = {"warnings": [], "updated": [], "created": [], "preserved": [], "errors": []}
    _remove_stale_client_artifacts(
        tmp_path,
        result,
        manifest_hashes={
            ".agents/skills/trw-decision/SKILL.md": _sha(skill),
            ".github/skills/trw-decision/SKILL.md": _sha(skill),
        },
    )
    result["warnings"].extend(retired_artifact_notices(tmp_path))  # what enforce_and_write_manifest adds
    result["retired_present"] = [*result.get("retired_kept", [])]
    report_removed(result, detailed=False, quiet=False)
    report_kept(result, tmp_path, detailed=False, quiet=False)
    out = capsys.readouterr().out
    expected = [
        f"retired_artifact_present: {client}/trw-decision is no longer used by TRW ({_RETIRED_SKILL_WHY}); "
        f"it holds 2 files; review them before removing; remove it manually: "
        f"rm -r {(tmp_path.resolve() / client / 'trw-decision')}"
        for client in (".agents/skills", ".github/skills")
    ]
    assert result["warnings"] == expected  # exactly one line per retired skill directory, in this order
    assert out == ""  # and the report prints no second line for it, per file or per directory
    assert result["retired_kept"] == [".agents/skills/trw-decision", ".github/skills/trw-decision"]
    assert not result.get("retired")


def _git_repo(root: Path) -> None:
    for cmd in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "i"],
    ):
        subprocess.run(["git", "-C", str(root), *cmd], check=True)


def test_an_unrecorded_custom_template_committed_in_git_is_kept_and_the_skill_stays_whole(tmp_path: Path) -> None:
    """A file named like a bundled companion is not TRW's unless its bytes are the shipped bytes: a clean git copy of
    the user's own PR-TEMPLATE.md must not make it deletable just because SKILL.md has a manifest record."""
    skill = b"copilot rendering of the skill\n"
    mine = b"# my own PR template\n"
    _ship(tmp_path, ".github/skills/trw-commit/SKILL.md", skill)
    _ship(tmp_path, ".github/skills/trw-commit/PR-TEMPLATE.md", mine)
    _git_repo(tmp_path)
    result: dict[str, list[str]] = {"warnings": []}
    _remove_stale_client_artifacts(
        tmp_path, result, manifest_hashes={".github/skills/trw-commit/SKILL.md": _sha(skill)}
    )
    assert (tmp_path / ".github/skills/trw-commit/PR-TEMPLATE.md").read_bytes() == mine
    assert (tmp_path / ".github/skills/trw-commit/SKILL.md").read_bytes() == skill
    assert not result.get("retired")
    assert result["retired_kept"] == [".github/skills/trw-commit"]
    assert result["warnings"] == []  # the retired-artifact notice, added later, is the one line


def test_a_recorded_edited_file_committed_in_git_is_still_recoverable_from_git(tmp_path: Path) -> None:
    """The git-clean route stays for a file the manifest records (a user edit committed to git)."""
    skill = b"old skill\n"
    _ship(tmp_path, ".agents/skills/trw-decision/SKILL.md", b"edited then committed\n")
    _git_repo(tmp_path)
    result: dict[str, list[str]] = {"warnings": []}
    _remove_stale_client_artifacts(
        tmp_path, result, manifest_hashes={".agents/skills/trw-decision/SKILL.md": _sha(skill)}
    )
    assert not (tmp_path / ".agents/skills/trw-decision").exists()
    assert result["retired"] == [".agents/skills/trw-decision/SKILL.md"]


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()}


def _race(
    monkeypatch: pytest.MonkeyPatch,
    parent: Path,
    *,
    before: Callable[[Path], object] | None = None,
    after: Callable[[Path], object] | None = None,
) -> None:
    """Run *before(<dir>)* just before, and *after(<aside dir>)* just after, the sweep's rename-aside in *parent*."""
    real = os.rename

    def rename(src: Any, dst: Any, **kw: Any) -> None:
        if not str(dst).startswith(".trw-retiring-"):
            real(src, dst, **kw)
            return
        if before:
            before(parent / str(src))
        real(src, dst, **kw)
        if after:
            after(parent / str(dst))

    monkeypatch.setattr(os, "rename", rename)


def _decision(tmp_path: Path, *, client: str = ".agents/skills") -> tuple[Path, dict[str, str], dict[str, bytes]]:
    files = {"SKILL.md": b"old skill\n", "sub/extra.md": b"companion\n"}
    for rel, data in files.items():
        _ship(tmp_path, f"{client}/trw-decision/{rel}", data)
    hashes = {f"{client}/trw-decision/{rel}": _sha(data) for rel, data in files.items()}
    return tmp_path / client / "trw-decision", hashes, files


def _leftovers(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in tmp_path.rglob(".trw-retiring-*")) + (
        [".trw/trash"] if (tmp_path / ".trw/trash").exists() else []
    )


def test_a_proven_skill_directory_is_removed_whole_with_nothing_left_behind(tmp_path: Path) -> None:
    skill_dir, hashes, _files = _decision(tmp_path)
    result: dict[str, list[str]] = {"warnings": []}
    _remove_stale_client_artifacts(tmp_path, result, manifest_hashes=hashes)
    assert not skill_dir.exists()
    assert sorted(result["retired"]) == [
        ".agents/skills/trw-decision/SKILL.md",
        ".agents/skills/trw-decision/sub/extra.md",
    ]
    assert result["warnings"] == [] and _leftovers(tmp_path) == []


def test_a_dry_run_says_would_remove_for_each_file_of_the_skill(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dry run executes against the scratch copy (never the real project) and renders its removals as proposals."""
    from trw_mcp.server._update_report import report_removed

    _skill_dir, hashes, _files = _decision(tmp_path)
    result: dict[str, list[str]] = {"warnings": [], "would_run": []}
    _remove_stale_client_artifacts(tmp_path, result, manifest_hashes=hashes)
    report_removed(result, detailed=False, quiet=False)
    assert capsys.readouterr().out.splitlines() == [
        "Would remove retired TRW file: .agents/skills/trw-decision/SKILL.md",
        "Would remove retired TRW file: .agents/skills/trw-decision/sub/extra.md",
    ]


def test_a_file_added_between_the_proof_and_the_rename_is_kept_and_the_dir_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir, hashes, files = _decision(tmp_path)
    _race(
        monkeypatch, tmp_path / ".agents/skills", before=lambda src: (src / "late.md").write_bytes(b"typed just now\n")
    )
    result: dict[str, list[str]] = {"warnings": []}
    _remove_stale_client_artifacts(tmp_path, result, manifest_hashes=hashes)
    assert _snapshot(skill_dir) == {**files, "late.md": b"typed just now\n"}
    assert not result.get("retired") and _leftovers(tmp_path) == []
    assert result["retired_kept"] == [".agents/skills/trw-decision"]
    assert result["warnings"] == []  # the retired-artifact notice, added later, is the one line


def test_a_file_changed_after_the_rename_is_restored_and_a_rerun_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir, hashes, files = _decision(tmp_path)
    _race(
        monkeypatch,
        tmp_path / ".agents/skills",
        after=lambda aside: (aside / "sub/extra.md").write_bytes(b"edited meanwhile\n"),
    )
    result: dict[str, list[str]] = {"warnings": []}
    _remove_stale_client_artifacts(tmp_path, result, manifest_hashes=hashes)
    assert _snapshot(skill_dir) == {"SKILL.md": files["SKILL.md"], "sub/extra.md": b"edited meanwhile\n"}
    assert not result.get("retired") and _leftovers(tmp_path) == []
    monkeypatch.undo()
    # The edited file is not TRW's bytes any more, so the rerun still keeps the skill whole, with no aliases or trash.
    again: dict[str, list[str]] = {"warnings": []}
    _remove_stale_client_artifacts(tmp_path, again, manifest_hashes=hashes)
    assert _snapshot(skill_dir) == {"SKILL.md": files["SKILL.md"], "sub/extra.md": b"edited meanwhile\n"}
    assert _leftovers(tmp_path) == []
    # Once the file is TRW's again, the same sweep removes the whole directory.
    (skill_dir / "sub/extra.md").write_bytes(files["sub/extra.md"])
    final: dict[str, list[str]] = {"warnings": []}
    _remove_stale_client_artifacts(tmp_path, final, manifest_hashes=hashes)
    assert not skill_dir.exists() and _leftovers(tmp_path) == []


def test_a_name_recreated_before_the_rename_back_leaves_the_sibling_and_names_it_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir, hashes, _files = _decision(tmp_path)

    def interfere(aside: Path) -> None:
        (aside / "SKILL.md").write_bytes(b"edited meanwhile\n")
        skill_dir.mkdir()
        (skill_dir / "new.md").write_bytes(b"the user's new skill\n")

    _race(monkeypatch, tmp_path / ".agents/skills", after=interfere)
    result: dict[str, list[str]] = {"warnings": []}
    _remove_stale_client_artifacts(tmp_path, result, manifest_hashes=hashes)
    (aside,) = [p for p in (tmp_path / ".agents/skills").iterdir() if p.name.startswith(".trw-retiring-trw-decision-")]
    assert (aside / "SKILL.md").read_bytes() == b"edited meanwhile\n"
    assert _snapshot(skill_dir) == {"new.md": b"the user's new skill\n"}
    assert len(result["warnings"]) == 1
    assert result["warnings"][0].startswith(f".agents/skills/trw-decision: kept at .agents/skills/{aside.name} (")
    assert "move it back yourself" in result["warnings"][0] and "kept as it was" not in result["warnings"][0]
    assert "rm -r" not in result["warnings"][0]


def test_a_parent_swapped_for_a_symlink_is_refused_and_nothing_is_touched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir, hashes, files = _decision(tmp_path)
    elsewhere = tmp_path / "elsewhere"

    def swap(src: Path) -> None:
        os.rename(tmp_path / ".agents/skills", elsewhere)
        (tmp_path / ".agents/skills").symlink_to(elsewhere)

    # The swap happens before the walk; _race fires at rename time, so hook the walk through an earlier rename.
    from trw_mcp.bootstrap import _retire_whole as _retire
    from trw_mcp.bootstrap._trash import _walk as real_walk

    def walk(root_fd: int, parts: tuple[str, ...], *, create: bool) -> int:
        swap(skill_dir)
        return real_walk(root_fd, parts, create=create)

    monkeypatch.setattr(_retire, "_walk", walk)
    result: dict[str, list[str]] = {"warnings": []}
    _remove_stale_client_artifacts(tmp_path, result, manifest_hashes=hashes)
    assert _snapshot(elsewhere / "trw-decision") == files
    assert not result.get("retired")
    assert [p.name for p in elsewhere.iterdir()] == ["trw-decision"]


def test_a_skill_outside_the_client_mirrors_kept_whole_is_named_once_by_the_sweep(tmp_path: Path) -> None:
    """``.claude/skills`` has no ``retired_artifact_present`` notice, so the sweep's own line (with ``rm -r``) stands."""
    from trw_mcp.bootstrap._ownership_proof import remove_proven

    skill = tmp_path / ".claude" / "skills" / "trw-gone"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"shipped\n")
    (skill / "mine.md").write_bytes(b"mine\n")
    result: dict[str, list[str]] = {}
    remove_proven(skill, {".claude/skills/trw-gone/SKILL.md": _sha(b"shipped\n")}, tmp_path, result)
    assert (skill / "SKILL.md").is_file() and (skill / "mine.md").is_file()
    assert len(result["warnings"]) == 1 and "rm -r .claude/skills/trw-gone" in result["warnings"][0]


# ── a crash after the rename-aside, and a mount inside the skill ──────────────────────────────────────────


def _interrupted_line(tmp_path: Path, rel: str) -> str:
    path = Path(rel)
    original = path.with_name(path.name.removeprefix(".trw-retiring-").rsplit("-", 1)[0])
    return (
        f"retired_artifact_present: interrupted retirement left {rel}; "
        f"it may hold your changes; review it and restore the remaining files to "
        f"{tmp_path.resolve() / original} without overwriting existing files"
    )


@pytest.mark.parametrize(
    "survivors",
    [{"SKILL.md": b"old skill\n", "sub/extra.md": b"companion\n"}, {"sub/extra.md": b"companion\n"}],
    ids=["restart_right_after_the_rename", "restart_after_a_partial_deletion"],
)
@pytest.mark.parametrize("client", [".agents/skills", ".github/skills", ".opencode/skills"])
def test_an_interrupted_retirement_is_reported_once_and_never_deleted(
    tmp_path: Path, client: str, survivors: dict[str, bytes]
) -> None:
    for rel, data in survivors.items():
        _ship(tmp_path, f"{client}/.trw-retiring-trw-decision-0123abcd/{rel}", data)
    rel = f"{client}/.trw-retiring-trw-decision-0123abcd"
    assert retired_artifact_notices(tmp_path) == [_interrupted_line(tmp_path, rel)]
    assert _snapshot(tmp_path / rel) == survivors  # reporting never touches it


def test_an_interrupted_retirement_under_claude_skills_is_reported_and_is_not_a_custom_skill(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._artifact_names import _get_custom_names

    _ship(tmp_path, ".claude/skills/.trw-retiring-trw-x-0123abcd/SKILL.md", b"x\n")
    _ship(tmp_path, ".claude/skills/my-own/SKILL.md", b"mine\n")
    assert _get_custom_names(tmp_path)["skills"] == ["my-own"]
    assert retired_artifact_notices(tmp_path) == [
        _interrupted_line(tmp_path, ".claude/skills/.trw-retiring-trw-x-0123abcd")
    ]


def test_a_skill_with_an_entry_on_another_device_is_kept_whole_and_reported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _retire_whole

    skill_dir, hashes, files = _decision(tmp_path)
    mounted = (skill_dir / "sub").stat().st_ino
    real = _retire_whole._device
    monkeypatch.setattr(_retire_whole, "_device", lambda st: -1 if st.st_ino == mounted else real(st))
    result: dict[str, list[str]] = {"warnings": []}
    _remove_stale_client_artifacts(tmp_path, result, manifest_hashes=hashes)
    assert _snapshot(skill_dir) == files and _leftovers(tmp_path) == []
    assert not result.get("retired")
    assert result["retired_kept"] == [".agents/skills/trw-decision"]
    assert result["warnings"] == []  # the retired-artifact notice, added later, is the one line
