"""PRD-SEC-013 FR08: the standing history check that survives ``--no-verify``.

This test IS the control. It is a pytest, not a git hook, so a commit made with
``--no-verify`` (or force-pushed around FR01/FR02) is still caught on the next
``make check`` / ``make test-fast`` run.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests._intent_contract_git import (
    CONTRACT_PATH,
    PROTECTED,
    commit_all,
    contract_yaml,
    git,
    pytest_skip_no_git,
    seed_contract_repo,
    write,
)
from trw_mcp.models.config._sub_models import IntentContractConfig
from trw_mcp.security.intent_contract.ledger import record_override
from trw_mcp.security.intent_contract.retro_compensator import (
    recent_commits_with_paths,
    scan_recent_history,
)
from trw_mcp.security.intent_contract.weaken_edit_detector import commit_diff_hash, record_approval

WEAKENED = "contract_id: INTENT-TEST\nmust_not_happen: []\n"


def _repo_root() -> Path | None:
    if shutil.which("git") is None:
        return None
    start = Path(__file__).resolve().parent
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(start),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    return Path(result.stdout.strip()) if result.returncode == 0 else None


def test_last_n_commits_have_no_unapproved_weaken_then_edit() -> None:
    """FR08 evidence artifact: this repository's recent history is clean."""
    root = _repo_root()
    if root is None:
        pytest.skip("not a git checkout")
    window = IntentContractConfig().retro_compensator_window_commits
    findings = scan_recent_history(root, window_commits=window)
    assert findings == [], "unapproved weaken-then-edit found in recent history: " + "; ".join(
        f"{f.claim_id} weakened at {f.weakening_sha}, edited at {f.editing_sha}" for f in findings
    )


@pytest_skip_no_git
def test_synthetic_unapproved_weaken_then_edit_is_caught(tmp_path: Path) -> None:
    """A --no-verify bypass leaves the evidence in history; the walk finds it."""
    repo, _ = seed_contract_repo(tmp_path)
    write(repo, CONTRACT_PATH, WEAKENED)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "drop the claim behind the hooks")
    write(repo, PROTECTED, "def guard():\n    return False\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "edit the protected module")

    findings = scan_recent_history(repo, window_commits=50)
    assert [f.claim_id for f in findings] == ["C-1"]


@pytest_skip_no_git
def test_same_history_passes_once_the_override_is_ledgered(tmp_path: Path) -> None:
    repo, _ = seed_contract_repo(tmp_path)
    write(repo, CONTRACT_PATH, WEAKENED)
    commit_all(repo, "drop the claim")
    write(repo, PROTECTED, "def guard():\n    return False\n")
    commit_all(repo, "edit the protected module")

    assert scan_recent_history(repo, window_commits=50)
    record_override(
        claim_id="C-1",
        file_path=PROTECTED,
        control_point="FR02",
        session_id="operator",
        reason="reviewed and accepted out of band",
        block_class="blocked",
        root=repo,
    )
    assert scan_recent_history(repo, window_commits=50) == []


@pytest_skip_no_git
def test_an_independent_session_approval_also_satisfies_the_walk(tmp_path: Path) -> None:
    repo, _ = seed_contract_repo(tmp_path)
    write(repo, CONTRACT_PATH, WEAKENED)
    commit_all(repo, "drop the claim")
    write(repo, PROTECTED, "def guard():\n    return False\n")
    editing = commit_all(repo, "edit the protected module")

    record_approval(
        repo,
        diff_hash=commit_diff_hash(repo, editing),
        session_id="reviewer-session",
        claim_id="C-1",
        note="independent review",
    )
    assert scan_recent_history(repo, window_commits=50, session_id="author-session") == []


@pytest_skip_no_git
def test_clean_history_produces_no_findings(tmp_path: Path) -> None:
    repo, _ = seed_contract_repo(tmp_path)
    write(repo, PROTECTED, "def guard():\n    return True  # tidy\n")
    commit_all(repo, "edit protected code without touching the contract")
    write(repo, CONTRACT_PATH, contract_yaml() + "# a trailing comment\n")
    commit_all(repo, "cosmetic contract rewrite")
    assert scan_recent_history(repo, window_commits=50) == []


@pytest_skip_no_git
def test_history_walk_is_one_log_pass(tmp_path: Path) -> None:
    """NFR02: the window walk must not be O(commits) subprocesses to enumerate."""
    repo, _ = seed_contract_repo(tmp_path)
    for index in range(5):
        write(repo, f"noise{index}.txt", f"{index}\n")
        commit_all(repo, f"noise {index}")
    commits = recent_commits_with_paths(repo, 50)
    assert len(commits) == 6
    assert commits[-1][1] == ("noise4.txt",)
