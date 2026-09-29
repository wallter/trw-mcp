"""FS-LINT row 2: the CC-05 explorer-agent withdraw goes through ``remove_if_hash`` (HB-2).

``withdraw_cc05_subagent_if_unedited`` used to hash the agent and then ``unlink`` it, so an edit saved in between
(or a write through a held fd) was destroyed. It now shares ``cc05_explorer_user_edited`` with the installer for
the edited verdict, captures the file into ``.trw/trash``, and re-verifies it there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._fs_hazards import open_fd_writer


def _agent(tmp_path: Path) -> tuple[Path, Path, bytes]:
    from trw_mcp.channels.claude_code._explorer_subagent import EXPLORER_AGENT_RELPATH, get_explorer_agent_content

    root = tmp_path / "proj"
    agent = root / EXPLORER_AGENT_RELPATH
    agent.parent.mkdir(parents=True)
    body = get_explorer_agent_content().encode("utf-8")
    agent.write_bytes(body)
    return root, agent, body


def _trash(root: Path) -> list[bytes]:
    trash = root / ".trw" / "trash"
    return sorted(p.read_bytes() for p in trash.glob("*/data")) if trash.is_dir() else []


def test_an_unedited_agent_moves_to_trash(tmp_path: Path) -> None:
    from trw_mcp.channels.claude_code._explorer_subagent import withdraw_cc05_subagent_if_unedited

    root, agent, body = _agent(tmp_path)
    assert withdraw_cc05_subagent_if_unedited(root, None) is True
    assert not agent.exists()
    assert _trash(root) == [body]


def test_a_user_edited_agent_is_kept_and_nothing_is_trashed(tmp_path: Path) -> None:
    from trw_mcp.channels.claude_code._explorer_subagent import withdraw_cc05_subagent_if_unedited

    root, agent, body = _agent(tmp_path)
    agent.write_bytes(body + b"\nmy note\n")
    assert withdraw_cc05_subagent_if_unedited(root, None) is False
    assert agent.read_bytes() == body + b"\nmy note\n"
    assert _trash(root) == []


def test_an_edit_after_the_verdict_is_put_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Red on the old withdraw: it hashed, then unlinked whatever the name held at the act."""
    from trw_mcp.bootstrap import _safe_remove
    from trw_mcp.channels.claude_code._explorer_subagent import withdraw_cc05_subagent_if_unedited

    root, agent, body = _agent(tmp_path)
    real = _safe_remove.remove_if_hash

    def edit_then_remove(path: Path, root_: Path, expected: str, **kw: object):  # type: ignore[no-untyped-def]
        path.write_bytes(body + b"\nedited at the act\n")
        return real(path, root_, expected, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(_safe_remove, "remove_if_hash", edit_then_remove)
    assert withdraw_cc05_subagent_if_unedited(root, None) is False
    assert agent.read_bytes() == body + b"\nedited at the act\n"


def test_a_late_write_through_a_held_fd_lands_in_trash(tmp_path: Path) -> None:
    from trw_mcp.channels.claude_code._explorer_subagent import withdraw_cc05_subagent_if_unedited

    root, agent, _body = _agent(tmp_path)
    with open_fd_writer(agent) as writer:
        assert withdraw_cc05_subagent_if_unedited(root, None) is True
        writer.write(b"late write\n")
    assert _trash(root) == [b"late write\n"]


def test_the_channel_installer_records_the_capture_as_trashed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The uncommitted-changes guard skips only paths reported as trashed; without it a real repo restores it."""
    from trw_mcp.bootstrap import _claude_code_distill_channels as channels
    from trw_mcp.channels.claude_code._explorer_subagent import EXPLORER_AGENT_RELPATH

    root, agent, _body = _agent(tmp_path)
    monkeypatch.setattr("trw_mcp.bootstrap._distill_entitlement.distill_artifacts_entitled", lambda **_k: False)
    result = channels.install_claude_code_distill_channels(root)
    assert not agent.exists()
    assert EXPLORER_AGENT_RELPATH in result.get("trashed", [])


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
    assert "trashed" not in result


def test_an_unreadable_agent_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.channels.claude_code import _explorer_subagent as sub

    root, agent, body = _agent(tmp_path)
    monkeypatch.setattr(sub, "cc05_explorer_user_edited", lambda *_a: False)
    real = Path.read_bytes

    def denied(self: Path) -> bytes:
        if self == agent:
            raise PermissionError(13, "denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", denied)
    assert sub.withdraw_cc05_subagent_if_unedited(root, None) is False
    assert agent.exists()


def test_an_edit_between_the_verdict_and_the_read_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """codex r1: the re-read hash must itself be proven, or an edit after the verdict would be withdrawn."""
    from trw_mcp.channels.claude_code import _explorer_subagent as sub

    root, agent, body = _agent(tmp_path)
    real = sub.cc05_explorer_user_edited

    def verdict_then_edit(repo_root: Path, hashes: dict[str, str] | None) -> bool:
        verdict = real(repo_root, hashes)
        agent.write_bytes(body + b"\nedited after the verdict\n")
        return verdict

    monkeypatch.setattr(sub, "cc05_explorer_user_edited", verdict_then_edit)
    assert sub.withdraw_cc05_subagent_if_unedited(root, None) is False
    assert agent.read_bytes() == body + b"\nedited after the verdict\n"
    assert _trash(root) == []
