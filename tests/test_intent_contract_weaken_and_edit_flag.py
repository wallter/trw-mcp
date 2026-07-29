"""PRD-SEC-013 FR02 + FR04: the standing proof that the weaken-then-edit flag fires.

FR04 IS this file: if either detector is disabled, the corresponding test fails,
so neither assertion is vacuous.
"""

from __future__ import annotations

import os
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
from trw_mcp.security.intent_contract.weaken_edit_detector import (
    UNANSWERABLE_CLAIM_ID,
    detect_range_weaken_then_edit,
    detect_same_commit_weaken,
    detect_staged_weaken,
    record_approval,
)

WEAKENED = "contract_id: INTENT-TEST\nmust_not_happen: []\n"


@pytest_skip_no_git
def test_same_commit_weaken_and_edit_flag_fires(tmp_path: Path) -> None:
    """FR04 evidence artifact: the L-2EW1 shape is caught mechanically."""
    repo, _ = seed_contract_repo(tmp_path)
    write(repo, PROTECTED, "def guard():\n    return False  # gate removed\n")
    write(repo, CONTRACT_PATH, WEAKENED)
    head = commit_all(repo, "refactor the guard and drop its claim")

    finding = detect_same_commit_weaken(repo, head)
    assert finding is not None
    assert finding.claim_id == "C-1"
    assert finding.condition == "C2"
    assert finding.edited_paths == (PROTECTED,)


@pytest_skip_no_git
def test_split_commit_range_weaken_then_edit_flag_fires(tmp_path: Path) -> None:
    """FR04 evidence artifact: neither commit alone trips case 1 (codex #5)."""
    repo, base = seed_contract_repo(tmp_path)
    write(repo, CONTRACT_PATH, WEAKENED)
    weakening = commit_all(repo, "drop the claim")
    write(repo, PROTECTED, "def guard():\n    return False\n")
    editing = commit_all(repo, "edit the protected module")

    assert detect_same_commit_weaken(repo, weakening) is None
    assert detect_same_commit_weaken(repo, editing) is None

    findings = detect_range_weaken_then_edit(repo, base, editing)
    assert [f.claim_id for f in findings] == ["C-1"]
    assert findings[0].weakening_sha == weakening
    assert findings[0].editing_sha == editing


@pytest_skip_no_git
def test_editing_protected_code_alone_does_not_fire(tmp_path: Path) -> None:
    repo, base = seed_contract_repo(tmp_path)
    write(repo, PROTECTED, "def guard():\n    return True  # tidy\n")
    head = commit_all(repo, "touch the protected module only")
    assert detect_same_commit_weaken(repo, head) is None
    assert detect_range_weaken_then_edit(repo, base, head) == []


@pytest_skip_no_git
def test_weakening_alone_does_not_fire(tmp_path: Path) -> None:
    repo, base = seed_contract_repo(tmp_path)
    write(repo, CONTRACT_PATH, WEAKENED)
    head = commit_all(repo, "drop the claim only")
    assert detect_same_commit_weaken(repo, head) is None
    assert detect_range_weaken_then_edit(repo, base, head) == []


@pytest_skip_no_git
def test_reverting_the_weakening_inside_the_range_clears_the_flag(tmp_path: Path) -> None:
    """OQ10 edge case, resolved by test: an in-range revert clears the pending flag."""
    repo, base = seed_contract_repo(tmp_path)
    write(repo, CONTRACT_PATH, WEAKENED)
    commit_all(repo, "drop the claim")
    write(repo, CONTRACT_PATH, contract_yaml())
    commit_all(repo, "restore the claim")
    write(repo, PROTECTED, "def guard():\n    return False\n")
    editing = commit_all(repo, "edit the protected module")

    assert detect_range_weaken_then_edit(repo, base, editing) == []


@pytest_skip_no_git
def test_independent_session_approval_clears_the_same_commit_flag(tmp_path: Path) -> None:
    repo, _ = seed_contract_repo(tmp_path)
    write(repo, PROTECTED, "def guard():\n    return False\n")
    write(repo, CONTRACT_PATH, WEAKENED)
    head = commit_all(repo, "refactor the guard and drop its claim")

    finding = detect_same_commit_weaken(repo, head, session_id="session-b")
    assert finding is not None
    record_approval(
        repo,
        diff_hash=finding.diff_hash,
        session_id="session-a",
        claim_id="C-1",
        note="reviewed in a separate session",
    )
    assert detect_same_commit_weaken(repo, head, session_id="session-b") is None
    # An approval recorded by the SAME session does not clear it.
    assert detect_same_commit_weaken(repo, head, session_id="session-a") is not None


@pytest_skip_no_git
def test_staged_tree_detection_is_what_the_pre_commit_stage_sees(tmp_path: Path) -> None:
    repo, _ = seed_contract_repo(tmp_path)
    write(repo, PROTECTED, "def guard():\n    return False\n")
    write(repo, CONTRACT_PATH, WEAKENED)
    git(repo, "add", "-A")

    finding = detect_staged_weaken(repo)
    assert finding is not None
    assert finding.claim_id == "C-1"
    assert finding.editing_sha == "<staged>"


@pytest_skip_no_git
def test_staged_unrelated_change_does_not_fire(tmp_path: Path) -> None:
    repo, _ = seed_contract_repo(tmp_path)
    write(repo, "README.md", "hello\n")
    git(repo, "add", "-A")
    assert detect_staged_weaken(repo) is None


