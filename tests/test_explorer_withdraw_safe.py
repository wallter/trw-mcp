"""FS-LINT row 2: the CC-05 explorer-agent withdraw deletes in place, never into ``.trw/trash`` (HB-2).

A TRW-rendered (or recorded) agent is deleted; one git holds clean is deleted and named with its restore
command; an uncommitted edit is kept and named with the command that removes it.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _agent(tmp_path: Path) -> tuple[Path, Path, bytes]:
    from trw_mcp.channels.claude_code._explorer_subagent import EXPLORER_AGENT_RELPATH, get_explorer_agent_content

    root = tmp_path / "proj"
    agent = root / EXPLORER_AGENT_RELPATH
    agent.parent.mkdir(parents=True)
    body = get_explorer_agent_content().encode("utf-8")
    agent.write_bytes(body)
    return root, agent, body


def test_an_unedited_agent_is_deleted_in_place(tmp_path: Path) -> None:
    from trw_mcp.channels.claude_code._explorer_subagent import withdraw_cc05_subagent_if_unedited

    root, agent, _body = _agent(tmp_path)
    assert withdraw_cc05_subagent_if_unedited(root, None) is True
    assert not agent.exists()
    assert not (root / ".trw" / "trash").exists()


def test_a_user_edited_agent_is_kept_and_nothing_is_trashed(tmp_path: Path) -> None:
    from trw_mcp.channels.claude_code._explorer_subagent import withdraw_cc05_subagent_if_unedited

    root, agent, body = _agent(tmp_path)
    agent.write_bytes(body + b"\nmy note\n")
    assert withdraw_cc05_subagent_if_unedited(root, None) is False
    assert agent.read_bytes() == body + b"\nmy note\n"
    assert not (root / ".trw" / "trash").exists()


def test_a_git_clean_edited_agent_is_deleted_and_names_the_restore(tmp_path: Path) -> None:
    import subprocess

    from trw_mcp.channels.claude_code._explorer_subagent import (
        EXPLORER_AGENT_RELPATH,
        withdraw_cc05_subagent_if_unedited,
    )

    root, agent, body = _agent(tmp_path)
    agent.write_bytes(body + b"\ncommitted note\n")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    result: dict[str, list[str]] = {}
    assert withdraw_cc05_subagent_if_unedited(root, None, result) is True
    assert not agent.exists()
    rel = EXPLORER_AGENT_RELPATH
    assert (
        f"{rel}: removed; your version differs from TRW's but is committed in git (restore: git restore -- {rel})"
        in result["warnings"]
    )
    assert not (root / ".trw" / "trash").exists()


def test_the_channel_installer_records_the_deletion_as_retired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The uncommitted-changes guard skips only paths reported as retired; without it a real repo restores it."""
    from trw_mcp.bootstrap import _claude_code_distill_channels as channels
    from trw_mcp.channels.claude_code._explorer_subagent import EXPLORER_AGENT_RELPATH

    root, agent, _body = _agent(tmp_path)
    monkeypatch.setattr("trw_mcp.bootstrap._distill_entitlement.distill_artifacts_entitled", lambda **_k: False)
    result = channels.install_claude_code_distill_channels(root)
    assert not agent.exists()
    assert EXPLORER_AGENT_RELPATH in result.get("retired", [])


def test_withdraw_uses_the_callers_pre_run_manifest_not_a_rewritten_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand edit that the on-disk manifest already recorded must not count as TRW's when the caller's
    pre-run baseline says otherwise."""
    import hashlib

    from trw_mcp.bootstrap import _claude_code_distill_channels as channels
    from trw_mcp.channels.claude_code._explorer_subagent import EXPLORER_AGENT_RELPATH

    root, agent, body = _agent(tmp_path)
    edited = body + b"\nmy note\n"
    agent.write_bytes(edited)
    rewritten = {EXPLORER_AGENT_RELPATH: hashlib.sha256(edited).hexdigest()}
    monkeypatch.setattr("trw_mcp.bootstrap._distill_entitlement.distill_artifacts_entitled", lambda **_k: False)
    monkeypatch.setattr("trw_mcp.bootstrap._version_manifest._read_manifest", lambda _t: {"content_hashes": rewritten})
    result = channels.install_claude_code_distill_channels(root, manifest_hashes={})
    assert agent.read_bytes() == edited
    assert "retired" not in result
    assert any(w.endswith(f"rm {EXPLORER_AGENT_RELPATH}") for w in result["warnings"])
