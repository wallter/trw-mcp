"""Uninstall keep/refuse branches that protect user bytes, driven at the manifest/skill-dir seam.

Each test names one way TRW could be wrong about ownership (unreadable, undecodable, shared, swapped
after planning, unhashable record) and asserts the user's file is still on disk, untouched, and the
disposition says why.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from trw_mcp.bootstrap import _uninstall_manifest as um
from trw_mcp.bootstrap import _uninstall_skill_dir as sd
from trw_mcp.bootstrap._uninstall_manifest import (
    KeyDisposition,
    apply_removal,
    plan_manifest_removal,
    plan_uncovered_surface,
)

_needs_non_root = pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")
_AGENT_KEY = ".claude/agents/x.md"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _agent(tmp_path: Path, data: bytes = b"# agent\n") -> tuple[Path, Path]:
    root = tmp_path / "proj"
    agent = root / ".claude" / "agents" / "x.md"
    agent.parent.mkdir(parents=True)
    agent.write_bytes(data)
    return root, agent


# ---------------------------------------------------------------------------
# plan_uncovered_surface
# ---------------------------------------------------------------------------


@_needs_non_root
def test_uncovered_surface_that_cannot_be_read_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    surface = root / "REVIEW.md"
    surface.write_bytes(b"bundled bytes")
    monkeypatch.setattr("trw_mcp.bootstrap._managed_client_artifacts.bundled_content_for", lambda _r: b"bundled bytes")
    surface.chmod(0)
    try:
        disposition = plan_uncovered_surface(surface, "REVIEW.md", root)
    finally:
        surface.chmod(0o644)

    assert (disposition.action, disposition.detail) == ("kept", "unreadable; left in place")
    assert surface.read_bytes() == b"bundled bytes"


def test_undecodable_review_md_is_kept_not_treated_as_generated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    surface = root / "REVIEW.md"
    surface.write_bytes(b"\xff\xfe\x00 not utf-8")
    monkeypatch.setattr("trw_mcp.bootstrap._managed_client_artifacts.bundled_content_for", lambda _r: b"something else")

    disposition = plan_uncovered_surface(surface, "REVIEW.md", root)

    assert disposition.action == "kept"
    assert disposition.detail == um._UNCOVERED_NOTE
    assert um._is_generated_review_md(b"\xff\xfe\x00 not utf-8", root) is False


# ---------------------------------------------------------------------------
# plan_manifest_removal
# ---------------------------------------------------------------------------


def test_a_file_still_owned_by_a_remaining_client_is_kept(tmp_path: Path) -> None:
    root, agent = _agent(tmp_path)
    recorded = {_AGENT_KEY: _sha(b"# agent\n")}
    owners = {_AGENT_KEY: ["claude-code", "codex"]}

    plan = plan_manifest_removal(root, ".claude/agents", "codex", recorded, owners, ["claude-code"])
    result: dict[str, list[str]] = {}
    removed, errors = apply_removal(plan, result, root)

    assert [(d.action, d.detail) for d in plan] == [("kept-shared-owner", "owned by ['claude-code']")]
    assert (removed, errors) == (set(), 0)
    assert agent.read_bytes() == b"# agent\n"


def test_the_same_file_is_removable_once_no_other_owner_remains(tmp_path: Path) -> None:
    """Negative twin: with the co-owner gone from the remaining targets the record is removable."""
    root, _agent_path = _agent(tmp_path)
    recorded = {_AGENT_KEY: _sha(b"# agent\n")}
    owners = {_AGENT_KEY: ["claude-code", "codex"]}

    plan = plan_manifest_removal(root, ".claude/agents", "codex", recorded, owners, [])

    assert [d.action for d in plan] == ["remove"]


@_needs_non_root
def test_an_unreadable_recorded_file_is_rejected_not_removed(tmp_path: Path) -> None:
    root, agent = _agent(tmp_path)
    agent.chmod(0)
    try:
        plan = plan_manifest_removal(root, ".claude/agents", "claude-code", {_AGENT_KEY: _sha(b"# agent\n")}, {}, [])
    finally:
        agent.chmod(0o644)

    assert [(d.action, d.detail) for d in plan] == [("rejected-unsafe", "unreadable")]
    assert agent.read_bytes() == b"# agent\n"


def test_a_recorded_path_that_is_a_directory_is_left_alone_with_no_disposition(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    directory = root / ".claude" / "agents" / "x.md"
    directory.mkdir(parents=True)
    (directory / "mine.txt").write_bytes(b"user data")

    plan = plan_manifest_removal(root, ".claude/agents", "claude-code", {_AGENT_KEY: _sha(b"# agent\n")}, {}, [])

    assert plan == []
    assert (directory / "mine.txt").read_bytes() == b"user data"


# ---------------------------------------------------------------------------
# apply_removal act-time refusals
# ---------------------------------------------------------------------------


def test_directory_swapped_for_a_symlink_after_planning_is_refused_and_target_survives(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / ".claude" / "skills").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.md").write_bytes(b"user data")
    swapped = root / ".claude" / "skills" / "demo"
    swapped.symlink_to(outside, target_is_directory=True)
    disposition = KeyDisposition("demo/SKILL.md", swapped, "remove", "", True, _sha(b"# skill\n"))
    result: dict[str, list[str]] = {}

    removed, errors = apply_removal([disposition], result, root)

    assert removed == set()
    assert errors == 1
    assert "symlink" in result["errors"][0] or "refus" in result["errors"][0].lower()
    assert (outside / "keep.md").read_bytes() == b"user data"
    assert swapped.is_symlink()


@pytest.mark.parametrize("bad_hash", ["", "not-a-hash", "A" * 64, "ab" * 10])
def test_a_record_without_a_valid_sha256_never_removes_the_file(tmp_path: Path, bad_hash: str) -> None:
    root, agent = _agent(tmp_path)
    result: dict[str, list[str]] = {}

    removed, errors = apply_removal([KeyDisposition(_AGENT_KEY, agent, "remove", "", False, bad_hash)], result, root)

    assert (removed, errors) == (set(), 1)
    assert result["errors"] == [f"{agent}: kept (no valid recorded hash)"]
    assert agent.read_bytes() == b"# agent\n"


def test_an_oserror_at_the_act_keeps_the_file_and_its_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, agent = _agent(tmp_path)

    def boom(*_a: object, **_k: object) -> None:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(um, "remove_if_hash", boom)
    result: dict[str, list[str]] = {}

    removed, errors = apply_removal(
        [KeyDisposition(_AGENT_KEY, agent, "remove", "", False, _sha(b"# agent\n"))], result, root
    )

    assert removed == set()
    assert errors == 1
    assert "denied" in result["errors"][0]
    assert agent.read_bytes() == b"# agent\n"


# ---------------------------------------------------------------------------
# skill-dir walk
# ---------------------------------------------------------------------------


@pytest.fixture
def skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    data = tmp_path / "data"
    (data / "skills" / "demo" / "refs").mkdir(parents=True)
    (data / "skills" / "demo" / "SKILL.md").write_bytes(b"# demo\n")
    (data / "skills" / "demo" / "refs" / "a.md").write_bytes(b"ref\n")
    monkeypatch.setattr("trw_mcp.bootstrap._utils._DATA_DIR", data)
    root = tmp_path / "project"
    demo = root / ".claude" / "skills" / "demo"
    (demo / "refs").mkdir(parents=True)
    (demo / "SKILL.md").write_bytes(b"# demo\n")
    (demo / "refs" / "a.md").write_bytes(b"ref\n")
    return root, demo


def test_a_child_that_cannot_be_stat_ed_is_kept_and_the_rest_still_removed(
    skill: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, demo = skill
    (demo / "notes.md").write_bytes(b"mine\n")
    real_lstat = os.lstat

    def flaky(path: object, *a: object, **k: object) -> os.stat_result:
        if str(path).endswith("notes.md"):
            raise PermissionError(13, "denied")
        return real_lstat(path, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(sd.os, "lstat", flaky)
    kept, failures, trw_left = sd._remove_skill_dir(demo, root, _sha(b"# demo\n"))

    assert (demo / "notes.md", "unreadable") in kept
    assert failures == []
    assert trw_left is True  # "unreadable" may still be TRW's: the record must stay
    assert (demo / "notes.md").read_bytes() == b"mine\n"


def test_a_subdirectory_that_fails_the_symlink_recheck_is_kept_and_never_entered(
    skill: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, demo = skill
    real_refusal = sd.path_refusal

    def refuse_refs(path: Path, target: Path) -> str | None:
        return "symlink component" if path.name == "refs" else real_refusal(path, target)

    monkeypatch.setattr(sd, "path_refusal", refuse_refs)
    kept, _failures, _left = sd._remove_skill_dir(demo, root, _sha(b"# demo\n"))

    assert (demo / "refs", "symlink") in kept
    assert (demo / "refs" / "a.md").read_bytes() == b"ref\n"  # the directory was never descended into


def test_skill_md_whose_identity_cannot_be_proven_is_kept(
    skill: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, demo = skill
    # UNINSTALL-SKILL-DIR-CAPTURE replaced the identity re-check with remove_if_hash: a capture whose bytes do
    # not re-prove is linked back and reported the same way, so the seam is the capture's own verdict.
    from trw_mcp.bootstrap._trash import Removal

    monkeypatch.setattr(
        sd,
        "remove_if_hash",
        lambda path, *_a, key=None, **_k: Removal(key, path, "kept", path, None, "bytes differ from the recorded hash"),
    )

    kept, failures, trw_left = sd._remove_skill_dir(demo, root, _sha(b"# demo\n"))

    assert (demo / "SKILL.md", "changed during uninstall") in kept
    assert failures == []
    assert trw_left is True
    assert (demo / "SKILL.md").read_bytes() == b"# demo\n"


def test_lexists_strict_counts_an_unstatable_path_as_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(_p: object) -> os.stat_result:
        raise PermissionError(13, "denied")

    with monkeypatch.context() as patch:
        patch.setattr(sd.os, "lstat", denied)
        assert sd._lexists_strict(tmp_path / "anything") is True
    assert sd._lexists_strict(tmp_path / "anything") is False  # genuinely absent


def test_a_read_error_while_hashing_is_unreadable_not_a_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "f"
    target.write_bytes(b"data")

    def bad_read(_fd: int, _n: int) -> bytes:
        raise OSError(5, "I/O error")

    with monkeypatch.context() as patch:
        patch.setattr(sd.os, "read", bad_read)
        assert sd._hash_regular_file(target) == (None, "unreadable")
    assert sd._hash_regular_file(target)[0] == _sha(b"data")


def test_a_file_that_grows_past_the_cap_while_being_read_is_too_large(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "f"
    target.write_bytes(b"abc")
    monkeypatch.setattr(sd, "_MAX_HASH_BYTES", 4)
    monkeypatch.setattr(sd.os, "read", lambda _fd, _n: b"x" * 16)  # returns more than asked: it grew under us

    assert sd._hash_regular_file(target) == (None, sd._TOO_LARGE)
