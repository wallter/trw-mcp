"""Transition attribution through the public gate in real temporary repositories."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools import _prd_transition_gate as gate

PD = "docs/requirements-aare-f/prds"
IDS = ["PRD-CORE-1", "PRD-CORE-2"]


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def flip(repo: Path, number: int = 1) -> None:
    path = repo / PD / f"PRD-CORE-{number}.md"
    path.write_text(path.read_text().replace("status: draft", "status: implemented"))


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key, value in {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_EDITOR": "true",
        "GIT_SEQUENCE_EDITOR": "true",
    }.items():
        monkeypatch.setenv(key, value)
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / PD).mkdir(parents=True)
    for pid in IDS:
        (root / PD / f"{pid}.md").write_text(f"---\nid: {pid}\nstatus: draft\n---\nbody\n")
    (root / "src.py").write_text("x = 1\n")
    (root / ".gitignore").write_text(".trw/\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    monkeypatch.chdir(root)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(root))
    monkeypatch.setattr(
        "trw_mcp.models.config.get_config",
        lambda: TRWConfig(prd_transition_gate="block", deliver_gate_mode="block_coding"),
    )
    monkeypatch.setattr(
        gate, "evaluate_prd_coherence", lambda *a, **k: gate.CoherenceReport(blocking=[gate.MISSING_BUILD])
    )
    return root


def check(
    repo: Path,
    base: str,
    expected: list[str],
    events: tuple[str, ...] = (),
    *,
    start_yaml: str = "",
    run_name: str = "run",
    first_event: str = "",
) -> None:
    run = repo / ".trw/runs/task" / run_name
    (run / "meta").mkdir(parents=True, exist_ok=True)
    # Deliberately no session id: committed attribution must not depend on one.
    (run / "meta/run.yaml").write_text(f"task_type: coding\nbase_commit: {base}\n" + start_yaml)
    (run / "meta/events.jsonl").write_text(
        first_event + "".join(json.dumps({"event": "file_modified", "file": path}) + "\n" for path in events)
    )
    outcome = gate.evaluate_transition_gate(run)
    assert outcome.should_block is bool(expected)
    assert sorted(outcome.prd_ids) == sorted(expected)


def upstream(repo: Path, *, flips: bool = True, numbers: tuple[int, ...] = (1, 2)) -> Path:
    # A separate worktree is essential: same-worktree branch commits are owned
    # under the requested HEAD-reflog rule, even if the branch is named upstream.
    other = repo.parent / "upstream"
    git(repo, "worktree", "add", "-qb", "upstream", str(other))
    if flips:
        for number in numbers:
            flip(other, number)
    (other / "src.py").write_text("upstream = 1\n")
    git(other, "commit", "-qam", "upstream changes")
    return other


@pytest.mark.parametrize("separate_code_commit", [False, True])
def test_shell_committed_flip(repo: Path, separate_code_commit: bool) -> None:
    base = git(repo, "rev-parse", "HEAD")
    if separate_code_commit:
        (repo / "src.py").write_text("local = 1\n")
        git(repo, "commit", "-qam", "code")
    flip(repo)
    git(repo, "commit", "-qam", "shell flip")
    check(repo, base, IDS[:1], ("src.py",) if separate_code_commit else ())


@pytest.mark.parametrize("has_common_ancestor", [False, True])
def test_amend_base_flip(repo: Path, has_common_ancestor: bool) -> None:
    if has_common_ancestor:
        (repo / "src.py").write_text("local = 1\n")
        git(repo, "commit", "-qam", "second base")
    base = git(repo, "rev-parse", "HEAD")
    flip(repo)
    git(repo, "commit", "-qa", "--amend", "-m", "amended base")
    check(repo, base, IDS[:1])


@pytest.mark.parametrize("movement", ["detach", "ff", "reset"])
def test_upstream_head_movement(repo: Path, movement: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    upstream(repo)
    if movement == "detach":
        git(repo, "checkout", "-q", "--detach", "upstream")
    elif movement == "reset":
        git(repo, "reset", "--hard", "upstream")
    else:
        git(repo, "merge", "-q", "--ff-only", "upstream")
    (repo / "src.py").write_text("session = 1\n")
    check(repo, base, [], ("src.py",))


@pytest.mark.parametrize("committed", [False, True])
def test_prd_body_edit_after_upstream_flip(repo: Path, committed: bool) -> None:
    base = git(repo, "rev-parse", "HEAD")
    upstream(repo)
    git(repo, "merge", "-q", "--ff-only", "upstream")
    path = repo / PD / f"{IDS[0]}.md"
    path.write_text(path.read_text() + "session body\n")
    if committed:
        git(repo, "commit", "-qam", "body only")
    check(repo, base, [], (f"{PD}/{IDS[0]}.md",))


@pytest.mark.parametrize("merge_flips", [False, True])
def test_non_fast_forward_merge(repo: Path, merge_flips: bool) -> None:
    base = git(repo, "rev-parse", "HEAD")
    upstream(repo, flips=not merge_flips)
    (repo / "local.py").write_text("local = 1\n")
    git(repo, "add", "local.py")
    git(repo, "commit", "-qm", "local")
    if merge_flips:
        git(repo, "merge", "-q", "--no-ff", "--no-commit", "upstream")
        flip(repo)
        git(repo, "commit", "-qam", "resolved merge flips status")
        assert "commit (merge):" in git(repo, "reflog", "show", "--format=%gs", "HEAD")
    else:
        git(repo, "merge", "-q", "--no-ff", "-m", "merge upstream", "upstream")
    check(repo, base, IDS[:1] if merge_flips else [], ("src.py",))


def test_resolved_merge_inherits_parent_status(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    upstream(repo)
    (repo / "local.py").write_text("local = 1\n")
    git(repo, "add", "local.py")
    git(repo, "commit", "-qm", "local")
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "upstream")
    git(repo, "commit", "-qm", "resolved merge inherits status")
    check(repo, base, [])


def test_pull_rebase_replays_flip(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    other = upstream(repo, flips=False)
    flip(repo)
    git(repo, "commit", "-qam", "session flip")
    git(repo, "remote", "add", "origin", str(other))
    git(repo, "config", "branch.main.remote", "origin")
    git(repo, "config", "branch.main.merge", "refs/heads/upstream")
    git(repo, "pull", "--rebase")
    assert "pull --rebase (pick):" in git(repo, "reflog", "show", "--format=%gs", "HEAD")
    check(repo, base, IDS[:1])


@pytest.mark.parametrize("coverage", ["deleted", "disabled", "base_absent", "old_sha_only"])
def test_reflog_coverage(repo: Path, coverage: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    upstream(repo)
    log = repo / git(repo, "rev-parse", "--git-path", "logs/HEAD")
    if coverage == "disabled":
        log.unlink()
        git(repo, "config", "core.logAllRefUpdates", "false")
    git(repo, "merge", "-q", "--ff-only", "upstream")
    if coverage == "deleted":
        log.unlink()
    elif coverage in {"base_absent", "old_sha_only"}:
        last = log.read_text().splitlines()[-1]
        if coverage == "base_absent":
            last = last.replace(base, "0" * 40)
        log.write_text(last + "\n")
    check(repo, base, [] if coverage == "old_sha_only" else IDS)


@pytest.mark.parametrize("state", ["working", "staged", "staged_reverted"])
def test_uncommitted_flip(repo: Path, state: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    flip(repo)
    if state != "working":
        git(repo, "add", PD)
    if state == "staged_reverted":
        path = repo / PD / f"{IDS[0]}.md"
        path.write_text(path.read_text().replace("status: implemented", "status: draft"))
    check(repo, base, IDS[:1])


@pytest.mark.parametrize("event_state", ["foreign_unknown", "malformed", "unreadable", "symlink"])
def test_events_cannot_change_attribution(repo: Path, event_state: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    upstream(repo)
    git(repo, "merge", "-q", "--ff-only", "upstream")
    stream = repo / ".trw/context/session-events.jsonl"
    stream.parent.mkdir(parents=True)
    if event_state == "unreadable":
        stream.mkdir()
    elif event_state == "malformed":
        stream.write_text("{bad json\n")
    else:
        stream.write_text(json.dumps({"event": "change_evidence_unknown", "session_id": "OTHER"}) + "\n")
    check(repo, base, [])
    # A local commit still counts, independent of event stream or path spelling.
    path = repo / PD / f"{IDS[0]}.md"
    path.write_text(path.read_text().replace("status: implemented", "status: draft"))
    git(repo, "commit", "-qam", "reopen")
    flip(repo)
    git(repo, "commit", "-qam", "local flip")
    link = repo.parent / "link"
    link.symlink_to(repo, target_is_directory=True)
    check(repo, base, IDS[:1], (str(link / PD / f"{IDS[0]}.md"),))


@pytest.mark.parametrize("state", ["not_a_repository", "no_commit_yet"])
def test_a_project_without_git_history_has_nothing_to_certify(repo: Path, state: str) -> None:
    """A project that is not a repository, or has no commit, was never blocked by this check and is not now.

    Nine deliver tests that run in plain temporary projects failed when a failed `git diff` became a block.
    """
    import shutil

    base = git(repo, "rev-parse", "HEAD")
    flip(repo)
    shutil.rmtree(repo / ".git")
    if state == "no_commit_yet":
        git(repo, "init", "-q", "-b", "main")
    check(repo, base, [])


@pytest.mark.parametrize("failure", ["git_missing", "history", "timeout", "decode"])
def test_git_fault_keeps_visible_changes_and_never_blocks_blind(
    repo: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from trw_mcp.tools import _prd_transition_attribution as attribution

    base = git(repo, "rev-parse", "HEAD")
    flip(repo)
    git(repo, "commit", "-qam", "flip")
    if failure == "git_missing":
        monkeypatch.setenv("PATH", "/nonexistent")
    else:
        original = attribution._git

        def broken(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
            if args[0] in {"log", "rev-list"}:
                if failure == "timeout":
                    raise subprocess.TimeoutExpired("git", 30)
                if failure == "decode":
                    raise UnicodeError("unreadable history")
                return subprocess.CompletedProcess(args, 128, "", "history unavailable")
            return original(root, *args)

        monkeypatch.setattr(attribution, "_git", broken)
    run = repo / ".trw/runs/task/run"
    (run / "meta").mkdir(parents=True)
    (run / "meta/run.yaml").write_text(f"task_type: coding\nbase_commit: {base}\n")
    outcome = gate.evaluate_transition_gate(run)
    if failure == "git_missing":  # nothing could be read: not evaluated, said so, never a hard block (NFR02)
        assert not outcome.should_block and outcome.prd_ids == []
        assert "not evaluated" in outcome.warning
    else:  # the status change is visible; only its ownership is unknown, so it counts
        assert outcome.should_block
        assert outcome.prd_ids == IDS[:1]


def test_linked_worktree_uses_own_head_reflog(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = git(repo, "rev-parse", "HEAD")
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-qb", "linked", str(linked))
    # The main worktree's commit is not an owned commit in the linked checkout.
    flip(repo, 2)
    git(repo, "commit", "-qam", "other worktree flip")
    git(linked, "merge", "-q", "--ff-only", "main")
    flip(linked, 1)
    git(linked, "commit", "-qam", "linked worktree flip")
    monkeypatch.chdir(linked)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(linked))
    check(linked, base, IDS[:1])


def test_a_history_fault_with_no_status_change_has_nothing_to_certify(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools import _prd_transition_attribution as attribution

    base = git(repo, "rev-parse", "HEAD")
    original = attribution._git

    def broken(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args[0] == "log":
            raise subprocess.TimeoutExpired("git", 30)
        return original(root, *args)

    monkeypatch.setattr(attribution, "_git", broken)
    run = repo / ".trw/runs/task/run"
    (run / "meta").mkdir(parents=True)
    (run / "meta/run.yaml").write_text(f"task_type: coding\nbase_commit: {base}\n")
    outcome = gate.evaluate_transition_gate(run)
    assert not outcome.should_block
    assert outcome.prd_ids == []


def test_an_unreadable_status_diff_is_reported_as_not_evaluated_and_does_not_block(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    run = repo / ".trw/runs/task/run"
    (run / "meta").mkdir(parents=True)
    (run / "meta/run.yaml").write_text(f"task_type: coding\nbase_commit: {base}\n")

    def unreadable(base: str | None = None) -> str:
        raise UnicodeError("git output cannot be decoded")

    monkeypatch.setattr(gate, "_prd_status_diff", unreadable)
    outcome = gate.evaluate_transition_gate(run)
    assert not outcome.should_block
    assert outcome.prd_ids == []
    assert "not evaluated" in outcome.warning and "unavailable" in outcome.message


@pytest.mark.parametrize("method", ["PR", "RA", "RM", "E12", "F1"])
def test_replayed_commits(repo: Path, method: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    upstream(repo, numbers=(2,))
    if method != "F1":
        flip(repo)
    if method in {"E12", "F1"}:
        (repo / "local.py").write_text("local\n")
    if method == "E12":
        (repo / "src.py").write_text("conflict\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "local")
    if method == "F1":
        (repo / "local.py").write_text("another local commit\n")
        git(repo, "commit", "-qam", "second local")
    if method == "PR":
        git(repo, "pull", "-q", "--rebase", ".", "upstream")
        assert "pull -q --rebase . upstream (pick):" in git(repo, "reflog", "--format=%gs")
    elif method == "E12":
        result = subprocess.run(["git", "rebase", "upstream"], cwd=repo, capture_output=True)
        assert result.returncode == 1
        (repo / "src.py").write_text("resolved\n")
        git(repo, "add", ".")
        git(repo, "rebase", "--continue")
    else:
        git(repo, "rebase", "-q", "--apply" if method == "RA" else "--merge", "upstream")
    check(repo, base, [] if method == "F1" else IDS[:1])


def test_custom_reflog_action_E4(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = git(repo, "rev-parse", "HEAD")
    flip(repo)
    monkeypatch.setenv("GIT_REFLOG_ACTION", "sync")
    git(repo, "commit", "-qam", "custom action")
    assert git(repo, "reflog", "-1", "--format=%gs").startswith("sync:")
    check(repo, base, IDS[:1])


def test_cherry_pick_fast_forward_is_a_movement_CF(repo: Path) -> None:
    """`cherry-pick --ff` onto the picked commit's own parent only moves HEAD; git writes `fast-forward`."""
    base = git(repo, "rev-parse", "HEAD")
    upstream(repo)
    git(repo, "cherry-pick", "--ff", "upstream")
    assert git(repo, "reflog", "-1", "--format=%gs") == "cherry-pick: fast-forward"
    check(repo, base, [])


