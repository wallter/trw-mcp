"""REMOVE-S2: doctor names a root CLAUDE.md that hides AGENTS.md from Claude Code.

Claude Code reads ``AGENTS.md`` only when no ``CLAUDE.md`` exists. update-project used to retire a
TRW-only ``CLAUDE.md`` itself (the one-time 8.0 migration) and warn about one with user content. That
migration is gone; this doctor row is now the one place the masking is reported. update-project no
longer touches a root ``CLAUDE.md`` at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401

_WARNING_FIX = "add an `@AGENTS.md` line"


def _project(tmp_path: Path, targets: list[str]) -> Path:
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text(yaml.safe_dump({"target_platforms": targets}), encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    return tmp_path


def _row(target: Path) -> tuple[str, str]:
    from trw_mcp.server._doctor_claude_md import claude_md_masks_agents_md_row

    return claude_md_masks_agents_md_row(target)


def test_no_claude_md_passes(tmp_path: Path) -> None:
    assert _row(_project(tmp_path, ["claude-code"]))[0] == "PASS"


def test_a_claude_md_with_user_content_warns_with_the_fix(tmp_path: Path) -> None:
    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").write_text("# my notes\nUse tabs.\n", encoding="utf-8")

    status, message = _row(project)

    assert status == "WARN"
    assert "skips AGENTS.md" in message
    assert _WARNING_FIX in message


@pytest.mark.parametrize("adapter", ["@AGENTS.md\n", "# notes\n@./AGENTS.md\nmore\n", "  @AGENTS.md  \n"])
def test_a_claude_md_that_imports_agents_md_passes(tmp_path: Path, adapter: str) -> None:
    """FB-INSTALL-02: the adapter is the user's fix, never a finding."""
    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").write_text(adapter, encoding="utf-8")

    assert _row(project)[0] == "PASS"


def test_a_symlink_to_agents_md_passes(tmp_path: Path) -> None:
    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").symlink_to("AGENTS.md")

    assert _row(project)[0] == "PASS"


def test_a_symlink_elsewhere_warns(tmp_path: Path) -> None:
    project = _project(tmp_path, ["claude-code"])
    (project / "notes.md").write_text("x\n", encoding="utf-8")
    (project / "CLAUDE.md").symlink_to("notes.md")

    assert _row(project)[0] == "WARN"


def test_a_project_without_claude_code_skips(tmp_path: Path) -> None:
    project = _project(tmp_path, ["codex"])
    (project / "CLAUDE.md").write_text("# mine\n", encoding="utf-8")

    assert _row(project)[0] == "SKIP"


def test_an_unreadable_claude_md_warns_rather_than_passing(tmp_path: Path) -> None:
    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").mkdir()  # a directory: exists, cannot be read as a file

    assert _row(project)[0] == "WARN"


def test_the_row_is_registered_in_doctor() -> None:
    from trw_mcp.server import _subcommands_doctor
    from trw_mcp.server._doctor_checks_registry import CHECKS

    names = dict(CHECKS)
    assert names["claude_md_masks_agents_md"] == "_check_claude_md_masks_agents_md"
    assert callable(getattr(_subcommands_doctor, "_check_claude_md_masks_agents_md"))


@pytest.mark.usefixtures("no_memory_daemon")
@pytest.mark.parametrize(
    "content",
    ["<!-- trw:start -->\nold TRW block\n<!-- trw:end -->\n", "@AGENTS.md\n", "# mine\nUse tabs.\n"],
    ids=["trw-only", "adapter", "user-prose"],
)
def test_update_project_never_touches_a_root_claude_md(initialized_repo: Path, content: str) -> None:
    """The one-time 8.0 retirement is gone: update-project neither trashes, edits nor warns; doctor reports.

    The adapter case is FB-INSTALL-02's guarantee: a lone ``@AGENTS.md`` CLAUDE.md is the user's and stays.
    """
    from trw_mcp.bootstrap import update_project

    claude_md = initialized_repo / "CLAUDE.md"
    claude_md.write_text(content, encoding="utf-8")

    result = update_project(initialized_repo)

    assert not result["errors"], result["errors"]  # a failed or rolled-back update would prove nothing
    assert (initialized_repo / "AGENTS.md").is_file()
    assert claude_md.read_text(encoding="utf-8") == content
    assert "CLAUDE.md" not in result.get("trashed", [])
    assert not [w for w in result.get("warnings", []) if "CLAUDE.md" in w]


@pytest.mark.parametrize(
    "content",
    ["<!-- trw:start -->\nold TRW block\n<!-- trw:end -->\n", "@AGENTS.md\n", "# mine\nUse tabs.\n"],
    ids=["trw-only", "adapter", "user-prose"],
)
def test_init_project_never_touches_a_root_claude_md(fake_git_repo: Path, content: str) -> None:
    from trw_mcp.bootstrap import init_project

    claude_md = fake_git_repo / "CLAUDE.md"
    claude_md.write_text(content, encoding="utf-8")

    result = init_project(fake_git_repo, ide="claude-code")

    assert not result["errors"], result["errors"]
    assert (fake_git_repo / "AGENTS.md").is_file()
    assert claude_md.read_text(encoding="utf-8") == content
    assert "CLAUDE.md" not in result.get("trashed", [])


def test_a_trw_only_leftover_warns_to_delete_it(tmp_path: Path) -> None:
    """A pre-8.0 TRW-only CLAUDE.md is not 'user content': the row says to delete it."""
    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").write_text("<!-- trw:start -->\nold block\n<!-- trw:end -->\n", encoding="utf-8")

    status, message = _row(project)

    assert status == "WARN"
    assert "only TRW content" in message
    assert "delete it" in message


# --- codex r1 KIs: Claude Code's import rule, confined and bounded reads, symlink edges ---------------------------


