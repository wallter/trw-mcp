"""PRD-CORE-337 FR06/FR07 -- ``trw_mcp._checkout_write``, the adapter every bootstrap and channel writer calls.

One test per arm of each function (charter Q7(25)): the safe write, the refusal. The write arms also pin
what "unchanged output" means for the migrated sites: the bytes ``Path.write_text(text, encoding="utf-8")``
wrote, and the permission bits an in-place ``write_text`` kept.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from trw_mcp._checkout_write import UnsafeWriteError, append_checkout_file, write_checkout_file

_TEXT = "first line\nsecond: üß ✓\n"


def _project_with_outside_sentinel(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"do not touch\n")
    return project, sentinel


def test_write_checkout_file_writes_what_write_text_wrote_and_creates_parents(tmp_path: Path) -> None:
    reference = tmp_path / "reference.txt"
    reference.write_text(_TEXT, encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()

    write_checkout_file(project, project / ".cursor" / "rules" / "x.mdc", _TEXT)
    write_checkout_file(project, project / "raw.bin", b"\x00\xffbytes")

    assert (project / ".cursor" / "rules" / "x.mdc").read_bytes() == reference.read_bytes()
    assert (project / "raw.bin").read_bytes() == b"\x00\xffbytes"


def test_write_checkout_file_keeps_an_existing_files_mode_and_creates_new_ones_like_open(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    private = project / ".mcp.json"
    private.write_text("{}", encoding="utf-8")
    private.chmod(0o600)
    umask = os.umask(0)
    os.umask(umask)

    write_checkout_file(project, private, '{"mcpServers": {}}\n')
    write_checkout_file(project, project / "new.json", "{}\n")

    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert private.read_text(encoding="utf-8") == '{"mcpServers": {}}\n'
    assert stat.S_IMODE((project / "new.json").stat().st_mode) == 0o666 & ~umask


def test_write_checkout_file_refuses_a_symlinked_leaf(tmp_path: Path) -> None:
    project, sentinel = _project_with_outside_sentinel(tmp_path)
    (project / ".cursor").mkdir()
    (project / ".cursor" / "hooks.json").symlink_to(sentinel)

    with pytest.raises(UnsafeWriteError) as refused:
        write_checkout_file(project, project / ".cursor" / "hooks.json", "{}\n")

    assert refused.value.reason == "symlink_leaf"
    assert sentinel.read_bytes() == b"do not touch\n"
    assert (project / ".cursor" / "hooks.json").is_symlink()


def test_write_checkout_file_refuses_a_symlinked_parent(tmp_path: Path) -> None:
    project, sentinel = _project_with_outside_sentinel(tmp_path)
    (project / ".codex").symlink_to(sentinel.parent, target_is_directory=True)

    with pytest.raises(UnsafeWriteError) as refused:
        write_checkout_file(project, project / ".codex" / "config.toml", "x = 1\n")

    assert refused.value.reason == "symlink_component"
    assert sorted(p.name for p in sentinel.parent.iterdir()) == ["sentinel.txt"]


def test_write_checkout_file_rejects_a_path_outside_its_root(tmp_path: Path) -> None:
    project, sentinel = _project_with_outside_sentinel(tmp_path)

    with pytest.raises(ValueError, match="is not in the subpath"):
        write_checkout_file(project, sentinel, "x")

    assert sentinel.read_bytes() == b"do not touch\n"


def test_append_checkout_file_appends_and_creates(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    log = project / ".trw" / "telemetry" / "events.jsonl"

    append_checkout_file(project, log, '{"n": 1}\n')
    append_checkout_file(project, log, b'{"n": 2}\n')

    assert log.read_bytes() == b'{"n": 1}\n{"n": 2}\n'


def test_append_checkout_file_refuses_a_symlinked_leaf(tmp_path: Path) -> None:
    project, sentinel = _project_with_outside_sentinel(tmp_path)
    (project / "events.jsonl").symlink_to(sentinel)

    with pytest.raises(UnsafeWriteError) as refused:
        append_checkout_file(project, project / "events.jsonl", "appended\n")

    assert refused.value.reason == "symlink_leaf"
    assert sentinel.read_bytes() == b"do not touch\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.parametrize("existing_mode", [0o600, 0o640, 0o400])
def test_write_checkout_file_keeps_the_mode_of_a_nested_file(tmp_path: Path, existing_mode: int) -> None:
    """The kept bits are read through the directory walk below *root*, not only at *root* itself (PRD-CORE-337 FR08)."""
    project = tmp_path / "project"
    target = project / ".trw" / "reports" / "export.json"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8")
    target.chmod(existing_mode)

    write_checkout_file(project, target, "new\n")

    assert target.read_bytes() == b"new\n"
    assert stat.S_IMODE(target.stat().st_mode) == existing_mode


@pytest.mark.parametrize("linesep", ["\r\n", "\n"])
def test_text_line_ends_follow_os_linesep_like_text_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linesep: str
) -> None:
    """``Path.write_text``/``open('a')`` write each ``\\n`` as ``os.linesep``; both calls must too (PRD-CORE-337 FR08)."""
    monkeypatch.setattr(os, "linesep", linesep)

    write_checkout_file(tmp_path, tmp_path / "a.txt", "one\ntwo\n")
    append_checkout_file(tmp_path, tmp_path / "b.jsonl", "x\n")
    append_checkout_file(tmp_path, tmp_path / "b.jsonl", "y\n")

    end = linesep.encode("ascii")
    assert (tmp_path / "a.txt").read_bytes() == b"one" + end + b"two" + end
    assert (tmp_path / "b.jsonl").read_bytes() == b"x" + end + b"y" + end
