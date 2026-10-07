"""Uninstall never deletes a project's CLAUDE.md or AGENTS.md (operator P0, 2026-10-01).

It takes out only TRW's marked blocks. A file left empty (it held only TRW's block) stays in place, empty, and is
reported. The file is captured into ``.trw/trash`` (the one part of ``.trw`` a whole-project uninstall keeps) by
rename and re-proven before the rewritten bytes are created at its name, so the previous version is the displaced
file itself and a save made while uninstall runs is never replaced (codex r1).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]

_HEADER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"
_BLOCK = f"{_HEADER}\n<!-- trw:start -->\n\nTRW context.\n@.trw/INSTRUCTIONS.md\n<!-- trw:end -->\n"


def _installed(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / ".git").mkdir()
    result = init_project(repo, ide="claude-code")
    assert not result["errors"], result["errors"]
    return repo


def _uninstall(repo: Path, capsys: pytest.CaptureFixture[str]) -> str:
    _run_uninstall(argparse.Namespace(target_dir=str(repo), dry_run=False, yes=True))
    return capsys.readouterr().out


def _uninstall_refused(repo: Path, capsys: pytest.CaptureFixture[str]) -> str:
    """An uninstall that left an item as found reports it and exits non-zero."""
    with pytest.raises(SystemExit) as exited:
        _run_uninstall(argparse.Namespace(target_dir=str(repo), dry_run=False, yes=True))
    assert exited.value.code == 1
    return capsys.readouterr().out


def _named_previous(out: str, repo: Path) -> dict[str, bytes]:
    """The summary's "Previous X kept at Y" lines, as {file name: bytes at Y}."""
    named = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Previous ") and " kept at " in line:
            name, _, rest = line[len("Previous ") :].partition(" kept at ")
            named[name] = (repo / rest.split("; delete it when satisfied.")[0]).read_bytes()
    return named


def _trash_copies(repo: Path) -> list[bytes]:
    return [p.read_bytes() for p in (repo / ".trw" / "trash").glob("*/data")]


@pytest.mark.parametrize(
    "content",
    [
        _BLOCK,
        "\n" + _BLOCK + "\n",
        "# CLAUDE.md\n\n## What This Is\n\n{Describe your project here}\n\n" + _BLOCK,
        "@AGENTS.md\n\n" + _BLOCK,
        "",
    ],
    ids=["block-only", "block-and-blank-lines", "pre-8-scaffold", "pointer", "empty"],
)
def test_uninstall_never_deletes_claude_md(tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str) -> None:
    repo = _installed(tmp_path)
    claude = repo / "CLAUDE.md"
    claude.write_text(content, encoding="utf-8")

    out = _uninstall(repo, capsys)

    assert claude.is_file(), out
    left = claude.read_text(encoding="utf-8")
    assert "trw:start" not in left and "TRW context." not in left, "only TRW's block goes"
    if "@AGENTS.md" in content:
        assert left.startswith("@AGENTS.md\n"), "the pointer is the user's line"
    if "{Describe your project here}" in content:
        assert "{Describe your project here}" in left, "a scaffold TRW wrote long ago is the user's file now"
    assert "Removed: " + str(claude) not in out


def test_a_claude_md_left_empty_is_kept_and_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _installed(tmp_path)
    (repo / "CLAUDE.md").write_text(_BLOCK, encoding="utf-8")

    out = _uninstall(repo, capsys)

    assert (repo / "CLAUDE.md").read_text(encoding="utf-8").strip() == ""
    assert "CLAUDE.md" in out and "empty" in out.lower(), out


