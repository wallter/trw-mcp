"""PRD-CORE-337 FR07 -- the channel writers refuse a planted symlink instead of writing through it.

Each case plants a symlink inside a fresh project, pointing OUTSIDE it, and runs one channel writer. Before
PRD-CORE-337 each one wrote through the link: it changed a sentinel file, created a file outside the
project, or (the telemetry prune) replaced the link with a copy of what it pointed at. Now the write is
refused: nothing outside the project changes and the planted link is still a link. The refusal surfaces
through the writer's existing contract: a reported error, the typed ``UnsafeWriteError``, or -- for the
fail-open telemetry writers -- nothing but the sentinel staying untouched.

Scope: proves each migrated FR07 site refuses a leaf (and, where the site walks from a repo root, a parent)
symlink; does not prove output bytes are unchanged for a clean project (``test_checkout_write.py`` pins the
adapter, the channels' own suites pin their content).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from trw_memory.safe_fs import UnsafeWriteError

from trw_mcp.channels._gitignore import add_gitignore_entry
from trw_mcp.channels._lock import ChannelLock
from trw_mcp.channels._telemetry import _write_channel_event, append_channel_event
from trw_mcp.channels.antigravity._before_edit_hook import (
    _AG03_HOOK_SCRIPT_PATH,
    AG03_HOOKS_PATH,
    install_before_edit_hook,
)
from trw_mcp.channels.antigravity._explorer_subagent import _AGENT_RELATIVE_PATH, generate_distill_explorer_agent
from trw_mcp.channels.claude_code._hook_helpers import write_hint_file
from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script
from trw_mcp.channels.copilot._vscode_mcp import generate_vscode_mcp_config
from trw_mcp.channels.opencode._shared_lock import agents_md_lock

_SENTINEL = b"x = 1\n"
_EVENT = {"channel_id": "cc-03", "client": "claude-code", "event_type": "pull_tool_call"}


def _run(call: Callable[[Path], object], project: Path) -> str:
    try:
        return repr(call(project))
    except UnsafeWriteError as refused:
        return f"raised {refused.reason}: {refused}"


def _hold_lock(lock: ChannelLock) -> str:
    with lock:
        return "acquired"


def _hint(project: Path) -> None:
    write_hint_file(
        hints_dir=project / ".trw" / "hints",
        tool_use_id="toolu_1",
        file_path="/x.py",
        tier="T1",
        hint_emitted=True,
        tokens_emitted=3,
        distill_status="ok",
    )


_LEAF_CASES = [
    pytest.param(".gitignore", lambda p: add_gitignore_entry(p, ".trw/x"), "symlink_leaf", id="gitignore"),
    pytest.param(
        "events.jsonl",
        lambda p: _write_channel_event(log_path=p / "events.jsonl", optional_fields={}, **_EVENT),
        "symlink_leaf",
        id="telemetry-append",
    ),
    pytest.param(
        "events.jsonl",
        lambda p: append_channel_event(log_path=p / "events.jsonl", **_EVENT),
        "None",
        id="telemetry-append-fail-open",
    ),
    pytest.param(_AG03_HOOK_SCRIPT_PATH, install_before_edit_hook, "symlink_leaf", id="antigravity-hook-script"),
    pytest.param(AG03_HOOKS_PATH, install_before_edit_hook, "symlink_leaf", id="antigravity-hooks-json"),
    pytest.param(
        _AGENT_RELATIVE_PATH,
        lambda p: generate_distill_explorer_agent(repo_root=p, sidecar_data=None, sidecar_sha=None),
        "symlink_leaf",
        id="antigravity-explorer-agent",
    ),
    pytest.param(".trw/hints/toolu_1.json", _hint, "symlink_leaf", id="claude-code-hint-file"),
    pytest.param(".codex/hooks/trw_post_edit_telemetry.py", install_hook_script, "symlink_leaf", id="codex-hook"),
    pytest.param(".vscode/mcp.json", generate_vscode_mcp_config, "symlink_leaf", id="copilot-vscode-mcp"),
]


@pytest.mark.parametrize(("rel", "writer", "surfaced"), _LEAF_CASES)
def test_a_planted_leaf_symlink_is_refused_not_followed(
    tmp_path: Path, rel: str, writer: Callable[[Path], object], surfaced: str
) -> None:
    project = tmp_path / "project"
    sentinel = tmp_path / "outside" / "sentinel"
    sentinel.parent.mkdir()
    sentinel.write_bytes(_SENTINEL)
    (project / rel).parent.mkdir(parents=True, exist_ok=True)
    (project / rel).symlink_to(sentinel)

    outcome = _run(writer, project)

    assert sentinel.read_bytes() == _SENTINEL, f"{rel}: the writer wrote through the planted link"
    assert (project / rel).is_symlink()
    assert surfaced in outcome


def test_telemetry_append_refuses_a_symlinked_leaf(tmp_path: Path) -> None:
    """The PRD's named FR07 acceptance case, on the changed function itself (no fail-open wrapper)."""
    log = tmp_path / "channel-events.jsonl"
    sentinel = tmp_path / "sentinel.jsonl"
    sentinel.write_bytes(_SENTINEL)
    log.symlink_to(sentinel)

    with pytest.raises(UnsafeWriteError) as refused:
        _write_channel_event(log_path=log, optional_fields={}, **_EVENT)

    assert refused.value.reason == "symlink_leaf"
    assert sentinel.read_bytes() == _SENTINEL


def test_channel_lock_refuses_a_dangling_leaf_symlink(tmp_path: Path) -> None:
    """``open(lock, 'a+')`` created the link's missing target outside the project; the lock now refuses."""
    target = tmp_path / "outside" / "created-by-lock"
    target.parent.mkdir()
    lock_path = tmp_path / "project" / ".trw" / "channels" / "x.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.symlink_to(target)

    outcome = _run(lambda _p: _hold_lock(ChannelLock(lock_path)), tmp_path)

    assert not target.exists()
    assert "symlink_leaf" in outcome


def test_channel_lock_refuses_a_symlinked_channels_dir(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".trw").mkdir(parents=True)
    (project / ".trw" / "channels").symlink_to(outside, target_is_directory=True)

    outcome = _run(lambda p: _hold_lock(agents_md_lock(p)), project)

    assert list(outside.iterdir()) == []
    assert "symlink_component" in outcome


def test_channel_lock_still_locks(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    lock_path = project / ".trw" / "channels" / "x.lock"

    with ChannelLock(lock_path, root=project) as held:
        assert held.lock_path.is_file()


def test_telemetry_append_under_a_symlinked_repo_telemetry_dir_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default log walks from TRW_REPO_ROOT, so a symlinked ``.trw/telemetry`` is refused, not followed."""
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".trw").mkdir(parents=True)
    (project / ".trw" / "telemetry").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("TRW_REPO_ROOT", str(project))

    with pytest.raises(UnsafeWriteError) as refused:
        _write_channel_event(log_path=None, optional_fields={}, **_EVENT)

    assert refused.value.reason == "symlink_component"
    assert list(outside.iterdir()) == []
