"""PRD-SEC-013 FR01: signature gate over every documented revision case."""

from __future__ import annotations

from pathlib import Path

from tests._intent_contract_git import (
    CONTRACT_PATH,
    PROTECTED,
    commit_all,
    contract_yaml,
    enable_ssh_signing,
    git,
    init_repo,
    pytest_skip_no_git,
    pytest_skip_no_ssh_keygen,
    seed_contract_repo,
    write,
)
from trw_mcp.security.intent_contract._revisions import ZERO_SHA
from trw_mcp.security.intent_contract.signed_commit import check_commit_range


@pytest_skip_no_git
def test_unsigned_weakening_commit_blocks_push(tmp_path: Path) -> None:
    """FR01 evidence artifact: unsigned weakening is rejected with a named sha."""
    repo, base = seed_contract_repo(tmp_path)
    write(repo, CONTRACT_PATH, "contract_id: INTENT-TEST\nmust_not_happen: []\n")
    head = commit_all(repo, "drop the claim")

    violations = check_commit_range(repo, base, head)
    assert [v.sha for v in violations] == [head]
    assert violations[0].reason == "signature_invalid"
    assert {hit.condition for hit in violations[0].weakened} == {"C2"}


@pytest_skip_no_git
@pytest_skip_no_ssh_keygen
def test_signed_equivalent_commit_passes(tmp_path: Path) -> None:
    repo, base = seed_contract_repo(tmp_path)
    enable_ssh_signing(repo)
    write(repo, CONTRACT_PATH, "contract_id: INTENT-TEST\nmust_not_happen: []\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-S", "-m", "drop the claim, signed")
    head = git(repo, "rev-parse", "HEAD").strip()

    assert check_commit_range(repo, base, head) == []


@pytest_skip_no_git
@pytest_skip_no_ssh_keygen
def test_signed_without_allowed_signers_reports_verification_unconfigured(tmp_path: Path) -> None:
    """OQ6, reproduced: a missing allowedSignersFile is an env defect, not tamper."""
    repo, base = seed_contract_repo(tmp_path)
    enable_ssh_signing(repo)
    write(repo, CONTRACT_PATH, "contract_id: INTENT-TEST\nmust_not_happen: []\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-S", "-m", "drop the claim, signed")
    head = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "config", "--unset", "gpg.ssh.allowedSignersFile")

    violations = check_commit_range(repo, base, head)
    assert [v.reason for v in violations] == ["verification_unconfigured"]


@pytest_skip_no_git
def test_non_weakening_contract_edit_needs_no_signature(tmp_path: Path) -> None:
    repo, base = seed_contract_repo(tmp_path)
    widened = contract_yaml(anchors=PROTECTED) + (
        '  - claim_id: C-2\n    text: "another"\n    authority_class: observational\n'
        "    state: active\n    machine_checkable: false\n    binding_channel: advisory\n"
    )
    write(repo, CONTRACT_PATH, widened)
    head = commit_all(repo, "add an observational claim")
    assert check_commit_range(repo, base, head) == []


@pytest_skip_no_git
def test_anchor_superset_needs_no_signature_but_replacement_does(tmp_path: Path) -> None:
    """C6 is set-difference (R15): pure additions are safe, replacements are not."""
    repo, base = seed_contract_repo(tmp_path)
    superset = contract_yaml().replace(f'anchors: ["{PROTECTED}"]', f'anchors: ["{PROTECTED}", "protected/other.py"]')
    write(repo, CONTRACT_PATH, superset)
    head = commit_all(repo, "add an anchor")
    assert check_commit_range(repo, base, head) == []

    # Swapping the specific anchor for a broader directory anchor still REMOVES
    # the original entry from the set, so it costs a signature.
    write(repo, CONTRACT_PATH, contract_yaml(anchors="protected"))
    replaced = commit_all(repo, "replace the anchor with a directory")
    violations = check_commit_range(repo, head, replaced)
    assert violations and any(hit.condition == "C6" for hit in violations[0].weakened)


@pytest_skip_no_git
def test_contract_rename_is_weakening_without_any_field_diff(tmp_path: Path) -> None:
    """codex #2: a git mv must not drop the contract out of comparison."""
    repo, base = seed_contract_repo(tmp_path)
    git(repo, "mv", CONTRACT_PATH, ".trw/contracts/elsewhere.yaml")
    head = commit_all(repo, "move the contract")

    violations = check_commit_range(repo, base, head)
    assert violations and violations[0].sha == head
    assert any(hit.condition == "C9" for hit in violations[0].weakened)


@pytest_skip_no_git
def test_locator_switch_in_config_is_weakening(tmp_path: Path) -> None:
    repo, base = seed_contract_repo(tmp_path)
    write(repo, ".trw/config.yaml", "security:\n  intent:\n    contract_path: .trw/contracts/other.yaml\n")
    head = commit_all(repo, "point the locator elsewhere")
    violations = check_commit_range(repo, base, head)
    assert violations and any(hit.condition == "C9" for hit in violations[0].weakened)


@pytest_skip_no_git
def test_control_plane_disable_is_weakening(tmp_path: Path) -> None:
    """A one-line enabled: false is a complete disarm — it costs a signature."""
    repo, base = seed_contract_repo(tmp_path)
    write(repo, ".trw/config.yaml", "security:\n  intent:\n    enabled: false\n")
    head = commit_all(repo, "disable the mechanism")
    violations = check_commit_range(repo, base, head)
    assert violations and any("control-plane" in hit.detail for hit in violations[0].weakened)


