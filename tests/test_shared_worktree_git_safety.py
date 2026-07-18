"""PRD-CORE-206 shared-index Git-safety integration proof (FR02 empirical basis).

These tests drive a REAL temporary git repository to prove the two factual claims
the FRAMEWORK Git-adapter policy rests on:

1. A path-limited commit (``git commit -- <path>``) EXCLUDES unrelated staged
   paths — another worker's staging in the shared index is not captured.
2. A whole-file path commit commits the COMPLETE current version of the named
   file — including another worker's bytes in the same file. This is why a
   whole-file path commit must fail closed (coordinate an exclusive handoff)
   when a file has mixed ownership: the mechanism cannot commit "only your hunks".

The 2026-07-09 incident (a plain commit consumed 1,755 unrelated staged paths)
motivates claim 1; claim 2 is why same-file mixed ownership is prohibited.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_GIT = shutil.which("git")
pytest_skip_no_git = pytest.mark.skipif(_GIT is None, reason="git not available")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "a2@example.test")
    _git(repo, "config", "user.name", "track-a2")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


@pytest_skip_no_git
def test_path_commit_excludes_unrelated_staged_paths(tmp_path: Path) -> None:
    """A path-limited commit leaves another worker's staged path untouched."""
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("a0\n", encoding="utf-8")
    (repo / "b.txt").write_text("b0\n", encoding="utf-8")
    _git(repo, "add", "a.txt", "b.txt")
    _git(repo, "commit", "-qm", "init")

    # Worker A changes a.txt; a DIFFERENT worker stages b.txt into the shared index.
    (repo / "a.txt").write_text("a-owned-change\n", encoding="utf-8")
    (repo / "b.txt").write_text("b-other-worker\n", encoding="utf-8")
    _git(repo, "add", "a.txt", "b.txt")  # both staged (shared index hazard)

    # Path-limited commit of ONLY a.txt.
    _git(repo, "commit", "-qm", "feat: a only", "--", "a.txt")

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert committed == ["a.txt"], f"path commit captured unrelated paths: {committed}"

    # b.txt's staged change is preserved, NOT swept into the commit.
    still_staged = _git(repo, "diff", "--cached", "--name-only").split()
    assert "b.txt" in still_staged
    assert "b-other-worker" in (repo / "b.txt").read_text(encoding="utf-8")


@pytest_skip_no_git
def test_whole_file_path_commit_captures_all_current_bytes(tmp_path: Path) -> None:
    """A path commit commits the complete current file — foreign bytes included.

    Demonstrates why same-file mixed ownership fails closed: there is no way to
    commit only the owner's hunks of a shared file through a path commit.
    """
    repo = _init_repo(tmp_path)
    (repo / "c.txt").write_text("owned-line\n", encoding="utf-8")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-qm", "init")

    # Two workers edited the SAME file: the owner's change AND foreign bytes coexist.
    (repo / "c.txt").write_text("owned-line-changed\nOTHER-WORKER-BYTES\n", encoding="utf-8")

    # A whole-file path commit cannot isolate the owner's hunk.
    _git(repo, "commit", "-qm", "feat: c", "--", "c.txt")

    committed_body = _git(repo, "show", "HEAD:c.txt")
    assert "OTHER-WORKER-BYTES" in committed_body, "path commit failed to capture the whole current file"


@pytest_skip_no_git
def test_isolated_index_commit_is_scoped_to_owned_path(tmp_path: Path) -> None:
    """The safe action: an owned path committed from the shared tree stays narrow."""
    repo = _init_repo(tmp_path)
    for name in ("x.txt", "y.txt", "z.txt"):
        (repo / name).write_text("v0\n", encoding="utf-8")
    _git(repo, "add", "x.txt", "y.txt", "z.txt")
    _git(repo, "commit", "-qm", "init")

    # Owner edits x.txt only; y.txt and z.txt are dirtied by others but never staged.
    (repo / "x.txt").write_text("x-owned\n", encoding="utf-8")
    (repo / "y.txt").write_text("y-foreign\n", encoding="utf-8")
    (repo / "z.txt").write_text("z-foreign\n", encoding="utf-8")

    _git(repo, "add", "x.txt")
    _git(repo, "commit", "-qm", "feat: x", "--", "x.txt")

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert committed == ["x.txt"]
    # Foreign working-tree changes are preserved, never overwritten or cleaned.
    assert "y-foreign" in (repo / "y.txt").read_text(encoding="utf-8")
    assert "z-foreign" in (repo / "z.txt").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# PRD-CORE-219 FR02/FR05/FR06: isolated candidates, CAS publication, handoff
