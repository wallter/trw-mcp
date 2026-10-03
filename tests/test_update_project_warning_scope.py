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

import pytest

from trw_mcp.bootstrap import init_project, update_project

from ._bootstrap_test_support import fake_git_repo  # noqa: F401

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

_CACHED_HOOKS = "Running Claude Code sessions use cached hooks/settings"


def test_pointer_claude_md_is_not_reported_as_missing_markers(fake_git_repo: Path) -> None:
    init_project(fake_git_repo, ide="claude-code")
    (fake_git_repo / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")

    result = update_project(fake_git_repo, ide="claude-code")

    assert not [w for w in result.get("warnings", []) if "missing TRW auto-generated markers" in w]
    # FB-INSTALL-02: a pointer-only CLAUDE.md imports AGENTS.md and is the user's adapter; it is kept, unwarned,
    # and gains only TRW's marked block (operator P0 2026-10-01: never deleted, the TRW context imported directly).
    text = (fake_git_repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("@AGENTS.md\n") and "@.trw/INSTRUCTIONS.md" in text
    notes = [w for w in result.get("warnings", []) if "CLAUDE.md" in w]
    # The one CLAUDE.md line is the named copy of the user's previous version (CANARY-ACCEPT dev22 P0); no complaint.
    assert len(notes) == 1 and "your previous version is kept at" in notes[0], notes


def test_an_agents_md_without_markers_that_is_not_a_pointer_still_warns(fake_git_repo: Path) -> None:
    """Non-vacuity partner: the check itself still fires for a real gap.

    Called directly because a real update appends the TRW block first, so the
    post-update verification never sees a marker-less non-pointer file.
    """
    from trw_mcp.bootstrap._utils import _check_instruction_markers

    init_project(fake_git_repo, ide="claude-code")
    (fake_git_repo / "AGENTS.md").write_text("# My project\n\nHand-written notes, no TRW block.\n", encoding="utf-8")
    result: dict[str, list[str]] = {"warnings": []}

    _check_instruction_markers(fake_git_repo, result)

    assert [w for w in result["warnings"] if "AGENTS.md missing TRW auto-generated markers" in w]


def test_cached_hooks_note_prints_only_when_claude_code_is_targeted(fake_git_repo: Path) -> None:
    init_project(fake_git_repo, ide="codex")
    codex_run = update_project(fake_git_repo, ide="codex")
    assert not [w for w in codex_run.get("warnings", []) if _CACHED_HOOKS in w]

    claude_run = update_project(fake_git_repo, ide="claude-code")
    assert [w for w in claude_run.get("warnings", []) if _CACHED_HOOKS in w]


def test_a_refused_update_does_not_tell_the_user_to_restart_to_pick_up_updates(
    fake_git_repo: Path, tmp_path: Path
) -> None:
    """INC-122(b): a run that updated nothing (a managed directory is a symlink) has nothing to reload."""
    init_project(fake_git_repo, ide="claude-code")
    hooks = fake_git_repo / ".claude" / "hooks"
    elsewhere = tmp_path / "elsewhere"
    hooks.rename(elsewhere)
    hooks.symlink_to(elsewhere)

    result = update_project(fake_git_repo, ide="claude-code")

    assert result["errors"], "precondition: the symlinked managed directory refused the update"
    assert not [w for w in result.get("warnings", []) if _CACHED_HOOKS in w]


@pytest.mark.parametrize("ide", ["codex", "cursor-ide", "copilot", "grok", "opencode", "antigravity-cli"])
def test_a_missing_mcp_json_is_not_warned_for_a_profile_that_keeps_its_entry_elsewhere(
    fake_git_repo: Path, ide: str
) -> None:
    """INC-117(d): ``.mcp.json`` is Claude Code's file; its absence is normal for every other profile."""
    init_project(fake_git_repo, ide=ide)
    assert not (fake_git_repo / ".mcp.json").exists(), "precondition: this profile writes no .mcp.json"

    result = update_project(fake_git_repo, ide=ide)

    assert not [w for w in result.get("warnings", []) if ".mcp.json not found" in w]