@pytest_skip_no_git
def test_falsifier_addition_on_a_binding_claim_is_weakening(tmp_path: Path) -> None:
    repo, base = seed_contract_repo(tmp_path)
    write(repo, CONTRACT_PATH, contract_yaml(falsifier="tests/test_protected.py::test_other"))
    head = commit_all(repo, "swap the falsifier")
    violations = check_commit_range(repo, base, head)
    assert violations and any(hit.condition == "C4" for hit in violations[0].weakened)


@pytest_skip_no_git
def test_malformed_contract_commit_is_weakening(tmp_path: Path) -> None:
    repo, base = seed_contract_repo(tmp_path)
    write(repo, CONTRACT_PATH, "must_not_happen: [\n")
    head = commit_all(repo, "break the contract file")
    violations = check_commit_range(repo, base, head)
    assert violations and any("unloadable" in hit.detail for hit in violations[0].weakened)


@pytest_skip_no_git
def test_merge_cannot_launder_a_one_sided_weakening(tmp_path: Path) -> None:
    """A merge is compared against EVERY parent, not just the first."""
    repo, base = seed_contract_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "side")
    write(repo, CONTRACT_PATH, "contract_id: INTENT-TEST\nmust_not_happen: []\n")
    commit_all(repo, "weaken on the side branch")
    git(repo, "checkout", "-q", "main")
    write(repo, "unrelated.txt", "noise\n")
    commit_all(repo, "unrelated main commit")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    head = git(repo, "rev-parse", "HEAD").strip()

    violations = check_commit_range(repo, base, head)
    assert head in {v.sha for v in violations}


@pytest_skip_no_git
def test_root_commit_is_compared_against_the_empty_tree(tmp_path: Path) -> None:
    repo = init_repo(tmp_path)
    write(repo, CONTRACT_PATH, contract_yaml())
    head = commit_all(repo, "author the first binding claim")
    violations = check_commit_range(repo, ZERO_SHA, head)
    assert violations and violations[0].sha == head


@pytest_skip_no_git
def test_new_ref_with_zero_sha_uses_shared_history(tmp_path: Path) -> None:
    repo, base = seed_contract_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "feature")
    write(repo, "docs.md", "unrelated\n")
    head = commit_all(repo, "unrelated feature commit")
    assert check_commit_range(repo, ZERO_SHA, head) == []
    assert base != head


@pytest_skip_no_git
def test_unreachable_base_fails_closed(tmp_path: Path) -> None:
    repo, _ = seed_contract_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD").strip()
    violations = check_commit_range(repo, "f" * 40, head)
    assert [v.reason for v in violations] == ["unresolvable_range"]


@pytest_skip_no_git
def test_unrelated_commit_needs_no_signature(tmp_path: Path) -> None:
    repo, base = seed_contract_repo(tmp_path)
    write(repo, "README.md", "hello\n")
    head = commit_all(repo, "unrelated change")
    assert check_commit_range(repo, base, head) == []


@pytest_skip_no_git
def test_contract_the_runtime_rejects_is_also_rejected_by_the_checker(tmp_path: Path) -> None:
    """R10: one allowlist for both paths — a committable contract must be runnable.

    Before this fix the commit-time checker parsed a contract whose falsifier
    argv[0] the runtime allowlist rejects, so a contract could be committed that
    every hook then fail-closed on (or, worse, that the checker compared against
    a claim set the runtime never saw).
    """
    repo, base = seed_contract_repo(tmp_path)
    disallowed = contract_yaml().replace(
        '    falsifiers:\n      - kind: pytest\n        node_id: "tests/test_protected.py::test_guard"\n',
        '    falsifiers:\n      - kind: argv\n        argv: ["bash", "-c", "true"]\n',
    )
    write(repo, CONTRACT_PATH, disallowed)
    head = commit_all(repo, "swap in a non-allowlisted falsifier command")

    violations = check_commit_range(repo, base, head)
    assert violations, "a contract the runtime refuses to load must not commit unsigned"
    assert any("unloadable" in hit.detail for hit in violations[0].weakened)


@pytest_skip_no_git
def test_ledger_and_checkpoint_rewrite_is_control_plane_weakening(tmp_path: Path) -> None:
    """C9 must cover the ledger + checkpoint: a forged self-consistent PAIR
    verifies internally (they share one trust domain), so the only mechanical
    signal left is that COMMITTING them costs a signature."""
    repo, base = seed_contract_repo(tmp_path)
    write(repo, ".trw/contracts/intent-override-ledger.jsonl", '{"seq":0,"entry_hash":"deadbeef"}\n')
    write(
        repo,
        ".trw/contracts/intent-override-ledger.checkpoint.json",
        '{"ledger_id":"forged","entry_count":1,"head_hash":"deadbeef"}\n',
    )
    head = commit_all(repo, "rewrite the ledger and its checkpoint together")

    violations = check_commit_range(repo, base, head)
    assert violations, "an unsigned ledger/checkpoint rewrite must be flagged"
    details = " | ".join(hit.detail for hit in violations[0].weakened)
    assert "ledger" in details or "checkpoint" in details


@pytest_skip_no_git
def test_approval_record_rewrite_is_control_plane_weakening(tmp_path: Path) -> None:
    repo, base = seed_contract_repo(tmp_path)
    write(repo, ".trw/contracts/weaken-edit-approvals.jsonl", '{"diff_hash":"x","session_id":"forged"}\n')
    head = commit_all(repo, "inject an approval record")
    assert check_commit_range(repo, base, head)
