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
    assert not result.removed and not result.git
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