def test_a_claude_md_with_user_text_keeps_it_and_a_copy_survives_uninstall(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _installed(tmp_path)
    before = "# My rules\n\nUse tabs.\n\n" + _BLOCK + "\n## More\n\nNo force pushes.\n"
    (repo / "CLAUDE.md").write_text(before, encoding="utf-8")

    out = _uninstall(repo, capsys)

    named = _named_previous(out, repo)
    assert named["CLAUDE.md"] == before.encode("utf-8"), "named, actionable"
    assert out.count("delete it when satisfied") == len(named), "each kept version named once"

    left = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert left.startswith("# My rules\n\nUse tabs.\n") and left.rstrip().endswith("No force pushes.")
    assert "trw:start" not in left
    assert before.encode("utf-8") in _trash_copies(repo), "the previous bytes are recoverable from .trw/trash"


def test_a_claude_md_without_trw_markers_is_untouched(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _installed(tmp_path)
    mine = b"# Mine\r\n\r\nNothing of TRW's here.\r\n"
    (repo / "CLAUDE.md").write_bytes(mine)

    _uninstall(repo, capsys)

    assert (repo / "CLAUDE.md").read_bytes() == mine


def test_uninstall_never_deletes_agents_md_even_when_only_trws_block_is_left(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """init-project created AGENTS.md holding only TRW's block: uninstall empties it and keeps the file."""
    repo = _installed(tmp_path)
    agents = repo / "AGENTS.md"
    assert "trw:start" in agents.read_text(encoding="utf-8")

    out = _uninstall(repo, capsys)

    assert agents.is_file(), out
    assert agents.read_text(encoding="utf-8").strip() == ""
    assert "AGENTS.md" in out and "empty" in out.lower(), out


def test_an_agents_md_with_user_text_keeps_it_and_a_copy_survives_uninstall(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _installed(tmp_path)
    agents = repo / "AGENTS.md"
    before = "# Team\n\nAgents-only rule.\n\n" + agents.read_text(encoding="utf-8")
    agents.write_text(before, encoding="utf-8")

    _uninstall(repo, capsys)

    left = agents.read_text(encoding="utf-8")
    assert left.startswith("# Team\n\nAgents-only rule.\n") and "trw:start" not in left
    assert before.encode("utf-8") in _trash_copies(repo)


def test_both_files_diverged_each_keep_their_own_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _installed(tmp_path)
    (repo / "CLAUDE.md").write_text("# For Claude\n\nClaude-only.\n\n" + _BLOCK, encoding="utf-8")
    agents = repo / "AGENTS.md"
    agents.write_text("# For agents\n\nAgents-only.\n\n" + agents.read_text(encoding="utf-8"), encoding="utf-8")

    _uninstall(repo, capsys)

    claude = (repo / "CLAUDE.md").read_text(encoding="utf-8")
    left = agents.read_text(encoding="utf-8")
    assert claude.startswith("# For Claude\n\nClaude-only.\n") and "Agents-only" not in claude
    assert left.startswith("# For agents\n\nAgents-only.\n") and "Claude-only" not in left


def test_the_previous_version_is_the_displaced_file_not_a_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Git-tracked or not, nothing is copied: the very inode that held the user's file is kept in .trw/trash."""
    import os
    import subprocess

    repo = _installed(tmp_path)
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.hooksPath=/dev/null"]
    subprocess.run([*git, "init", "-q"], check=True)
    claude = repo / "CLAUDE.md"
    before = "# My rules\n\nUse tabs.\n\n" + _BLOCK
    claude.write_text(before, encoding="utf-8")
    subprocess.run([*git, "add", "CLAUDE.md"], check=True)
    subprocess.run([*git, "commit", "-qm", "rules"], check=True)
    inode = os.stat(claude).st_ino

    _uninstall(repo, capsys)

    kept = [p for p in (repo / ".trw" / "trash").glob("*/data") if p.read_bytes() == before.encode("utf-8")]
    assert [os.stat(p).st_ino for p in kept] == [inode], "the displaced file itself, moved by rename"
    assert os.stat(claude).st_ino != inode and "trw:start" not in claude.read_text(encoding="utf-8")


def test_a_claude_md_keeps_its_permission_bits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _installed(tmp_path)
    claude = repo / "CLAUDE.md"
    claude.write_text("# Mine\n\n" + _BLOCK, encoding="utf-8")
    claude.chmod(0o600)

    _uninstall(repo, capsys)

    assert claude.stat().st_mode & 0o777 == 0o600
    assert claude.read_text(encoding="utf-8").startswith("# Mine\n")


def _save_while_writing(monkeypatch: pytest.MonkeyPatch, target: Path, save: bytes) -> list[bool]:
    """Land *save* at *target* once TRW has judged the file and is writing its replacement.

    Fires on whichever comes first: an exclusive create of a temp named like *target* elsewhere, an ``os.replace``
    onto *target* (a temp-then-replace write), or the ``link`` that publishes the staged file at *target*'s name.
    """
    import os

    fired: list[bool] = []
    real_open, real_replace, real_link = os.open, os.replace, os.link

    def land() -> None:
        if not fired:
            fired.append(True)
            fd = real_open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            os.write(fd, save)
            os.close(fd)

    def open_(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
        p = Path(os.fsdecode(path)) if isinstance(path, (str, bytes, os.PathLike)) else None
        if p is not None and flags & os.O_EXCL and p.name == target.name and p != target:
            land()
        return real_open(path, flags, *args, **kwargs)

    def replace(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
        if Path(os.fsdecode(dst)) == target:
            land()
        return real_replace(src, dst, *args, **kwargs)

    def link(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
        if os.fsdecode(dst) == target.name and kwargs.get("dst_dir_fd") is not None:
            land()
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "link", link)
    return fired


@pytest.mark.parametrize(
    "content",
    ["# Mine\n\nUse tabs.\n\n" + _BLOCK, _BLOCK],
    ids=["user-text", "block-only"],
)
def test_a_save_made_while_uninstall_rewrites_the_file_is_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    """Codex r1 block: the compare-then-replace replaced a save landing between the compare and the replace."""
    repo = _installed(tmp_path)
    claude = repo / "CLAUDE.md"
    claude.write_text(content, encoding="utf-8")
    save = b"# Saved while uninstall ran\n"
    fired = _save_while_writing(monkeypatch, claude.resolve(), save)

    out = _uninstall_refused(repo, capsys)

    assert fired, "the save was injected"
    assert claude.read_bytes() == save, out
    assert _named_previous(out, repo)["CLAUDE.md"] == content.encode("utf-8"), "the judged version, named"


def test_a_failed_write_puts_the_previous_file_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno
    import os

    repo = _installed(tmp_path)
    claude = repo / "CLAUDE.md"
    before = "# Mine\n\n" + _BLOCK
    claude.write_text(before, encoding="utf-8")
    real_link = os.link

    def full_disk(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
        if os.fsdecode(src) == "new" and os.fsdecode(dst) == "CLAUDE.md":  # the publishing link only
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", full_disk)

    out = _uninstall_refused(repo, capsys)

    assert claude.read_text(encoding="utf-8") == before, out
    assert "previous version was put back" in out


def test_a_claude_md_edited_before_the_capture_is_left_as_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _trash

    repo = _installed(tmp_path)
    claude = repo / "CLAUDE.md"
    claude.write_text("# Mine\n\n" + _BLOCK, encoding="utf-8")
    edited = b"# Edited after uninstall read it\n"
    real = _trash.remove_if_hash

    def edit_first(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path.name == "CLAUDE.md":
            path.write_bytes(edited)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(_trash, "remove_if_hash", edit_first)

    out = _uninstall_refused(repo, capsys)

    assert claude.read_bytes() == edited, out
    assert "Error updating CLAUDE.md: kept: left as found" in out, "the user's bytes are named at their own path"


def test_a_platform_without_the_safe_capture_leaves_the_file_as_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Native Windows lacks the fd-anchored rename capture (codex r1 KI): a refusal, never a raw rewrite."""
    from trw_mcp.bootstrap import _trash

    repo = _installed(tmp_path)
    claude = repo / "CLAUDE.md"
    before = b"# Mine\n\n" + _BLOCK.encode("utf-8")
    claude.write_bytes(before)
    monkeypatch.setattr(_trash, "_UNSUPPORTED", "platform lacks O_DIRECTORY/O_NOFOLLOW/O_NONBLOCK")

    out = _uninstall_refused(repo, capsys)

    assert claude.read_bytes() == before, out
    assert "Error updating CLAUDE.md: kept: left as found (platform lacks" in out


def test_a_rewrite_that_changes_nothing_takes_no_capture(tmp_path: Path) -> None:
    """Lead condition 1: no bytes change, so nothing is moved and .trw/trash is not created."""
    from trw_mcp.bootstrap._proven_replace import replace_proven

    (tmp_path / "CLAUDE.md").write_bytes(b"# Mine\n")
    outcome = replace_proven(tmp_path / "CLAUDE.md", tmp_path, b"# Mine\n", b"# Mine\n")

    assert outcome.status == "replaced" and outcome.previous is None
    assert not (tmp_path / ".trw").exists()


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_a_file_that_held_only_trws_block_leaves_no_capture_behind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    """Lead: TRW-only bytes go with TRW's other files (system Trash, or the hash-proven purge), never named clutter.

    TRW-only means the block TRW writes today, byte for byte: a block the user edited inside is kept (S1 red team B4).
    """
    import sys

    from trw_mcp.state.claude_md._instructions_link import claude_md_link_section

    repo = _installed(tmp_path)
    claude = repo / "CLAUDE.md"
    trw_only = "\n" + claude_md_link_section() + "\n"
    claude.write_text(trw_only, encoding="utf-8")
    agents_before = (repo / "AGENTS.md").read_bytes()
    monkeypatch.setattr(sys, "platform", platform)
    if platform == "darwin":  # every macOS home has ~/.Trash; the isolated home makes one only on a real Mac
        (Path.home() / ".Trash").mkdir(exist_ok=True)

    out = _uninstall(repo, capsys)

    assert claude.is_file() and (repo / "AGENTS.md").is_file(), out
    assert "delete it when satisfied" not in out, "nothing of the user's was kept, so nothing is named"
    left = _trash_copies(repo) if (repo / ".trw" / "trash").is_dir() else []
    assert trw_only.encode("utf-8") not in left and agents_before not in left, out


def test_a_block_the_user_edited_inside_keeps_its_named_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S1 red team B4: the text inside the markers is not what TRW wrote, so its previous version is kept, named."""
    repo = _installed(tmp_path)
    before = "\n" + _BLOCK + "\n"  # "TRW context." is not a block TRW writes
    (repo / "CLAUDE.md").write_text(before, encoding="utf-8")

    out = _uninstall(repo, capsys)

    assert _named_previous(out, repo)["CLAUDE.md"] == before.encode("utf-8")


def test_one_user_byte_outside_the_block_keeps_the_named_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A single character of the user's outside TRW's block: the previous version stays in .trw/trash, named."""
    repo = _installed(tmp_path)
    before = "x\n" + _BLOCK
    (repo / "CLAUDE.md").write_text(before, encoding="utf-8")

    out = _uninstall(repo, capsys)

    assert (repo / "CLAUDE.md").read_text(encoding="utf-8").strip() == "x"
    assert _named_previous(out, repo)["CLAUDE.md"] == before.encode("utf-8")
    assert before.encode("utf-8") in _trash_copies(repo)


def test_a_save_made_in_place_the_moment_the_new_file_appears_is_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r2 block: creating the name and then filling it let TRW's fill overwrite an in-place save.

    The user saves in place at the first instant TRW's new file is visible at the name. Their save must win.
    """
    import os

    repo = _installed(tmp_path)
    claude = repo / "CLAUDE.md"
    claude.write_text("# Mine\n\n" + _BLOCK, encoding="utf-8")
    target = claude.resolve()
    save = b"# Saved in place\n"
    real_open, real_link = os.open, os.link
    fired: list[bool] = []

    def save_in_place() -> None:
        if not fired:
            fired.append(True)
            fd = real_open(target, os.O_WRONLY | os.O_TRUNC)
            os.write(fd, save)
            os.close(fd)

    def open_(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
        fd = real_open(path, flags, *args, **kwargs)
        if isinstance(path, (str, os.PathLike)) and Path(os.fsdecode(path)) == target and flags & os.O_EXCL:
            save_in_place()  # the name now exists, created by TRW
        return fd

    def link(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
        real_link(src, dst, *args, **kwargs)
        if os.fsdecode(dst) == "CLAUDE.md" and kwargs.get("dst_dir_fd") is not None:
            save_in_place()  # the name now exists, linked by TRW

    monkeypatch.setattr(os, "open", open_)
    monkeypatch.setattr(os, "link", link)

    _uninstall(repo, capsys)

    assert fired, "the save was injected"
    assert claude.read_bytes() == save


def test_a_filesystem_without_hard_links_leaves_the_file_as_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """exFAT, FAT and SMB have no hard links, so there is no one-step swap: nothing is moved or rewritten."""
    import errno
    import os

    repo = _installed(tmp_path)
    claude = repo / "CLAUDE.md"
    before = b"# Mine\n\n" + _BLOCK.encode("utf-8")
    claude.write_bytes(before)
    real_link = os.link

    def no_links(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
        if os.fsdecode(dst) == "probe":
            raise OSError(errno.EPERM, "Operation not permitted")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", no_links)

    out = _uninstall_refused(repo, capsys)

    assert claude.read_bytes() == before, out
    assert "has no hard links" in out
    assert before not in (_trash_copies(repo) if (repo / ".trw" / "trash").is_dir() else [])
