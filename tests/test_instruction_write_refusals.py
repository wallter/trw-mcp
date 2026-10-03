"""An instruction write that cannot run safely is refused and reported, never raised (CLAUDE-MD-S1-r2 KI2, FORCE KI).

Two inputs used to escape the structured refusal as an exception: a platform without the fd-anchored primitives
(native Windows has no ``os.O_DIRECTORY``; ``_proven_replace._staged`` raised AttributeError) and an existing
instruction file that is not valid UTF-8 (the merge read raised UnicodeDecodeError). Neither wrote anything, but the
caller crashed instead of reporting the file as left untouched.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ._copilot_test_support import fake_git_repo  # noqa: F401


def test_unsupported_platform_refuses_and_leaves_the_file_as_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _trash
    from trw_mcp.state.claude_md._write_guard import guarded_instruction_write

    target = tmp_path / "CLAUDE.md"
    original = "# Mine\n\n<!-- trw:start -->\nold\n<!-- trw:end -->\n"
    target.write_text(original, encoding="utf-8")
    monkeypatch.setattr(_trash, "_UNSUPPORTED", "platform lacks O_DIRECTORY/O_NOFOLLOW/O_NONBLOCK")
    monkeypatch.delattr(os, "O_DIRECTORY", raising=False)

    verdict = guarded_instruction_write(
        target, "# Mine\n\n<!-- trw:start -->\nnew\n<!-- trw:end -->\n", project_root=tmp_path
    )

    assert not verdict.written
    assert verdict.refusal is not None and "platform lacks" in verdict.refusal["detail"]
    assert target.read_text(encoding="utf-8") == original


def test_unsupported_platform_refuses_a_new_file_without_creating_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _trash
    from trw_mcp.state.claude_md._write_guard import guarded_instruction_write

    target = tmp_path / "AGENTS.md"
    monkeypatch.setattr(_trash, "_UNSUPPORTED", "platform lacks O_DIRECTORY/O_NOFOLLOW/O_NONBLOCK")
    monkeypatch.delattr(os, "O_DIRECTORY", raising=False)

    verdict = guarded_instruction_write(target, "<!-- trw:start -->\nx\n<!-- trw:end -->\n", project_root=tmp_path)

    assert not verdict.written
    assert not target.exists()


@pytest.mark.parametrize("force", [False, True], ids=["plain", "force"])
def test_an_instruction_file_that_is_not_utf8_is_reported_and_left_as_found(fake_git_repo: Path, force: bool) -> None:
    from trw_mcp.bootstrap._copilot import generate_copilot_instructions

    target = fake_git_repo / ".github" / "copilot-instructions.md"
    target.parent.mkdir(parents=True)
    original = b"# R\xe9gles\nlatin-1, not UTF-8\n"
    target.write_bytes(original)

    result = generate_copilot_instructions(fake_git_repo, force=force)

    assert target.read_bytes() == original
    assert [e for e in result["errors"] if "copilot-instructions.md" in e], result
