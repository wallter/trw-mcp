"""EVIDENCE-DELETION-POLICY (b): an unpinned session that deletes its change log after an edit cannot deliver.

The witness is an in-memory, per-session snapshot outside ``.trw/`` (git HEAD plus digests of the paths already
dirty or untracked at ``trw_session_start``). Real git checkouts throughout: the witness's whole claim is about
what git and the filesystem say.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from trw_mcp.state import _session_change_witness as witness

_SESSION = "sess-witness"


@pytest.fixture(autouse=True)
def _fresh_snapshots(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # A client whose hook records file changes (FR10); the Codex tests below override it.
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "claude-code")
    witness._reset_for_tests()
    yield
    witness._reset_for_tests()


def _git(root: Path, *args: str) -> None:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}  # fmt: skip
    subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)


def _checkout(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".trw" / "context").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "src/a.py")
    _git(root, "commit", "-q", "-m", "base")
    (root / "swarm-notes.md").write_text("pre-existing untracked\n", encoding="utf-8")  # already dirty at start
    return root


def _count(root: Path) -> witness.WitnessCount:
    return witness.changed_since_snapshot(_SESSION, root)


def test_nothing_changed_counts_zero_even_with_pre_existing_untracked_files(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    witness.record_snapshot(_SESSION, root)
    assert _count(root) == witness.WitnessCount("counted", 0, _count(root).reason)


def test_an_edit_counts_and_survives_a_commit(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    witness.record_snapshot(_SESSION, root)
    (root / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")
    assert _count(root).count == 1
    _git(root, "commit", "-q", "-am", "edit")  # a clean tree after committing is still a changed session
    assert _count(root).count == 1


def test_a_pre_existing_untracked_file_counts_only_once_modified(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    witness.record_snapshot(_SESSION, root)
    (root / "swarm-notes.md").write_text("pre-existing untracked\n", encoding="utf-8")  # rewritten, same bytes
    assert _count(root).count == 0
    (root / "swarm-notes.md").write_text("edited\n", encoding="utf-8")
    assert _count(root).count == 1


def test_trw_state_and_an_earlier_sessions_commit_are_not_this_sessions_changes(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    (root / "src" / "a.py").write_text("A = 3\n", encoding="utf-8")
    _git(root, "commit", "-q", "-am", "someone else, before this session")
    witness.record_snapshot(_SESSION, root)
    (root / ".trw" / "context" / "session-events.jsonl").write_text("{}\n", encoding="utf-8")
    assert _count(root).count == 0


def test_a_new_file_and_a_deleted_file_both_count(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    witness.record_snapshot(_SESSION, root)
    (root / "src" / "new.py").write_text("B = 1\n", encoding="utf-8")
    (root / "src" / "a.py").unlink()
    assert _count(root).count == 2


def test_no_snapshot_and_no_git_are_named_not_zero(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    missing = _count(root)
    assert missing.status == "no_snapshot" and missing.count is None and "restarted" in missing.reason
    plain = tmp_path / "plain"
    plain.mkdir()
    witness.record_snapshot(_SESSION, plain)
    assert witness.changed_since_snapshot(_SESSION, plain).status == "no_git"


def test_the_first_session_start_is_the_baseline(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    witness.record_snapshot(_SESSION, root)
    (root / "src" / "a.py").write_text("A = 4\n", encoding="utf-8")
    witness.record_snapshot(_SESSION, root)  # a second session_start must not launder the edit
    assert _count(root).count == 1


# --- the FIX-140-FR04 counter and the deliver gate ---------------------------------------------------------------


def _counter(root: Path) -> int | None:
    from trw_mcp.tools._delivery_event_checks import unpinned_session_changed_files

    return unpinned_session_changed_files(root / ".trw", _SESSION)


def test_deleting_the_session_stream_after_an_edit_is_still_a_change(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    witness.record_snapshot(_SESSION, root)
    (root / "src" / "a.py").write_text("A = 5\n", encoding="utf-8")
    stream = root / ".trw" / "context" / "session-events.jsonl"
    stream.write_text(f'{{"event": "file_modified", "file": "src/a.py", "session_id": "{_SESSION}"}}\n')
    assert _counter(root) == 1
    stream.unlink()
    assert _counter(root) == 1


def test_a_stream_without_this_sessions_records_falls_back_to_the_witness(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    witness.record_snapshot(_SESSION, root)
    (root / "src" / "a.py").write_text("A = 6\n", encoding="utf-8")
    (root / ".trw" / "context" / "session-events.jsonl").write_text(
        '{"event": "file_modified", "file": "src/b.py", "session_id": "someone-else"}\n'
    )
    assert _counter(root) == 1


def test_no_snapshot_makes_the_count_uncomputable_and_no_git_keeps_zero(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    assert _counter(root) is None
    plain = tmp_path / "plain"
    (plain / ".trw" / "context").mkdir(parents=True)
    witness.record_snapshot(_SESSION, plain)
    assert _counter(plain) == 0


def test_the_unpinned_gate_blocks_a_deleted_stream_and_names_a_lost_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools._deliver_gate_selfcomputed import evaluate_build_authority

    root = _checkout(tmp_path)
    monkeypatch.setenv("TRW_SESSION_ID", _SESSION)
    monkeypatch.setattr(
        "trw_mcp.tools._deliver_gate_mode.resolve_gate_mode_with_source", lambda _t: ("block_coding", False)
    )
    witness.record_snapshot(_SESSION, root)
    (root / "src" / "a.py").write_text("A = 7\n", encoding="utf-8")
    results: dict[str, Any] = {}
    assert evaluate_build_authority(cast("Any", results), [], None, root / ".trw", False, "") is True
    assert "modifications to 1 file" in results["delivery_blocked"]

    witness._reset_for_tests()  # the server restarted
    results = {}
    assert evaluate_build_authority(cast("Any", results), [], None, root / ".trw", False, "") is True
    assert "server restarted" in results["delivery_blocked"]


def test_trw_session_start_takes_the_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests._ceremony_helpers import make_ceremony_server

    root = _checkout(tmp_path)
    tools = make_ceremony_server(monkeypatch, root)
    monkeypatch.setenv("TRW_SESSION_ID", _SESSION)
    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", lambda: root / ".trw")
    tools["trw_session_start"].fn()
    (root / "src" / "a.py").write_text("A = 8\n", encoding="utf-8")
    assert _count(root).count == 1


def test_a_tracked_trw_file_changing_is_not_a_code_change(tmp_path: Path) -> None:
    """The diff path, not only the status scan, excludes .trw/ (the main checkout tracks some .trw files)."""
    root = _checkout(tmp_path)
    (root / ".trw" / "config.yaml").write_text("a: 1\n", encoding="utf-8")
    _git(root, "add", ".trw/config.yaml")
    _git(root, "commit", "-q", "-m", "tracked trw file")
    witness.record_snapshot(_SESSION, root)
    (root / ".trw" / "config.yaml").write_text("a: 2\n", encoding="utf-8")
    _git(root, "commit", "-q", "-am", "trw state only")
    assert _count(root).count == 0


# --- lead conditions + W5 addition -------------------------------------------------------------------------------


def _gate(root: Path) -> tuple[bool, dict[str, Any]]:
    from trw_mcp.tools._deliver_gate_selfcomputed import evaluate_build_authority

    results: dict[str, Any] = {}
    blocked = evaluate_build_authority(cast("Any", results), [], None, root / ".trw", False, "")
    return blocked, results


def test_a_server_restart_after_an_edit_blocks_with_exactly_the_two_remedies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Canary hot swaps restart the server: a drained-then-successor process has no snapshot."""
    root = _checkout(tmp_path)
    monkeypatch.setenv("TRW_SESSION_ID", _SESSION)
    monkeypatch.setattr(
        "trw_mcp.tools._deliver_gate_mode.resolve_gate_mode_with_source", lambda _t: ("block_coding", False)
    )
    witness.record_snapshot(_SESSION, root)
    (root / "src" / "a.py").write_text("A = 9\n", encoding="utf-8")
    witness._reset_for_tests()  # the successor server process
    blocked, results = _gate(root)
    reason = results["delivery_blocked"]
    assert blocked is True and witness.NO_SNAPSHOT_REASON in reason
    assert "run trw_build_check, or record an acceptable-failure" in reason
    assert "call trw_session_start" not in reason and "re-run trw_session_start" not in reason


