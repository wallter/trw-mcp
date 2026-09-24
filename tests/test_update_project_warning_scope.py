"""PRD-INFRA-192 I4: update-project warnings name only what is true of this project.

Two false warnings eroded trust in every other printed line:

* a single-source ``@AGENTS.md`` pointer ``CLAUDE.md`` -- the intended layout, which
  doctor's own instruction-surface check passes -- was reported as "missing TRW
  auto-generated markers";
* the Claude-Code-only "restart sessions for cached hooks" note printed on runs
  that never targeted Claude Code.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.bootstrap import init_project, update_project

from ._bootstrap_test_support import fake_git_repo  # noqa: F401

_CACHED_HOOKS = "Running Claude Code sessions use cached hooks/settings"


def test_pointer_claude_md_is_not_reported_as_missing_markers(fake_git_repo: Path) -> None:
    init_project(fake_git_repo, ide="claude-code")
    (fake_git_repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")

    result = update_project(fake_git_repo, ide="claude-code")

    assert not [w for w in result.get("warnings", []) if "CLAUDE.md missing TRW auto-generated markers" in w]
    assert (fake_git_repo / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"


def test_a_claude_md_without_markers_that_is_not_a_pointer_still_warns(fake_git_repo: Path) -> None:
    """Non-vacuity partner: the check itself still fires for a real gap.

    Called directly because a real update appends the TRW block first, so the
    post-update verification never sees a marker-less non-pointer file.
    """
    from trw_mcp.bootstrap._utils import _check_instruction_markers

    init_project(fake_git_repo, ide="claude-code")
    (fake_git_repo / "CLAUDE.md").write_text("# My project\n\nHand-written notes, no TRW block.\n", encoding="utf-8")
    result: dict[str, list[str]] = {"warnings": []}

    _check_instruction_markers(fake_git_repo, result)

    assert [w for w in result["warnings"] if "CLAUDE.md missing TRW auto-generated markers" in w]


def test_cached_hooks_note_prints_only_when_claude_code_is_targeted(fake_git_repo: Path) -> None:
    init_project(fake_git_repo, ide="codex")
    codex_run = update_project(fake_git_repo, ide="codex")
    assert not [w for w in codex_run.get("warnings", []) if _CACHED_HOOKS in w]

    claude_run = update_project(fake_git_repo, ide="claude-code")
    assert [w for w in claude_run.get("warnings", []) if _CACHED_HOOKS in w]