# ---------------------------------------------------------------------------


def test_request_integration_fails_closed_when_parent_ref_unresolvable(tmp_path: Path) -> None:
    """FR06 fail-CLOSED: if the recorded checked-out ref cannot be resolved by
    ``git rev-parse``, request_integration must REFUSE the handoff, not coerce the
    tip to "" and silently skip the stale-parent check (release-verify 2026-07-17 P1)."""
    from trw_mcp.state.git_commit_transaction import (
        GitTransactionError,
        build_and_publish_candidate,
        read_journal,
        request_integration,
        write_journal,
    )

    repo, parent = _txn_repo(tmp_path)
    manifest = _owned_manifest(repo, parent)
    build_and_publish_candidate(manifest, repo, "feat: unresolvable-parent")
    journal = read_journal(repo, "txn-a")
    assert journal is not None
    # Point the recorded checked-out ref at a branch that does not exist so
    # `git rev-parse <ref>` raises instead of returning a resolvable tip.
    write_journal(repo, journal.model_copy(update={"checked_out_ref": "refs/heads/does-not-exist"}))

    with pytest.raises(GitTransactionError, match="cannot resolve the reviewed branch tip"):
        request_integration(repo, "txn-a")

    # The journal was advanced to a terminal FAILED state, not left integrable.
    after = read_journal(repo, "txn-a")
    assert after is not None and after.reason == "parent_ref_unresolvable"