def test_a_codex_bash_edit_with_no_hook_record_is_caught(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """W5: Codex Bash edits bypass every hook. No TRW_SESSION_ID (the key is the server's own process id, read
    unscoped, as for codex): the witness alone sees the edit and the gate blocks."""
    from trw_mcp.state._paths import resolve_pin_key

    root = _checkout(tmp_path)
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "codex")  # no registered change-evidence writer (FR10)
    monkeypatch.setattr(
        "trw_mcp.tools._deliver_gate_mode.resolve_gate_mode_with_source", lambda _t: ("block_coding", False)
    )
    witness.record_snapshot(resolve_pin_key(None), root)
    (root / "src" / "a.py").write_text("A = 10\n", encoding="utf-8")  # a Bash sed: no hook record anywhere
    blocked, results = _gate(root)
    assert blocked is True and "1 file" in results["delivery_blocked"]


def test_hook_records_and_the_witness_are_unioned(tmp_path: Path) -> None:
    """One recorded apply_patch edit must not hide a second, Bash-made edit (and the same file counts once)."""
    root = _checkout(tmp_path)
    witness.record_snapshot(_SESSION, root)
    (root / "src" / "a.py").write_text("A = 11\n", encoding="utf-8")  # recorded by the hook
    (root / "src" / "b.py").write_text("B = 1\n", encoding="utf-8")  # Bash: not recorded
    (root / ".trw" / "context" / "session-events.jsonl").write_text(
        f'{{"event": "file_modified", "file": "src/a.py", "session_id": "{_SESSION}"}}\n'
    )
    assert _counter(root) == 2


