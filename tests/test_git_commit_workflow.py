"""Verified two-step candidate-commit workflow and CLI coverage."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import structlog

from tests._git_commit_workflow_support import create_verified_run, journal_edits_and_build


def _last_json(out: str) -> dict[str, object]:
    idx = out.rindex("\n{") + 1 if "\n{" in out else out.index("{")
    return json.loads(out[idx:])


@pytest.fixture(autouse=True)
def _restore_structlog_config() -> object:
    saved = structlog.get_config()
    yield
    structlog.configure(**saved)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "owned.py").write_text("owned-v1\n", encoding="utf-8")
    (repo / "foreign.py").write_text("foreign-v1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True, capture_output=True)
    (repo / "foreign.py").write_text("foreign-staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "foreign.py"], check=True, capture_output=True)
    (repo / "foreign.py").write_text("foreign-unstaged\n", encoding="utf-8")
    return repo


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def test_run_candidate_commit_full_production_path(tmp_path: Path) -> None:
    from trw_mcp.state.git_commit_workflow import prepare_candidate_claim, run_candidate_commit

    repo = _init_repo(tmp_path)
    run_dir = create_verified_run(repo, "run/one")
    manifest = prepare_candidate_claim(repo, ("owned.py",), run_dir, transaction_id="txn-happy")
    assert manifest.pre_edit_digests["owned.py"] == "sha256:" + hashlib.sha256(b"owned-v1\n").hexdigest()

    (repo / "owned.py").write_text("owned-v2\n", encoding="utf-8")
    journal_edits_and_build(run_dir, ("owned.py",), "run/one")
    head_before = _git_out(repo, "rev-parse", "HEAD").strip()
    status_before = _git_out(repo, "--no-optional-locks", "status", "--porcelain=v2", "--untracked-files=no")
    result = run_candidate_commit(repo, "feat: owned change\n", transaction_id=manifest.transaction_id, run_dir=run_dir)

    assert result["candidate_ref"] == "refs/trw/commit-candidates/run-one/txn-happy"
    assert _git_out(repo, "rev-parse", str(result["candidate_ref"])).strip() == result["candidate_oid"]
    assert _git_out(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", str(result["candidate_oid"])).split() == [
        "owned.py"
    ]
    assert result["reviewed_parent_oid"] == head_before
    assert result["evidence_bound"] is True
    assert _git_out(repo, "rev-parse", "HEAD").strip() == head_before
    assert _git_out(repo, "--no-optional-locks", "status", "--porcelain=v2", "--untracked-files=no") == status_before
    assert (repo / "foreign.py").read_text(encoding="utf-8") == "foreign-unstaged\n"
    records = [
        json.loads(line)
        for line in (run_dir / "meta" / "checkpoints.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records[-1]["candidate_evidence"]["candidate_oid"] == result["candidate_oid"]


def test_preexisting_foreign_edit_cannot_be_claimed_at_commit_time(tmp_path: Path) -> None:
    """The publication entrypoint has no paths/run-id inputs and refuses a file
    that was already edited before a claim and lacks a post-claim event."""
    from trw_mcp.state.git_commit_transaction import GitTransactionError
    from trw_mcp.state.git_commit_workflow import prepare_candidate_claim, run_candidate_commit

    repo = _init_repo(tmp_path)
    (repo / "owned.py").write_text("foreign-before-claim\n", encoding="utf-8")
    run_dir = create_verified_run(repo, "run/owner")
    manifest = prepare_candidate_claim(repo, ("owned.py",), run_dir, transaction_id="txn-foreign")
    journal_edits_and_build(run_dir, (), "run/owner")
    with pytest.raises(GitTransactionError, match="lacks a post-claim run journal event"):
        run_candidate_commit(repo, "feat: absorb\n", transaction_id=manifest.transaction_id, run_dir=run_dir)
    assert "refs/trw/commit-candidates" not in _git_out(repo, "for-each-ref")


def test_arbitrary_run_or_external_run_directory_is_rejected(tmp_path: Path) -> None:
    from trw_mcp.state.git_commit_transaction import GitTransactionError
    from trw_mcp.state.git_commit_workflow import prepare_candidate_claim

    repo = _init_repo(tmp_path)
    external = tmp_path / "forged-run"
    (external / "meta").mkdir(parents=True)
    (external / "meta" / "run.yaml").write_text("run_id: arbitrary\n", encoding="utf-8")
    with pytest.raises(GitTransactionError, match="not under this repository"):
        prepare_candidate_claim(repo, ("owned.py",), external)


def test_claim_path_set_cannot_widen(tmp_path: Path) -> None:
    from trw_mcp.state.git_commit_workflow import prepare_candidate_claim, run_candidate_commit

    repo = _init_repo(tmp_path)
    run_dir = create_verified_run(repo, "run/one")
    manifest = prepare_candidate_claim(repo, ("owned.py",), run_dir, transaction_id="txn-narrow")
    (repo / "owned.py").write_text("owned-v2\n", encoding="utf-8")
    (repo / "foreign.py").write_text("foreign-after\n", encoding="utf-8")
    journal_edits_and_build(run_dir, ("owned.py", "foreign.py"), "run/one")
    result = run_candidate_commit(repo, "feat: narrow\n", transaction_id=manifest.transaction_id, run_dir=run_dir)
    assert result["owned_paths"] == ["owned.py"]


@pytest.mark.parametrize("drift", ["parent", "branch"])
def test_prepared_repository_context_drift_is_rejected(tmp_path: Path, drift: str) -> None:
    from trw_mcp.state.git_commit_transaction import GitTransactionError
    from trw_mcp.state.git_commit_workflow import prepare_candidate_claim, run_candidate_commit

    repo = _init_repo(tmp_path)
    run_dir = create_verified_run(repo, "run/one")
    manifest = prepare_candidate_claim(repo, ("owned.py",), run_dir, transaction_id=f"txn-{drift}")
    (repo / "owned.py").write_text("owned-v2\n", encoding="utf-8")
    journal_edits_and_build(run_dir, ("owned.py",), "run/one")
    if drift == "parent":
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "advance"], check=True, capture_output=True
        )
        expected = "parent changed"
    else:
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "other"], check=True, capture_output=True)
        expected = "branch changed"
    with pytest.raises(GitTransactionError, match=expected):
        run_candidate_commit(repo, "feat: drift\n", transaction_id=manifest.transaction_id, run_dir=run_dir)


def test_missing_post_claim_validation_receipt_blocks(tmp_path: Path) -> None:
    from trw_mcp.state.git_commit_transaction import GitTransactionError
    from trw_mcp.state.git_commit_workflow import prepare_candidate_claim, run_candidate_commit

    repo = _init_repo(tmp_path)
    run_dir = create_verified_run(repo, "run/one")
    manifest = prepare_candidate_claim(repo, ("owned.py",), run_dir, transaction_id="txn-no-build")
    (repo / "owned.py").write_text("owned-v2\n", encoding="utf-8")
    with (run_dir / "meta" / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "file_modified", "file": "owned.py"}) + "\n")
    with pytest.raises(GitTransactionError, match="no successful post-claim build receipt"):
        run_candidate_commit(repo, "feat: no proof\n", transaction_id=manifest.transaction_id, run_dir=run_dir)


def test_validation_receipt_must_bind_final_post_edit_bytes(tmp_path: Path) -> None:
    from trw_mcp.state.git_commit_transaction import GitTransactionError
    from trw_mcp.state.git_commit_workflow import prepare_candidate_claim, run_candidate_commit

    repo = _init_repo(tmp_path)
    run_dir = create_verified_run(repo, "run/one")
    manifest = prepare_candidate_claim(repo, ("owned.py",), run_dir, transaction_id="txn-stale-build")
    (repo / "owned.py").write_text("validated-v2\n", encoding="utf-8")
    journal_edits_and_build(run_dir, ("owned.py",), "run/one")
    # A later edit invalidates the receipt even though its run, time, command
    # IDs, and path scope still look valid.
    (repo / "owned.py").write_text("never-validated-v3\n", encoding="utf-8")
    with pytest.raises(GitTransactionError, match="no successful post-claim build receipt"):
        run_candidate_commit(repo, "feat: stale proof\n", transaction_id=manifest.transaction_id, run_dir=run_dir)


def test_validation_binding_rejects_incorrect_final_file_size(tmp_path: Path) -> None:
    from trw_mcp.models._evidence_core import ContentBinding, ContentEntry, EntryState, compute_manifest_digest
    from trw_mcp.state.git_commit_workflow import _binding_matches_final_content

    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "owned.py"
    target.write_bytes(b"abc")
    digest = hashlib.sha256(b"abc").hexdigest()
    entry = ContentEntry(path="owned.py", state=EntryState.FILE, byte_digest=digest, byte_size=999)
    binding = ContentBinding(
        scope_id="scope",
        scope_digest="scope-digest",
        project_identity="repo",
        entries=(entry,),
        manifest_digest=compute_manifest_digest((entry,)),
    )
    assert not _binding_matches_final_content(binding, repo, {"owned.py": f"sha256:{digest}"})


def test_cli_prepare_then_commit_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

    repo = _init_repo(tmp_path)
    run_dir = create_verified_run(repo, "run/cli")
    SUBCOMMAND_HANDLERS["prepare-candidate"](
        argparse.Namespace(paths=["owned.py"], transaction_id="txn-cli", repo_root=str(repo), run_dir=str(run_dir))
    )
    prepared = _last_json(capsys.readouterr().out)
    assert prepared["state"] == "prepared"
    (repo / "owned.py").write_text("owned-v2\n", encoding="utf-8")
    journal_edits_and_build(run_dir, ("owned.py",), "run/cli")
    message_file = tmp_path / "msg.txt"
    message_file.write_text("feat: cli\n", encoding="utf-8")
    SUBCOMMAND_HANDLERS["commit-candidate"](
        argparse.Namespace(
            message_file=str(message_file),
            transaction_id="txn-cli",
            repo_root=str(repo),
            run_dir=str(run_dir),
            require_signature=False,
        )
    )
    result = _last_json(capsys.readouterr().out)
    assert str(result["candidate_ref"]).startswith("refs/trw/commit-candidates/run-cli/")


def test_cli_missing_message_file_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

    with pytest.raises(SystemExit) as exit_info:
        SUBCOMMAND_HANDLERS["commit-candidate"](
            argparse.Namespace(
                message_file=str(tmp_path / "absent"),
                transaction_id="txn",
                repo_root=str(tmp_path),
                run_dir=str(tmp_path),
                require_signature=False,
            )
        )
    assert exit_info.value.code == 2
    assert "message file not found" in str(_last_json(capsys.readouterr().out)["error"])


def test_run_candidate_commit_rejects_empty_message(tmp_path: Path) -> None:
    from trw_mcp.state.git_commit_transaction import GitTransactionError
    from trw_mcp.state.git_commit_workflow import run_candidate_commit

    repo = _init_repo(tmp_path)
    with pytest.raises(GitTransactionError, match="message must not be empty"):
        run_candidate_commit(repo, "   ", transaction_id="missing", run_dir=repo)
