"""A user's root CLAUDE.md keeps its content and gains TRW's marked block (operator P0, 2026-10-01).

Claude Code skips ``AGENTS.md`` while a ``CLAUDE.md`` exists, so a project with its own ``CLAUDE.md`` never saw
TRW's context, and releases 8.0.0-8.1.5 deleted a ``CLAUDE.md`` they judged "TRW-only". Now ``init-project``,
``update-project`` and an upgrade add (or refresh) one small marked block in an EXISTING ``CLAUDE.md``: it imports
``.trw/INSTRUCTIONS.md`` and names ``AGENTS.md`` in plain text, never importing it (the two files may differ).
Everything outside TRW's markers stays byte-for-byte; no ``CLAUDE.md`` is ever created; nothing is synced between
the two files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]

_START = "<!-- trw:start -->"
_END = "<!-- trw:end -->"
_HEADER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"
_IMPORT = "@.trw/INSTRUCTIONS.md"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


def _init(repo: Path) -> dict[str, list[str]]:
    result = init_project(repo, ide="claude-code")
    assert not result["errors"], result["errors"]
    return result


def _update(repo: Path) -> dict[str, list[str]]:
    result = update_project(repo)
    assert not result["errors"], result["errors"]
    return result


def _block(text: str) -> str:
    return text.split(_START, 1)[1].split(_END, 1)[0]


class TestNoClaudeMd:
    def test_init_and_update_never_create_a_claude_md(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _init(repo)
        _update(repo)

        assert not (repo / "CLAUDE.md").exists(), "Claude Code reads AGENTS.md natively when there is no CLAUDE.md"
        assert _IMPORT in (repo / "AGENTS.md").read_text(encoding="utf-8")


class TestExistingClaudeMd:
    def test_init_adds_one_block_and_keeps_every_user_byte(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        mine = "# My rules\n\nUse tabs.\nNever push to main.\n"
        (repo / "CLAUDE.md").write_text(mine, encoding="utf-8")

        _init(repo)

        text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
        assert text.startswith(mine), "the user's bytes come first, unchanged"
        assert text.count(_START) == 1 and text.count(_END) == 1
        assert _IMPORT in _block(text)
        assert "AGENTS.md" in _block(text) and "@AGENTS.md" not in text, "named, never imported"

    def test_update_adds_the_block_to_a_claude_md_an_older_install_left_alone(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _init(repo)
        mine = "# Project notes\n\nRun make check before committing.\n"
        (repo / "CLAUDE.md").write_text(mine, encoding="utf-8")

        result = _update(repo)

        text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
        assert text.startswith(mine)
        assert _IMPORT in _block(text)
        assert "CLAUDE.md" in result["updated"]
        assert any("added" in line for line in result["claude_md"]), "the edit is named in the output"

    def test_a_second_update_changes_nothing(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / "CLAUDE.md").write_text("# Mine\n", encoding="utf-8")
        _init(repo)
        once = (repo / "CLAUDE.md").read_bytes()

        result = _update(repo)

        assert (repo / "CLAUDE.md").read_bytes() == once
        assert "CLAUDE.md" not in result["updated"]

    def test_a_shim_that_imports_agents_md_is_left_untouched(self, tmp_path: Path) -> None:
        """9.0.1 regression: ``@AGENTS.md`` already loads TRW's import, so a block there only repeats it."""
        repo = _repo(tmp_path)
        _init(repo)
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")

        result = _update(repo)

        assert (repo / "CLAUDE.md").read_bytes() == b"@AGENTS.md\n"
        assert "CLAUDE.md" not in result["updated"]
        assert not result.get("claude_md")

    def test_a_shim_reaching_agents_md_through_another_file_is_left_untouched(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _init(repo)
        (repo / "notes").mkdir()
        (repo / "notes" / "rules.md").write_text("Rules.\n@../AGENTS.md\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("# Mine\n\n@notes/rules.md\n", encoding="utf-8")

        _update(repo)

        assert (repo / "CLAUDE.md").read_text(encoding="utf-8") == "# Mine\n\n@notes/rules.md\n"

    def test_a_shim_given_a_block_by_9_0_1_loses_only_that_block_and_says_so(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md._instructions_link import claude_md_link_section

        repo = _repo(tmp_path)
        _init(repo)
        (repo / "CLAUDE.md").write_text(f"@AGENTS.md\n\n{claude_md_link_section()}", encoding="utf-8")

        result = _update(repo)

        assert (repo / "CLAUDE.md").read_bytes() == b"@AGENTS.md\n", "the shim the user wrote, restored"
        assert "CLAUDE.md" in result["updated"]
        assert any("removed" in line and "AGENTS.md" in line for line in result["claude_md"]), result

    def test_an_import_inside_a_fence_does_not_count(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _init(repo)
        (repo / "CLAUDE.md").write_text("# Mine\n\n```\n@AGENTS.md\n```\n", encoding="utf-8")

        _update(repo)

        assert _IMPORT in _block((repo / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_a_shim_whose_agents_md_lacks_trws_import_still_gains_the_block(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._template_claude_md import link_claude_md

        repo = _repo(tmp_path)
        (repo / "AGENTS.md").write_text("# Agents, no TRW block\n", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}

        link_claude_md(repo, result)

        assert _IMPORT in _block((repo / "CLAUDE.md").read_text(encoding="utf-8"))
        assert result["claude_md"], "every edit to CLAUDE.md is reported"

    def test_crlf_line_endings_are_kept(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _init(repo)
        (repo / "CLAUDE.md").write_bytes(b"# Mine\r\n\r\nWindows line endings.\r\n")

        _update(repo)

        raw = (repo / "CLAUDE.md").read_bytes()
        assert raw.startswith(b"# Mine\r\n\r\nWindows line endings.\r\n")
        assert b"\n" not in raw.replace(b"\r\n", b""), "the block is written in the file's own line ending"

    def test_a_dry_run_reports_the_block_without_writing(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _init(repo)
        (repo / "CLAUDE.md").write_text("# Mine\n", encoding="utf-8")

        result = update_project(repo, dry_run=True)

        assert (repo / "CLAUDE.md").read_text(encoding="utf-8") == "# Mine\n"
        assert "CLAUDE.md" in result["updated"]


class TestLegacyTrwContent:
    def test_an_upgrade_replaces_a_pre_8_trw_block_and_keeps_the_scaffold_and_user_lines(self, tmp_path: Path) -> None:
        """A 7.x CLAUDE.md: TRW's full protocol and promoted learnings inside its markers, the user's lines around."""
        repo = _repo(tmp_path)
        _init(repo)
        before = "# CLAUDE.md\n\n## What This Is\n\nA billing service.\n\n"
        after = "\n## Team rules\n\nTwo reviewers per PR.\n"
        legacy = (
            f"{_HEADER}\n{_START}\n\n## TRW Behavioral Protocol (Auto-Generated)\n\n"
            "### Learnings\n\n- L-old1: never mock the database\n- L-old2: run make check\n\n"
            f"{_END}\n"
        )
        (repo / "CLAUDE.md").write_text(before + legacy + after, encoding="utf-8")

        _update(repo)

        text = (repo / "CLAUDE.md").read_text(encoding="utf-8")
        assert text.count(_START) == 1
        assert "L-old1" not in text and "Behavioral Protocol" not in text, "TRW-marked legacy content goes"
        assert _IMPORT in _block(text)
        assert text.startswith(before.rstrip("\n")), "everything above TRW's markers is the user's"
        assert text.endswith(after), "everything below TRW's markers is the user's"

    def test_unbalanced_markers_leave_the_file_untouched_and_the_update_succeeds(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _init(repo)
        broken = f"# Mine\n\n{_START}\nhalf a block, no end marker\n"
        (repo / "CLAUDE.md").write_text(broken, encoding="utf-8")

        result = _update(repo)

        assert (repo / "CLAUDE.md").read_text(encoding="utf-8") == broken
        assert any("CLAUDE.md" in w and "marker" in w for w in result["warnings"]), result["warnings"]

    def test_a_symlinked_claude_md_is_never_written_through(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _init(repo)
        agents = (repo / "AGENTS.md").read_bytes()
        (repo / "CLAUDE.md").symlink_to("AGENTS.md")

        _update(repo)

        assert (repo / "CLAUDE.md").is_symlink()
        assert (repo / "AGENTS.md").read_bytes() == agents


class TestBothFilesDiverged:
    def test_each_file_keeps_its_own_content_and_nothing_is_synced(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        (repo / "CLAUDE.md").write_text("# For Claude\n\nClaude-only rule.\n", encoding="utf-8")
        (repo / "AGENTS.md").write_text("# For agents\n\nAgents-only rule.\n", encoding="utf-8")

        _init(repo)
        _update(repo)

        claude = (repo / "CLAUDE.md").read_text(encoding="utf-8")
        agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
        assert claude.startswith("# For Claude\n\nClaude-only rule.\n") and "Agents-only rule" not in claude
        assert agents.startswith("# For agents\n\nAgents-only rule.\n") and "Claude-only rule" not in agents
        assert _IMPORT in _block(claude) and _IMPORT in _block(agents)


class TestDoctor:
    def test_doctor_passes_once_claude_md_carries_the_block(self, tmp_path: Path) -> None:
        from trw_mcp.server._doctor_claude_md import claude_md_masks_agents_md_row

        repo = _repo(tmp_path)
        _init(repo)
        (repo / "CLAUDE.md").write_text("# Mine\n", encoding="utf-8")
        status, message = claude_md_masks_agents_md_row(repo)
        assert status == "WARN"
        assert "update-project" in message and "@AGENTS.md" not in message and "delete" not in message.lower()

        _update(repo)

        assert claude_md_masks_agents_md_row(repo)[0] == "PASS"

    def test_a_pre_8_block_without_the_import_still_warns(self, tmp_path: Path) -> None:
        from trw_mcp.server._doctor_claude_md import claude_md_masks_agents_md_row

        repo = _repo(tmp_path)
        _init(repo)
        (repo / "CLAUDE.md").write_text(f"# Mine\n\n{_START}\nold protocol\n{_END}\n", encoding="utf-8")

        assert claude_md_masks_agents_md_row(repo)[0] == "WARN"


class TestCodexR1:
    """CLAUDE-MD S1 codex r1: a concurrent save is never overwritten, fenced examples are not TRW's, reads are bounded."""

    def test_a_save_made_while_the_backup_runs_is_never_overwritten(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path)
        _init(repo)
        claude = repo / "CLAUDE.md"
        claude.write_text("# Mine\n", encoding="utf-8")
        from trw_mcp.state.claude_md import _write_backup

        prepare = _write_backup.prepare_backup_dir

        def save_during_backup(*args: object, **kwargs: object) -> Path:
            directory = prepare(*args, **kwargs)  # type: ignore[arg-type]
            claude.write_text("# Mine\n\nSaved while TRW was writing.\n", encoding="utf-8")
            return directory

        monkeypatch.setattr(_write_backup, "prepare_backup_dir", save_during_backup)

        result = _update(repo)

        assert claude.read_text(encoding="utf-8") == "# Mine\n\nSaved while TRW was writing.\n"
        assert any("CLAUDE.md left untouched" in w for w in result["warnings"]), result["warnings"]

    def test_a_save_made_after_trw_read_the_file_is_never_overwritten(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.bootstrap import _template_claude_md

        repo = _repo(tmp_path)
        _init(repo)
        claude = repo / "CLAUDE.md"
        claude.write_text("# Mine\n", encoding="utf-8")
        read = _template_claude_md._read_claude_md

        def read_then_user_saves(path: Path) -> object:
            text = read(path)
            claude.write_text("# Mine\n\nSaved just after TRW read it.\n", encoding="utf-8")
            return text

        monkeypatch.setattr(_template_claude_md, "_read_claude_md", read_then_user_saves)

        result = _update(repo)

        assert claude.read_text(encoding="utf-8") == "# Mine\n\nSaved just after TRW read it.\n"
        assert any("CLAUDE.md left untouched" in w for w in result["warnings"]), result["warnings"]

    def test_markers_inside_a_fenced_example_are_the_users(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _init(repo)
        example = f"# Docs\n\n~~~markdown\n{_START}\nan example of a TRW block\n{_END}\n~~~\n"
        (repo / "CLAUDE.md").write_text(example, encoding="utf-8")

        result = _update(repo)

        assert (repo / "CLAUDE.md").read_text(encoding="utf-8") == example
        assert any("CLAUDE.md" in w and "fenced" in w for w in result["warnings"]), result["warnings"]

    def test_doctor_does_not_pass_a_fenced_block(self, tmp_path: Path) -> None:
        from trw_mcp.server._doctor_claude_md import claude_md_masks_agents_md_row
        from trw_mcp.state.claude_md._instructions_link import claude_md_link_section

        repo = _repo(tmp_path)
        _init(repo)
        (repo / "CLAUDE.md").write_text(f"# Docs\n\n```\n{claude_md_link_section()}```\n", encoding="utf-8")

        assert claude_md_masks_agents_md_row(repo)[0] == "WARN"

    @pytest.mark.skipif(not hasattr(__import__("os"), "mkfifo"), reason="FIFOs are POSIX-only")
    def test_a_fifo_named_claude_md_never_stalls_the_update(self, tmp_path: Path) -> None:
        import os

        repo = _repo(tmp_path)
        _init(repo)
        os.mkfifo(repo / "CLAUDE.md")

        result = _update(repo)

        assert any("CLAUDE.md left untouched" in w and "regular file" in w for w in result["warnings"])

    def test_whitespace_only_bytes_are_kept(self, tmp_path: Path) -> None:
        repo = _repo(tmp_path)
        _init(repo)
        (repo / "CLAUDE.md").write_bytes(b"  \r\n\t\r\n")

        _update(repo)

        raw = (repo / "CLAUDE.md").read_bytes()
        assert raw.startswith(b"  \r\n\t\r\n") and _IMPORT.encode() in raw

    def test_a_legacy_recorded_client_that_resolves_to_claude_code_is_linked(self, tmp_path: Path) -> None:
        import yaml

        repo = _repo(tmp_path)
        _init(repo)
        config = repo / ".trw" / "config.yaml"
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        data["target_platforms"] = ["cursor"]
        config.write_text(yaml.safe_dump(data), encoding="utf-8")
        (repo / "CLAUDE.md").write_text("# Mine\n", encoding="utf-8")

        _update(repo)

        assert _IMPORT in (repo / "CLAUDE.md").read_text(encoding="utf-8")


class TestCodexR2:
    """CLAUDE-MD S1 codex r2 and PUBLISH-RACE-HARDEN: the publish never discards a save; inline code is no fence.

    The guard publishes through ``_publish``: the file is captured by rename and re-proven by content, the complete
    new file is linked at the free name, and the displaced file becomes the backup by rename.
    """

    @staticmethod
    def _write(root: Path, target: Path, candidate: str) -> object:
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.claude_md._write_guard import guarded_instruction_write

        return guarded_instruction_write(
            target, candidate, enforce_shrink_floor=False, config=TRWConfig(), project_root=root
        )

    def test_a_save_between_the_capture_and_the_publish_keeps_the_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        target = tmp_path / "AGENTS.md"
        target.write_text("judged\n", encoding="utf-8")
        real_link = os.link

        def save_first(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
            if os.fsdecode(dst) == "AGENTS.md" and os.fsdecode(src) == "new":
                target.write_text("saved\n", encoding="utf-8")
            return real_link(src, dst, *args, **kwargs)

        monkeypatch.setattr(os, "link", save_first)

        verdict = self._write(tmp_path, target, "ours\n")

        assert not verdict.written and verdict.refusal["reason"] == "changed_during_write"  # type: ignore[attr-defined]
        assert target.read_text(encoding="utf-8") == "saved\n"
        assert any(p.read_bytes() == b"judged\n" for p in (tmp_path / ".trw" / "trash").glob("*/data"))

    def test_a_save_made_in_place_the_moment_the_new_file_appears_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """r3's create-then-fill let its fill overwrite an in-place save; a link has no fill."""
        import os

        target = tmp_path / "AGENTS.md"
        target.write_text("judged\n", encoding="utf-8")
        real_link, real_open = os.link, os.open
        fired: list[bool] = []

        def save_in_place() -> None:
            if not fired:
                fired.append(True)
                fd = real_open(target, os.O_WRONLY | os.O_TRUNC)
                os.write(fd, b"saved in place\n")
                os.close(fd)

        def link(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
            real_link(src, dst, *args, **kwargs)
            if os.fsdecode(dst) == "AGENTS.md":
                save_in_place()

        def open_(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
            fd = real_open(path, flags, *args, **kwargs)
            if (
                isinstance(path, (str, os.PathLike))
                and Path(os.fsdecode(path)).name == "AGENTS.md"
                and flags & os.O_EXCL
            ):
                save_in_place()
            return fd

        monkeypatch.setattr(os, "link", link)
        monkeypatch.setattr(os, "open", open_)

        self._write(tmp_path, target, "ours\n")

        assert fired and target.read_bytes() == b"saved in place\n"

    def test_the_displaced_file_becomes_the_backup_by_rename(self, tmp_path: Path) -> None:
        """Spec (a): no copy is taken; the very inode that held the judged bytes is the backup."""
        import os

        target = tmp_path / "AGENTS.md"
        target.write_text("judged\n", encoding="utf-8")
        inode = os.stat(target).st_ino

        verdict = self._write(tmp_path, target, "ours\n")

        assert verdict.written and target.read_text(encoding="utf-8") == "ours\n"  # type: ignore[attr-defined]
        backup = Path(verdict.backup_path)  # type: ignore[attr-defined, arg-type]
        assert backup.parent == tmp_path / ".trw" / "backups" / "instructions"
        assert os.stat(backup).st_ino == inode and backup.read_bytes() == b"judged\n"
        assert not list((tmp_path / ".trw" / "trash").glob("*/data")), "nothing left behind in .trw/trash"

    def test_a_refused_backup_rename_reports_the_capture_and_copies_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec (c): the backup directory on another filesystem; the displaced file stays where it is, named."""
        import errno

        from trw_mcp.state.claude_md import _write_backup

        target = tmp_path / "AGENTS.md"
        target.write_text("judged\n", encoding="utf-8")

        real_link = _write_backup.os.link

        def cross_device(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
            if Path(_write_backup.os.fsdecode(dst)).parent.name == "instructions":  # into the backup directory only
                raise OSError(errno.EXDEV, "Cross-device link")
            return real_link(src, dst, *args, **kwargs)

        monkeypatch.setattr(_write_backup.os, "link", cross_device)

        verdict = self._write(tmp_path, target, "ours\n")

        assert verdict.written and target.read_text(encoding="utf-8") == "ours\n"  # type: ignore[attr-defined]
        backup = Path(verdict.backup_path)  # type: ignore[attr-defined, arg-type]
        assert backup.parent.parent == tmp_path / ".trw" / "trash" and backup.read_bytes() == b"judged\n"

    def test_a_failed_publish_gives_the_name_back_and_leaves_no_temp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec (d) and (f): the link fails; the displaced file is back at the name; no staged file remains."""
        import errno
        import os

        target = tmp_path / "AGENTS.md"
        target.write_text("judged\n", encoding="utf-8")
        real_link = os.link

        def full_disk(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
            if os.fsdecode(src) == "new" and os.fsdecode(dst) == "AGENTS.md":
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_link(src, dst, *args, **kwargs)

        monkeypatch.setattr(os, "link", full_disk)

        verdict = self._write(tmp_path, target, "ours\n")

        assert not verdict.written  # type: ignore[attr-defined]
        assert target.read_text(encoding="utf-8") == "judged\n"
        assert not list((tmp_path / ".trw" / "trash").glob("publish-*")), "the staged file is gone"

    def test_a_new_file_follows_the_umask(self, tmp_path: Path) -> None:
        """Spec (e)."""
        import os

        target = tmp_path / "AGENTS.md"
        old = os.umask(0o027)
        try:
            verdict = self._write(tmp_path, target, "ours\n")
        finally:
            os.umask(old)

        assert verdict.written and target.read_text(encoding="utf-8") == "ours\n"  # type: ignore[attr-defined]
        assert target.stat().st_mode & 0o777 == 0o640

    def test_inline_code_with_three_backticks_is_not_a_fence(self, tmp_path: Path) -> None:
        from trw_mcp.server._doctor_claude_md import claude_md_masks_agents_md_row

        repo = _repo(tmp_path)
        _init(repo)
        (repo / "CLAUDE.md").write_text("# Mine\n\nRun ```make check``` first.\n", encoding="utf-8")

        _update(repo)

        assert _IMPORT in _block((repo / "CLAUDE.md").read_text(encoding="utf-8"))
        assert claude_md_masks_agents_md_row(repo)[0] == "PASS"


# --- S1 impact gate: the hardened publish leaves no stray TRW state and reports I/O failures as such ----------


def test_publishing_a_new_file_leaves_no_trw_directory_in_a_project_without_one(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._proven_replace import create_exclusive

    target = tmp_path / ".opencode" / "INSTRUCTIONS.md"
    target.parent.mkdir()

    assert create_exclusive(target, tmp_path, b"generated\n").status == "replaced"

    assert target.read_bytes() == b"generated\n"
    assert not (tmp_path / ".trw").exists()


def test_an_existing_empty_trash_directory_is_kept(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._proven_replace import create_exclusive

    (tmp_path / ".trw" / "trash").mkdir(parents=True)

    assert create_exclusive(tmp_path / "AGENTS.md", tmp_path, b"x\n").status == "replaced"

    assert (tmp_path / ".trw" / "trash").is_dir()


def test_a_failed_stage_is_reported_as_a_write_failure_not_a_concurrent_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _proven_replace

    def full_disk(*_args: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(_proven_replace, "_write_new", full_disk)
    target = tmp_path / "AGENTS.md"
    target.write_bytes(b"user\n")

    replaced = _proven_replace.replace_proven(target, tmp_path, b"user\n", b"user\nmore\n")
    created = _proven_replace.create_exclusive(tmp_path / "NEW.md", tmp_path, b"x\n")

    assert (replaced.status, replaced.failed) == ("refused", True)
    assert (created.status, created.failed) == ("refused", True)
    assert target.read_bytes() == b"user\n"
    assert not (tmp_path / "NEW.md").exists()


def test_a_published_instruction_file_is_recorded_as_this_runs_own_write(tmp_path: Path) -> None:
    """An update's rollback clears TRW's own write only on proof from the run ledger (FB-01-KI1-RACE)."""
    import hashlib

    from trw_mcp._checkout_write import recording_writes, written_this_run
    from trw_mcp.state.claude_md._publish import publish

    target = tmp_path / "AGENTS.md"
    target.write_text("old\n", encoding="utf-8")
    with recording_writes():
        outcome = publish(target, tmp_path, "new\n", "old\n", None, 3)
        assert outcome.blocked is None
        assert written_this_run(target) == hashlib.sha256(b"new\n").hexdigest()