@pytest.mark.parametrize(
    "content",
    ["```text\n@AGENTS.md\n```\n", "~~~\n@AGENTS.md\n~~~\n", "Use `@AGENTS.md` to import it.\n"],
    ids=["backtick-fence", "tilde-fence", "code-span"],
)
def test_an_import_inside_code_is_inert_and_warns(tmp_path: Path, content: str) -> None:
    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").write_text(content, encoding="utf-8")

    assert _row(project)[0] == "WARN"


@pytest.mark.parametrize(
    "content", ["@AGENTS.md # shared instructions\n", "See @AGENTS.md for the protocol.\n", "@AGENTS.md\r\nmore\r\n"]
)
def test_a_working_import_with_surrounding_text_passes(tmp_path: Path, content: str) -> None:
    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").write_bytes(content.encode("utf-8"))

    assert _row(project)[0] == "PASS"


def test_a_symlink_is_judged_by_its_target_never_read(tmp_path: Path) -> None:
    """A link to an outside file that says ``@AGENTS.md`` must not PASS: the row never follows it for content."""
    (tmp_path / "proj").mkdir()
    project = _project(tmp_path / "proj", ["claude-code"])
    outside = tmp_path / "outside.md"
    outside.write_text("@AGENTS.md\n", encoding="utf-8")
    (project / "CLAUDE.md").symlink_to(outside)

    status, message = _row(project)

    assert status == "WARN"
    assert "not AGENTS.md" in message


def test_a_symlink_to_a_missing_agents_md_warns(tmp_path: Path) -> None:
    project = _project(tmp_path, ["claude-code"])
    (project / "AGENTS.md").unlink()
    (project / "CLAUDE.md").symlink_to("AGENTS.md")

    assert _row(project)[0] == "WARN"


def test_a_looping_symlink_warns_instead_of_failing(tmp_path: Path) -> None:
    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").symlink_to("CLAUDE.md")

    status, message = _row(project)

    assert status == "WARN"
    assert "broken or looping symlink" in message


@pytest.mark.skipif(not hasattr(__import__("os"), "mkfifo"), reason="FIFOs are POSIX-only")
def test_a_fifo_is_not_read_and_does_not_block(tmp_path: Path) -> None:
    import os

    project = _project(tmp_path, ["claude-code"])
    os.mkfifo(project / "CLAUDE.md")

    status, message = _row(project)  # a blocking open with no writer would hang here

    assert status == "WARN"
    assert "not a regular file" in message


def test_a_huge_file_is_read_only_up_to_the_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.server import _doctor_claude_md

    monkeypatch.setattr(_doctor_claude_md, "_MAX_READ", 64)
    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").write_text("x" * 10_000 + "\n@AGENTS.md\n", encoding="utf-8")

    status, message = _row(project)

    assert status == "WARN"  # the import past the bound is never seen
    assert "has user content" in message


# --- codex r2 KIs: CommonMark spans and fences, reader failures, truncation, portability ---------------------------


@pytest.mark.parametrize(
    "content",
    ["Use ``example @AGENTS.md other`` here.\n", "Use ```@AGENTS.md``` here.\n"],
    ids=["double-backtick-span", "triple-backtick-span"],
)
def test_an_import_inside_a_multi_backtick_span_is_inert(tmp_path: Path, content: str) -> None:
    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").write_text(content, encoding="utf-8")

    assert _row(project)[0] == "WARN"


@pytest.mark.parametrize(
    "content",
    ["    ```\n\n@AGENTS.md\n", "````\ncode\n```\nstill code\n````\n@AGENTS.md\n", "```\ncode\n```   \n@AGENTS.md\n"],
    ids=["indented-is-not-a-fence", "longer-fence-needs-longer-close", "closing-fence-with-trailing-space"],
)
def test_fences_follow_commonmark(tmp_path: Path, content: str) -> None:
    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").write_text(content, encoding="utf-8")

    assert _row(project)[0] == "PASS"


def test_a_read_failure_after_open_warns_and_closes_the_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from trw_mcp.server import _doctor_claude_md

    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").write_text("# mine\n", encoding="utf-8")
    opened: list[int] = []
    real_open = os.open

    def _open(*args: object, **kwargs: object) -> int:
        fd = real_open(*args, **kwargs)  # type: ignore[arg-type]
        opened.append(fd)
        return fd

    def _fstat(_fd: int) -> os.stat_result:
        raise OSError(5, "I/O error")

    monkeypatch.setattr(_doctor_claude_md.os, "open", _open)
    monkeypatch.setattr(_doctor_claude_md.os, "fstat", _fstat)

    status, message = _row(project)

    assert status == "WARN"
    assert "cannot be read" in message
    assert opened
    with pytest.raises(OSError):  # already closed by the row: closing again is EBADF, not a leaked fd
        os.close(opened[0])


def test_truncation_never_manufactures_an_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read cut mid-token must not turn ``@AGENTS.md.evil`` into ``@AGENTS.md``."""
    from trw_mcp.server import _doctor_claude_md

    head = "x" * 20 + "\n"
    monkeypatch.setattr(_doctor_claude_md, "_MAX_READ", len(head) + len("@AGENTS.md") - 1)
    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").write_text(head + "@AGENTS.md.evil\n", encoding="utf-8")

    assert _row(project)[0] == "WARN"


def test_the_reader_works_where_os_lacks_o_nonblock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows has no O_NONBLOCK (or O_NOFOLLOW); the row must still read a regular file."""
    from trw_mcp.server import _doctor_claude_md

    monkeypatch.delattr(_doctor_claude_md.os, "O_NONBLOCK", raising=False)
    project = _project(tmp_path, ["claude-code"])
    (project / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")

    assert _row(project)[0] == "PASS"