def _txn_repo(tmp_path: Path) -> tuple[Path, str]:
    """Multi-writer fixture: committed base + FOREIGN staged and unstaged noise."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "owned.py").write_text("owned-v1\n", encoding="utf-8")
    (repo / "foreign.py").write_text("foreign-v1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True, capture_output=True)
    # Concurrent-writer noise: foreign STAGED edit + foreign UNSTAGED edit.
    (repo / "foreign.py").write_text("foreign-staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "foreign.py"], check=True, capture_output=True)
    (repo / "foreign.py").write_text("foreign-unstaged\n", encoding="utf-8")
    parent = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, parent


def _owned_manifest(repo: Path, parent: str) -> object:
    import hashlib

    from trw_mcp.models.git_commit_transaction import OwnershipManifest

    (repo / "owned.py").write_text("owned-v2\n", encoding="utf-8")
    return OwnershipManifest(
        transaction_id="txn-a",
        run_id="run/one",
        parent_oid=parent,
        owned_paths=("owned.py",),
        path_digests={"owned.py": "sha256:" + hashlib.sha256((repo / "owned.py").read_bytes()).hexdigest()},
    )


def test_prd_core_219_fr02(tmp_path: Path) -> None:
    """FR02 acceptance: Given shared staged and unstaged changes, When candidate
    construction runs, Then the candidate contains only owned paths and
    porcelain status, cached diff semantics, worktree bytes, HEAD, and shared
    index digest are unchanged."""
    import subprocess

    from trw_mcp.models.git_commit_transaction import TransactionState
    from trw_mcp.state.git_commit_transaction import (
        build_and_publish_candidate,
        snapshot_shared_state,
    )

    repo, parent = _txn_repo(tmp_path)
    manifest = _owned_manifest(repo, parent)
    before = snapshot_shared_state(repo)
    cached_before = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached"], check=True, capture_output=True, text=True
    ).stdout

    journal = build_and_publish_candidate(manifest, repo, "feat(owned): candidate")

    assert str(journal.state) == TransactionState.CANDIDATE_PUBLISHED.value
    # Candidate delta is exactly the owned path.
    delta = subprocess.run(
        ["git", "-C", str(repo), "diff-tree", "--no-commit-id", "--name-only", "-r", journal.candidate_oid],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert delta == ["owned.py"]
    # Shared state semantically unchanged (branch, HEAD, index bytes, porcelain).
    assert snapshot_shared_state(repo) == before
    cached_after = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached"], check=True, capture_output=True, text=True
    ).stdout
    assert cached_after == cached_before  # foreign staged edit untouched
    assert (repo / "foreign.py").read_text(encoding="utf-8") == "foreign-unstaged\n"


def test_prd_core_219_fr02_preserves_git_modes_and_deletions(tmp_path: Path) -> None:
    """FR02/NFR02: candidate entries retain native Git modes.

    The executable case is the independent-review repro: modifying the bytes
    of a parent-tree 100755 file must not silently turn it into 100644. The
    same candidate also proves ordinary files, symlink entries, and deletions.
    """
    import os
    import subprocess

    from tests._git_commit_workflow_support import verified_candidate
    from trw_mcp.state.git_commit_transaction import snapshot_shared_state

    repo = _init_repo(tmp_path)
    executable = repo / "tool.sh"
    ordinary = repo / "plain.txt"
    symlink = repo / "tool-link"
    deleted = repo / "delete-me.txt"
    executable.write_text("#!/bin/sh\necho before\n", encoding="utf-8")
    executable.chmod(0o755)
    ordinary.write_text("plain before\n", encoding="utf-8")
    deleted.write_text("delete me\n", encoding="utf-8")
    os.symlink("tool.sh", symlink)
    _git(repo, "add", "--all")
    _git(repo, "commit", "-qm", "base modes")
    # Model repositories where Git must retain the reviewed parent's mode
    # rather than infer a permission change from filesystem metadata.
    _git(repo, "config", "core.filemode", "false")

    executable.write_text("#!/bin/sh\necho after\n", encoding="utf-8")
    ordinary.write_text("plain after\n", encoding="utf-8")
    symlink.unlink()
    os.symlink("plain.txt", symlink)
    deleted.unlink()
    before = snapshot_shared_state(repo)

    result, _run_dir = verified_candidate(
        repo,
        ("tool.sh", "plain.txt", "tool-link", "delete-me.txt"),
        "fix: preserve git modes\n",
        "run/modes",
        transaction_id="txn-modes",
    )

    entries = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", result["candidate_oid"], "--", "tool.sh", "plain.txt", "tool-link"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    modes = {line.rsplit("\t", 1)[1]: line.split(maxsplit=1)[0] for line in entries}
    assert modes == {"plain.txt": "100644", "tool-link": "120000", "tool.sh": "100755"}
    assert _git(repo, "show", f"{result['candidate_oid']}:tool-link") == "plain.txt"
    assert not _git(repo, "ls-tree", result["candidate_oid"], "--", "delete-me.txt").strip()
    assert snapshot_shared_state(repo) == before


def test_prd_core_219_fr05(tmp_path: Path) -> None:
    """FR05 (publication slice): the candidate ref lives in the namespaced
    refs/trw/commit-candidates/ space, CAS-published without advancing the
    checked-out branch; a second publication to the same ref fails (reuse
    defense) instead of silently overwriting."""
    import subprocess

    import pytest

    from trw_mcp.state.git_commit_transaction import GitTransactionError, build_and_publish_candidate

    repo, parent = _txn_repo(tmp_path)
    manifest = _owned_manifest(repo, parent)
    journal = build_and_publish_candidate(manifest, repo, "feat: one")
    assert journal.candidate_ref.startswith("refs/trw/commit-candidates/run-one/")
    head_after = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert head_after == parent  # checked-out branch never advanced

    # Reuse of the same transaction ref is refused by CAS (expected-absent).
    with pytest.raises(GitTransactionError):
        build_and_publish_candidate(manifest, repo, "feat: duplicate")


def test_prd_core_219_fr06(tmp_path: Path) -> None:
    """FR06 acceptance: no checked-out branch, HEAD, shared index, or worktree
    bytes advance; the helper returns the immutable candidate ref plus explicit
    quiescence/native-integration preconditions; automatic integration is
    refused with automatic_integration_unsupported."""
    from trw_mcp.models.git_commit_transaction import (
        REASON_AUTO_INTEGRATION_UNSUPPORTED,
        TransactionState,
    )
    from trw_mcp.state.git_commit_transaction import (
        build_and_publish_candidate,
        request_integration,
        snapshot_shared_state,
    )

    repo, parent = _txn_repo(tmp_path)
    manifest = _owned_manifest(repo, parent)
    journal = build_and_publish_candidate(manifest, repo, "feat: handoff")
    before = snapshot_shared_state(repo)

    # Attempted AUTOMATIC integration: refused, zero shared-state mutation.
    refused, handoff = request_integration(repo, "txn-a", automatic=True)
    assert str(refused.state) == TransactionState.CANDIDATE_ONLY.value
    assert refused.reason == REASON_AUTO_INTEGRATION_UNSUPPORTED
    assert handoff is None
    assert snapshot_shared_state(repo) == before

    # Manual handoff: immutable candidate + explicit preconditions, still no-op.
    ready, handoff = request_integration(repo, "txn-a")
    assert str(ready.state) == TransactionState.HANDOFF_READY.value
    assert handoff is not None
    assert handoff.candidate_oid == journal.candidate_oid
    assert handoff.owned_paths == ("owned.py",)
    assert any("quiescent" in p for p in handoff.preconditions)
    assert any("native git" in p for p in handoff.preconditions)
    assert snapshot_shared_state(repo) == before


def test_prd_core_219_fr05_retention_and_tombstones(tmp_path: Path) -> None:
    """FR05 retention matrix: expired unprotected candidates collect after 30
    days (injected day counter), reviewed/handoff-ready refs survive, collected
    ids are tombstoned, and tombstoned transaction ids cannot be reused."""
    import subprocess

    import pytest

    from trw_mcp.models.git_commit_transaction import CANDIDATE_RETENTION_DAYS
    from trw_mcp.state.git_commit_transaction import (
        GitTransactionError,
        _journal_path,
        build_and_publish_candidate,
        cleanup_candidates,
        read_journal,
        record_candidate_review,
        request_integration,
    )

    repo, parent = _txn_repo(tmp_path)
    manifest = _owned_manifest(repo, parent)
    journal = build_and_publish_candidate(manifest, repo, "feat: retained")

    # Fresh candidate: retained.
    today_days = int(_journal_path(repo, "txn-a").stat().st_mtime // 86400)
    result = cleanup_candidates(repo, now_epoch_days=today_days)
    assert result == {"collected": [], "retained": ["txn-a"]}

    # Aged past retention and unprotected: collected + ref deleted + tombstoned.
    result = cleanup_candidates(repo, now_epoch_days=today_days + CANDIDATE_RETENTION_DAYS + 1)
    assert result["collected"] == ["txn-a"]
    refs = subprocess.run(
        ["git", "-C", str(repo), "for-each-ref", "refs/trw/commit-candidates"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert journal.candidate_ref not in refs
    tombstone = read_journal(repo, "txn-a")
    assert tombstone is not None and tombstone.tombstoned

    # Tombstoned transaction id cannot be reused for a new review.
    with pytest.raises(GitTransactionError, match="tombstoned"):
        record_candidate_review(repo, _owned_manifest(repo, parent), "feat: reuse")

    # Handoff-ready candidates are protected from collection.
    import hashlib as _hashlib

    from trw_mcp.models.git_commit_transaction import OwnershipManifest

    (repo / "owned.py").write_text("owned-v3\n", encoding="utf-8")
    protected_manifest = OwnershipManifest(
        transaction_id="txn-b",
        run_id="run/one",
        parent_oid=parent,
        owned_paths=("owned.py",),
        path_digests={"owned.py": "sha256:" + _hashlib.sha256((repo / "owned.py").read_bytes()).hexdigest()},
    )
    build_and_publish_candidate(protected_manifest, repo, "feat: protected")
    request_integration(repo, "txn-b")  # -> handoff_ready
    protected_days = int(_journal_path(repo, "txn-b").stat().st_mtime // 86400)
    result = cleanup_candidates(repo, now_epoch_days=protected_days + CANDIDATE_RETENTION_DAYS + 10)
    assert "txn-b" in result["retained"]


def test_prd_core_219_nfr01(tmp_path: Path) -> None:
    """NFR01 acceptance: the full PRODUCTION path leaves checked-out branch,
    HEAD, shared index digest, and porcelain status semantically unchanged."""
    from tests._git_commit_workflow_support import verified_candidate
    from trw_mcp.state.git_commit_transaction import snapshot_shared_state

    repo, _parent = _txn_repo(tmp_path)
    (repo / "owned.py").write_text("owned-v2\n", encoding="utf-8")
    before = snapshot_shared_state(repo)
    verified_candidate(repo, ("owned.py",), "feat: owned\n", "run/nfr01")
    after = snapshot_shared_state(repo)
    assert after == before  # branch, HEAD, index digest, porcelain — all equal


def test_prd_core_219_nfr03(tmp_path: Path) -> None:
    """NFR03 acceptance: an interrupted transaction is journal-recoverable —
    a pre-publication crash recovers as safe-to-rebuild; a journal claiming a
    published candidate whose ref vanished becomes a typed failure and its
    claim releases."""
    import json as _json

    from tests._git_commit_workflow_support import verified_candidate
    from trw_mcp.models.git_commit_transaction import TransactionState
    from trw_mcp.state.git_commit_transaction import (
        _git,
        read_journal,
        record_candidate_review,
        recover_transaction,
        snapshot_shared_state,
    )
    from trw_mcp.state.git_commit_workflow import build_manifest_from_worktree

    repo, _parent = _txn_repo(tmp_path)
    (repo / "owned.py").write_text("owned-v2\n", encoding="utf-8")

    # Crash BEFORE publication: review recorded, then the process dies.
    manifest = build_manifest_from_worktree(repo, ("owned.py",), "run/one", transaction_id="txn-crash")
    record_candidate_review(repo, manifest, "msg")
    before = snapshot_shared_state(repo)
    recovered = recover_transaction(repo, "txn-crash")
    assert str(recovered.state) == TransactionState.RECOVERED.value
    assert recovered.recovery_action == "safe_to_rebuild_and_re_review"
    assert snapshot_shared_state(repo) == before  # recovery touches nothing shared

    # Crash AFTER publication with a lost ref: typed failure + claim release.
    # (txn-crash's recovered claim still binds owned.py, so use fresh paths.)
    (repo / "pub.py").write_text("pub-v1\n", encoding="utf-8")
    result, _run_dir = verified_candidate(repo, ("pub.py",), "feat: pub\n", "run/one", transaction_id="txn-pub")
    _git(repo, "update-ref", "-d", result["candidate_ref"])  # simulate lost ref
    failed = recover_transaction(repo, "txn-pub")
    assert str(failed.state) == TransactionState.FAILED.value
    assert failed.reason == "published_candidate_ref_missing"
    assert not (repo / ".trw" / "git-transactions" / "claims" / "txn-pub.json").exists()

    # Intact published transaction is confirmed as-is (no transition).
    (repo / "ok.py").write_text("ok-v1\n", encoding="utf-8")
    intact, _run_dir = verified_candidate(repo, ("ok.py",), "feat: ok\n", "run/one", transaction_id="txn-ok")
    confirmed = recover_transaction(repo, "txn-ok")
    assert confirmed.candidate_oid == intact["candidate_oid"]
    journal_raw = _json.loads(
        (repo / ".trw" / "git-transactions" / "journals" / "txn-ok.json").read_text(encoding="utf-8")
    )
    assert journal_raw["state"] == read_journal(repo, "txn-ok").state


def test_fr06_parent_race_blocks_handoff(tmp_path: Path) -> None:
    """Audit F6: a branch that advanced past the reviewed parent makes the
    candidate STALE — handoff refuses and requires a fresh review; the
    precondition is enforced, not prose."""
    import subprocess

    from trw_mcp.models.git_commit_transaction import TransactionState
    from trw_mcp.state.git_commit_transaction import (
        GitTransactionError,
        publish_reviewed_candidate,
        read_journal,
        record_candidate_review,
        request_integration,
    )
    from trw_mcp.state.git_commit_workflow import build_manifest_from_worktree

    repo, _parent = _txn_repo(tmp_path)
    (repo / "owned.py").write_text("owned-v2\n", encoding="utf-8")
    manifest = build_manifest_from_worktree(repo, ("owned.py",), "run/one", transaction_id="txn-race")
    record_candidate_review(repo, manifest, "msg")
    publish_reviewed_candidate(repo, manifest, "msg")

    # A concurrent commit advances the checked-out branch past the parent.
    (repo / "third.py").write_text("concurrent\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "third.py"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "concurrent", "--", "third.py"],
        check=True,
        capture_output=True,
    )

    with pytest.raises(GitTransactionError, match="advanced past the reviewed parent"):
        request_integration(repo, "txn-race")
    stale = read_journal(repo, "txn-race")
    assert stale is not None and str(stale.state) == TransactionState.FAILED.value
    assert stale.reason == "stale_parent_requires_re_review"


def test_fr05_checkpoint_referenced_refs_retain(tmp_path: Path) -> None:
    """Audit F7: a checkpoint-referenced published candidate survives the
    retention sweep even past the age limit; unreferenced ones collect."""
    from trw_mcp.models.git_commit_transaction import CANDIDATE_RETENTION_DAYS
    from trw_mcp.state.git_commit_transaction import (
        cleanup_candidates,
        publish_reviewed_candidate,
        read_journal,
        record_candidate_review,
    )
    from trw_mcp.state.git_commit_workflow import build_manifest_from_worktree

    repo, _parent = _txn_repo(tmp_path)
    # Published-but-not-handed-off candidates (handoff-ready ones are protected
    # by state; the F7 gap is exactly the CANDIDATE_PUBLISHED + referenced case).
    (repo / "kept.py").write_text("kept\n", encoding="utf-8")
    kept_manifest = build_manifest_from_worktree(repo, ("kept.py",), "run/one", transaction_id="txn-kept")
    record_candidate_review(repo, kept_manifest, "feat: kept")
    referenced_journal = publish_reviewed_candidate(repo, kept_manifest, "feat: kept")
    (repo / "drop.py").write_text("drop\n", encoding="utf-8")
    drop_manifest = build_manifest_from_worktree(repo, ("drop.py",), "run/one", transaction_id="txn-drop")
    record_candidate_review(repo, drop_manifest, "feat: dropped")
    publish_reviewed_candidate(repo, drop_manifest, "feat: dropped")
    referenced = {"candidate_ref": referenced_journal.candidate_ref}
    assert read_journal(repo, "txn-kept") is not None

    far_future = int((repo / ".trw").stat().st_mtime // 86400) + CANDIDATE_RETENTION_DAYS + 10
    report = cleanup_candidates(
        repo,
        now_epoch_days=far_future,
        referenced_transaction_ids=frozenset({"txn-kept"}),
    )
    assert "txn-drop" in report["collected"]
    assert "txn-kept" in report["retained"]
    import subprocess

    refs = subprocess.run(
        ["git", "-C", str(repo), "for-each-ref", "refs/trw/commit-candidates/"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert referenced["candidate_ref"] in refs