@pytest.mark.parametrize("conflict", [False, True], ids=["E5b", "E5"])
def test_cherry_pick(repo: Path, conflict: bool) -> None:
    base = git(repo, "rev-parse", "HEAD")
    upstream(repo, numbers=(1,))
    if conflict:
        (repo / "src.py").write_text("conflict\n")
        git(repo, "commit", "-qam", "local")
        result = subprocess.run(["git", "cherry-pick", "upstream"], cwd=repo, capture_output=True)
        assert result.returncode == 1
        (repo / "src.py").write_text("resolved\n")
        git(repo, "add", ".")
        git(repo, "cherry-pick", "--continue")
        assert git(repo, "reflog", "-1", "--format=%gs").startswith("commit (cherry-pick):")
    else:
        git(repo, "cherry-pick", "upstream")
    check(repo, base, IDS[:1])


@pytest.mark.parametrize("method", ["M1", "M2", "E11", "B4b"])
def test_merge_attributes_only_handmade_flip(repo: Path, method: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    upstream(repo, numbers=(1,))
    path = repo / PD / f"{IDS[1]}.md"
    path.write_text(path.read_text() + "local body\n")
    if method == "E11":
        (repo / "src.py").write_text("conflict\n")
    git(repo, "commit", "-qam", "local")
    if method == "E11":
        result = subprocess.run(["git", "merge", "upstream"], cwd=repo, capture_output=True)
        assert result.returncode == 1
        (repo / "src.py").write_text("resolved\n")
        flip(repo, 2)
        git(repo, "add", ".")
        git(repo, "merge", "--continue")
    elif method == "M1":
        git(repo, "merge", "-q", "--no-ff", "--no-commit", "upstream")
        flip(repo, 2)
        git(repo, "commit", "-qam", "manual merge")
    else:
        git(repo, "merge", "-q", "--no-ff", "-m", "auto merge", "upstream")
        if method == "M2":
            flip(repo, 2)
            git(repo, "commit", "-qa", "--amend", "--no-edit")
    check(repo, base, [] if method == "B4b" else IDS[1:])


@pytest.mark.parametrize("revert_again", [False, True], ids=["E6", "E6b"])
def test_reverted_flip(repo: Path, revert_again: bool) -> None:
    base = git(repo, "rev-parse", "HEAD")
    flip(repo)
    git(repo, "commit", "-qam", "flip")
    git(repo, "revert", "--no-edit", "HEAD")
    if revert_again:
        git(repo, "revert", "--no-edit", "HEAD")
    check(repo, base, IDS[:1] if revert_again else [])


@pytest.mark.parametrize("source", ["created_at", "directory", "event", "missing", "invalid", "naive", "boundary"])
def test_previous_session_flip_F2(repo: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2020-01-01T00:00:00+00:00")
    git(repo, "checkout", "-qb", "old")
    flip(repo)
    git(repo, "commit", "-qam", "earlier session")
    git(repo, "checkout", "-q", "main")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2020-01-03T00:00:00+00:00")
    git(repo, "merge", "-q", "--ff-only", "old")
    kwargs = {}
    if source == "created_at":
        # YAML creation time is authoritative over a later directory/event.
        kwargs = {
            "start_yaml": "created_at: '2020-01-02T00:00:00Z'\n",
            "run_name": "20200104T000000Z-test",
            "first_event": '{"ts":"2020-01-05T00:00:00Z","event":"run_init"}\n',
        }
    elif source == "directory":
        kwargs = {"run_name": "20200102T000000Z-test", "first_event": "{malformed\n"}
    elif source == "event":
        kwargs = {"first_event": '{"ts":"2020-01-02T00:00:00Z","event":"run_init"}\n'}
    elif source in {"invalid", "naive"}:
        kwargs = {
            "start_yaml": "created_at: '" + ("bad" if source == "invalid" else "2020-01-02T00:00:00") + "'\n",
            "first_event": "{malformed\n",
        }
    elif source == "boundary":
        kwargs = {"start_yaml": "created_at: '2020-01-01T00:00:00.500Z'\n"}
    check(repo, base, [] if source in {"created_at", "directory", "event"} else IDS[:1], **kwargs)


@pytest.mark.parametrize("method", ["E1", "E2", "E3", "E3b", "E7", "E9", "E10", "B3", "RL2", "RL3"])
def test_remaining_probe_scenarios(repo: Path, method: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    if method in {"E9", "B3"}:
        upstream(repo, numbers=(1,))
        if method == "B3":
            git(repo, "merge", "-q", "--ff-only", "upstream")
            (repo / "src.py").write_text("session\n")
            git(repo, "commit", "-qam", "same code path as upstream")
        else:
            git(repo, "merge", "-q", "--squash", "upstream")
            git(repo, "commit", "-qm", "squashed")
    else:
        if method == "E2":
            git(repo, "checkout", "-qb", "feat")
        if method in {"E3", "E3b"}:
            (repo / "src.py").write_text("local\n")
            git(repo, "commit", "-qam", "code")
        flip(repo)
        if method == "E7":
            git(repo, "stash", "-q")
            git(repo, "stash", "pop", "-q")
        else:
            message = {"E3": "fixup! code", "E3b": "squash! code"}.get(method, "flip")
            git(repo, "commit", "-qam", message)
            if method == "E1":
                git(repo, "reset", "-q", "--soft", "HEAD~1")
                git(repo, "commit", "-qm", "recommit")
            elif method == "E2":
                git(repo, "checkout", "-q", "main")
                git(repo, "merge", "-q", "--ff-only", "feat")
            elif method in {"E3", "E3b"}:
                git(repo, "rebase", "-q", "-i", "--autosquash", base)
            elif method == "E10":
                git(repo, "switch", "-q", "--detach", base)
                git(repo, "switch", "-q", "main")
            elif method == "RL2":
                git(repo, "config", "core.logAllRefUpdates", "false")
                (repo / git(repo, "rev-parse", "--git-path", "logs/HEAD")).write_text("")
            elif method == "RL3":
                git(repo, "reflog", "expire", "--expire=now", "--all")
    check(repo, base, [] if method == "B3" else IDS[:1])


@pytest.mark.parametrize("movement", ["detach", "ff"], ids=["INC", "INC2"])
def test_incident_with_long_upstream_history(repo: Path, movement: str) -> None:
    base = git(repo, "rev-parse", "HEAD")
    other = upstream(repo)
    for index in range(299):
        (other / "src.py").write_text(f"upstream = {index + 2}\n")
        git(other, "commit", "-qam", f"upstream {index}")
    assert git(repo, "rev-list", "--count", f"{base}..upstream") == "300"
    if movement == "detach":
        git(repo, "checkout", "-q", "--detach", "upstream")
    else:
        git(repo, "merge", "-q", "--ff-only", "upstream")
        path = repo / PD / f"{IDS[0]}.md"
        content = path.read_text()
        path.write_text(content.replace("implemented", "draft"))
        path.write_text(content)
    (repo / "src.py").write_text("session\n")
    (repo / "other.py").write_text("session\n")
    check(repo, base, [])


@pytest.mark.parametrize("flipped", [False, True])
def test_the_status_diff_is_read_in_the_project_not_in_the_process_directory(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flipped: bool
) -> None:
    """A server started outside the project (a shared server, an exported tree with no git) reads the project.

    Before, the candidate diff ran in the process's working directory: outside a repository git failed and the
    delivery was blocked with nothing changed; inside another repository that repository's requirements were read.
    """
    base = git(repo, "rev-parse", "HEAD")
    if flipped:
        flip(repo)
        git(repo, "commit", "-qam", "flip")
    elsewhere = tmp_path / "not-a-repository"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    run = repo / ".trw/runs/task/run"
    (run / "meta").mkdir(parents=True)
    (run / "meta/run.yaml").write_text(f"task_type: coding\nbase_commit: {base}\n")
    outcome = gate.evaluate_transition_gate(run)
    assert outcome.should_block is flipped
    assert outcome.prd_ids == (IDS[:1] if flipped else [])
    assert not outcome.warning or "not evaluated" not in outcome.warning


@pytest.mark.parametrize("flipped", [False, True])
def test_a_missing_reflog_blocks_only_when_a_status_changed(repo: Path, flipped: bool) -> None:
    """With the HEAD reflog gone, ownership is unknown: a visible change counts, and no change is nothing to certify."""
    base = git(repo, "rev-parse", "HEAD")
    if flipped:
        flip(repo)
        git(repo, "commit", "-qam", "flip")
    (repo / git(repo, "rev-parse", "--git-path", "logs/HEAD")).unlink()
    run = repo / ".trw/runs/task/run"
    (run / "meta").mkdir(parents=True)
    (run / "meta/run.yaml").write_text(f"task_type: coding\nbase_commit: {base}\n")
    outcome = gate.evaluate_transition_gate(run)
    assert outcome.should_block is flipped
    assert outcome.prd_ids == (IDS[:1] if flipped else [])