@pytest_skip_no_git
def test_staged_unloadable_contract_fires(tmp_path: Path) -> None:
    repo, _ = seed_contract_repo(tmp_path)
    write(repo, CONTRACT_PATH, "must_not_happen: [\n")
    git(repo, "add", "-A")
    finding = detect_staged_weaken(repo)
    assert finding is not None
    assert "unloadable" in finding.detail


@pytest_skip_no_git
def test_state_flip_to_suspect_plus_edit_fires(tmp_path: Path) -> None:
    """The 'benign' documented transition disarms enforcement — it must fire."""
    repo, _ = seed_contract_repo(tmp_path)
    write(repo, CONTRACT_PATH, contract_yaml(state="suspect"))
    write(repo, PROTECTED, "def guard():\n    return False\n")
    head = commit_all(repo, "flip to suspect and edit")

    finding = detect_same_commit_weaken(repo, head)
    assert finding is not None
    assert finding.condition == "C3"


@pytest_skip_no_git
def test_unresolvable_range_fails_closed(tmp_path: Path) -> None:
    repo, _ = seed_contract_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD").strip()
    findings = detect_range_weaken_then_edit(repo, "f" * 40, head)
    assert [f.claim_id for f in findings] == ["<range>"]


# --- F3 (2026-07-25): a check that could not RUN is not a check that PASSED ---
#
# `detect_staged_weaken` did `except GitCommandError: return None`, and None means
# "no finding" all the way out to exit 0. Positive control below: identical staged
# state, healthy git -> a finding; two-line `git` stub -> silence. The sibling
# shapes were `_changed_paths -> ()` and `_contract_at -> None`. The rule these now
# follow is the one pre_push_check.py already stated for an unresolvable range.


def _shadow_git(tmp_path: Path, name: str) -> str:
    """A writable PATH entry holding a ``git`` that always fails."""
    directory = tmp_path / f"{name}-bin"
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / "git"
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    return str(directory)


@pytest_skip_no_git
def test_f3_staged_weaken_under_a_shadowed_git_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reproduction, with its positive control in the same test."""
    repo, _ = seed_contract_repo(tmp_path)
    write(repo, PROTECTED, "def guard():\n    return False\n")
    write(repo, CONTRACT_PATH, WEAKENED)
    git(repo, "add", "-A")

    healthy = detect_staged_weaken(repo)
    assert healthy is not None and healthy.claim_id == "C-1", "baseline must detect the real weakening"

    monkeypatch.setenv("PATH", _shadow_git(tmp_path, "staged") + os.pathsep + os.environ["PATH"])
    sabotaged = detect_staged_weaken(repo)
    assert sabotaged is not None, "a git that cannot answer must not read as `no finding`"
    assert sabotaged.claim_id == UNANSWERABLE_CLAIM_ID
    assert "could not be evaluated" in sabotaged.detail


@pytest_skip_no_git
def test_f3_same_commit_and_range_detectors_fail_closed_under_a_shadowed_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_changed_paths -> ()` had the same shape: no paths, therefore no finding."""
    repo, base = seed_contract_repo(tmp_path)
    write(repo, PROTECTED, "def guard():\n    return False\n")
    write(repo, CONTRACT_PATH, WEAKENED)
    head = commit_all(repo, "refactor the guard and drop its claim")
    assert detect_same_commit_weaken(repo, head) is not None, "baseline must detect it"

    monkeypatch.setenv("PATH", _shadow_git(tmp_path, "commit") + os.pathsep + os.environ["PATH"])
    same = detect_same_commit_weaken(repo, head)
    assert same is not None and same.claim_id == UNANSWERABLE_CLAIM_ID
    # The range detector fails closed one step earlier, at range RESOLUTION, so
    # it reports `<range>`. Either id is fine; an empty list is not.
    ranged = detect_range_weaken_then_edit(repo, base, head)
    assert [f.claim_id for f in ranged] == ["<range>"]


@pytest_skip_no_git
def test_f3_an_unloadable_base_contract_fails_closed_rather_than_protecting_nothing(tmp_path: Path) -> None:
    """`_contract_at -> None` made a corrupt BASE contract mean "nothing was protected".

    Every later comparison then ran against an empty claim set, so no weakening
    could ever be found — the most permissive possible reading of a broken file.
    """
    repo, _ = seed_contract_repo(tmp_path)
    write(repo, CONTRACT_PATH, "must_not_happen: [\n")
    broken = commit_all(repo, "commit a corrupt contract")
    write(repo, PROTECTED, "def guard():\n    return False\n")
    editing = commit_all(repo, "edit the protected module")

    findings = detect_range_weaken_then_edit(repo, broken, editing)
    assert [f.claim_id for f in findings] == [UNANSWERABLE_CLAIM_ID]
    assert "unloadable" in findings[0].detail


@pytest_skip_no_git
def test_f3_healthy_repositories_are_not_swept_up_by_the_fail_closed_rule(tmp_path: Path) -> None:
    """Bystander control: fail-closed that fires on clean repos is a different bug."""
    repo, base = seed_contract_repo(tmp_path)
    write(repo, "README.md", "hello\n")
    head = commit_all(repo, "an entirely unrelated change")

    assert detect_same_commit_weaken(repo, head) is None
    assert detect_range_weaken_then_edit(repo, base, head) == []
    git(repo, "add", "-A")
    assert detect_staged_weaken(repo) is None