def test_an_unborn_baseline_still_sees_a_first_commit(tmp_path: Path) -> None:
    root = tmp_path / "unborn"
    (root / ".trw").mkdir(parents=True)
    _git(root, "init", "-q")
    witness.record_snapshot(_SESSION, root)
    (root / "new.py").write_text("N = 1\n", encoding="utf-8")
    _git(root, "add", "new.py")
    _git(root, "commit", "-q", "-m", "first")
    assert _count(root).count == 1


def test_a_fifo_in_the_checkout_never_blocks_the_snapshot(tmp_path: Path) -> None:
    import os

    root = _checkout(tmp_path)
    (root / "pipe").write_text("tracked\n", encoding="utf-8")
    _git(root, "add", "pipe")
    _git(root, "commit", "-q", "-m", "tracked file")
    (root / "pipe").unlink()
    os.mkfifo(root / "pipe")  # a tracked path replaced by a FIFO: git reports it as changed
    witness.record_snapshot(_SESSION, root)  # read_bytes() of the FIFO would block forever
    assert _count(root).count == 0


def test_the_count_is_a_union_not_the_larger_of_the_two(tmp_path: Path) -> None:
    """A hook-recorded edit later reverted (only the hook knows it) plus a Bash edit (only the witness knows it)."""
    root = _checkout(tmp_path)
    witness.record_snapshot(_SESSION, root)
    (root / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")  # edited and restored: same bytes as the snapshot
    (root / "src" / "b.py").write_text("B = 2\n", encoding="utf-8")  # Bash: unrecorded
    (root / ".trw" / "context" / "session-events.jsonl").write_text(
        f'{{"event": "file_modified", "file": "src/a.py", "session_id": "{_SESSION}"}}\n'
    )
    assert _counter(root) == 2


def test_a_codex_session_with_a_snapshot_and_no_edits_is_counted_not_uncomputable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR10's no-writer backstop yields to real evidence: a clean witnessed count is 0, not None."""
    root = _checkout(tmp_path)
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "codex")
    witness.record_snapshot(_SESSION, root)
    assert _counter(root) == 0
    witness._reset_for_tests()  # a restart: no evidence at all, W2's backstop stands
    assert _counter(root) is None


def test_a_restarted_codex_session_names_the_lost_snapshot_not_the_missing_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The restart is the cause to fix (build check or AF record); 'no hook' would send the agent the wrong way."""
    root = _checkout(tmp_path)
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "codex")
    monkeypatch.setattr(
        "trw_mcp.tools._deliver_gate_mode.resolve_gate_mode_with_source", lambda _t: ("block_coding", False)
    )
    blocked, results = _gate(root)  # no snapshot in this (successor) process
    assert blocked is True and witness.NO_SNAPSHOT_REASON in results["delivery_blocked"]
