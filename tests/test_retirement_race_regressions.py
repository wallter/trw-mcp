"""Retirement preserves post-scan writes and gives proof-gated recovery advice."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap import _retire_whole
from trw_mcp.bootstrap._retire import retire_tree
from trw_mcp.bootstrap._retired_artifacts import retired_artifact_notices, retired_artifact_row
from trw_mcp.bootstrap._version_manifest import MANIFEST_VERSION


@pytest.mark.parametrize("change", ["added", "edited", "replaced", "symlink"])
@pytest.mark.parametrize("occupied", [False, True])
@pytest.mark.parametrize("directory", ["", "sub"])
def test_post_scan_writes_survive_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str, occupied: bool, directory: str
) -> None:
    skill = tmp_path / ".agents/skills/trw-gone"
    (skill / directory).mkdir(parents=True)
    original = b"TRW's bytes\n"
    mine = b"user bytes added after the verified scan\x00\xff\n"
    (skill / directory / "SKILL.md").write_bytes(original)
    outside = tmp_path / "user.md"
    outside.write_bytes(mine)
    scan = _retire_whole._scan
    injected = False
    held_fd = os.open(skill / directory, os.O_RDONLY | os.O_DIRECTORY)

    def scan_then_write(dir_fd: int, dev: int, prefix: str = "") -> dict[str, str]:
        nonlocal injected
        found = scan(dir_fd, dev, prefix)
        if not prefix:
            injected = True
            if occupied:
                skill.mkdir()
                (skill / "new.md").write_bytes(b"new occupant")
            if change in {"replaced", "symlink"}:
                os.unlink("SKILL.md", dir_fd=held_fd)
            if change == "symlink":
                os.symlink(outside, "SKILL.md", dir_fd=held_fd)
            else:
                name = "late.md" if change == "added" else "SKILL.md"
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600, dir_fd=held_fd)
                try:
                    os.write(fd, mine)
                finally:
                    os.close(fd)
        return found

    monkeypatch.setattr(_retire_whole, "_scan", scan_then_write)
    try:
        result = retire_tree(skill, tmp_path, lambda _: {hashlib.sha256(original).hexdigest()}, whole=True)
    finally:
        os.close(held_fd)
    assert injected
    if occupied:
        (survivor,) = skill.parent.glob(".trw-retiring-*")
        assert (skill / "new.md").read_bytes() == b"new occupant"
    else:
        survivor = skill
        assert not list(skill.parent.glob(".trw-retiring-*"))
    entry = survivor / directory / ("late.md" if change == "added" else "SKILL.md")
    assert entry.read_bytes() == mine
    assert entry.is_symlink() == (change == "symlink")
    assert outside.read_bytes() == mine
    expected_removed = (
        [f".agents/skills/trw-gone/{directory + '/' if directory else ''}SKILL.md"] if change == "added" else []
    )
    assert result.removed == expected_removed and not result.git
    assert len(result.kept) == 1
    assert "files were added or changed during retirement" in result.kept[0][1]
    assert str(survivor.relative_to(tmp_path)) in result.kept[0][1]


@pytest.mark.parametrize("state", ["proven", "changed", "added", "symlink", "unreadable"])
def test_leftover_advice_and_doctor_require_ownership_of_every_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    rel = ".agents/skills/.trw-retiring-trw-gone-0123abcd"
    original_rel = ".agents/skills/trw-gone"
    leftover = tmp_path / rel
    (leftover / "sub").mkdir(parents=True)
    baseline = {"SKILL.md": b"old skill\n", "sub/notes.md": b"old companion\n"}
    for name, content in baseline.items():
        (leftover / name).write_bytes(content)
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw/managed-artifacts.yaml").write_text(
        yaml.safe_dump(
            {
                "version": MANIFEST_VERSION,
                "content_hashes": {
                    f"{original_rel}/{name}": hashlib.sha256(content).hexdigest() for name, content in baseline.items()
                },
            }
        )
    )
    if state == "changed":
        (leftover / "sub/notes.md").write_bytes(b"my changed notes\n")
    elif state == "added":
        (leftover / "mine.md").write_bytes(b"my notes\n")
    elif state == "symlink":
        (leftover / "sub/notes.md").unlink()
        (leftover / "sub/notes.md").symlink_to(leftover / "SKILL.md")
    elif state == "unreadable":

        def unreadable(dir_fd: int, dev: int, prefix: str = "") -> dict[str, str]:
            raise PermissionError("cannot read leftover")

        monkeypatch.setattr(_retire_whole, "_scan", unreadable)
    before = {p.relative_to(leftover): p.read_bytes() for p in leftover.rglob("*") if p.is_file()}
    (notice,) = retired_artifact_notices(tmp_path)
    status, doctor = retired_artifact_row(tmp_path)
    assert status == "WARN"
    assert notice == f"retired_artifact_present: {doctor}"
    if state != "proven":
        assert "may hold your changes" in notice
        assert str(tmp_path / original_rel) in notice
        assert "rm " not in notice and "rmdir" not in notice
    else:
        assert f"rm -r {leftover}" in notice
    assert {p.relative_to(leftover): p.read_bytes() for p in leftover.rglob("*") if p.is_file()} == before


def test_partial_whole_skill_retirement_reports_unlinked_files_and_directory_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap._retire import record_retirement

    skill = tmp_path / ".agents/skills/trw-gone"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"owned\n")
    digest = hashlib.sha256(b"owned\n").hexdigest()
    real_rmdir = os.rmdir
    injected = False

    def add_late_file(name: str | bytes, *args: object, **kwargs: object) -> None:
        nonlocal injected
        if str(name).startswith(".trw-retiring-") and not injected:
            injected = True
            (skill.parent / str(name) / "late.md").write_bytes(b"user\n")
        real_rmdir(name, *args, **kwargs)

    monkeypatch.setattr(os, "rmdir", add_late_file)
    outcome = _retire_whole.retire_whole(
        skill,
        tmp_path,
        [skill / "SKILL.md"],
        lambda _path: {digest},
        lambda _path: set(),
        lambda path: path.relative_to(tmp_path).as_posix(),
    )
    result: dict[str, list[str]] = {"warnings": []}
    record_retirement(result, outcome)

    assert injected
    assert outcome.removed == [".agents/skills/trw-gone/SKILL.md"]
    assert outcome.kept_dirs == frozenset({".agents/skills/trw-gone"})
    assert result["retired"] == [".agents/skills/trw-gone/SKILL.md"]
    assert any("rm -r .agents/skills/trw-gone" in warning for warning in result["warnings"])
    assert (skill / "late.md").read_bytes() == b"user\n"


def test_stopped_retirement_reports_files_removed_when_original_name_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = tmp_path / ".agents/skills/trw-gone"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"owned\n")
    digest = hashlib.sha256(b"owned\n").hexdigest()
    remove = _retire_whole._remove_verified

    def remove_then_name_taken(dir_fd: int, found: dict[str, str], removed: list[str]) -> None:
        remove(dir_fd, found, removed)
        skill.mkdir()
        raise OSError("concurrent stop")

    monkeypatch.setattr(_retire_whole, "_remove_verified", remove_then_name_taken)
    outcome = _retire_whole.retire_whole(
        skill,
        tmp_path,
        [skill / "SKILL.md"],
        lambda _: {digest},
        lambda _: set(),
        lambda path: path.relative_to(tmp_path).as_posix(),
    )

    assert outcome.removed == [".agents/skills/trw-gone/SKILL.md"]
    assert "already removed" in outcome.kept[0][1]
