"""PRD-SEC-013 NFR03: the adversarial case matrix. Every case must be CAUGHT.

Each case is a named callable returning ``True`` when the attack is detected and
handled per its documented failure-mode policy. ``test_adversarial_case_matrix``
runs them all and reports every case that silently passed, so a regression names
the exact bypass rather than a generic assertion failure.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from collections.abc import Callable
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
from tests._intent_contract_hooks import make_project, payload
from trw_mcp.models.config._sub_models import IntentContractConfig
from trw_mcp.security.intent_contract import _hook_common, check_write, post_edit_check
from trw_mcp.security.intent_contract._hook_common import BLOCK
from trw_mcp.security.intent_contract.enrollment import check_enrollment_status
from trw_mcp.security.intent_contract.ledger import (
    checkpoint_path,
    ledger_path,
    record_override,
    verify_override_ledger,
)
from trw_mcp.security.intent_contract.loader import ContractLoadError, load_contract_bytes
from trw_mcp.security.intent_contract.retro_compensator import scan_recent_history
from trw_mcp.security.intent_contract.signed_commit import check_commit_range
from trw_mcp.security.intent_contract.weaken_edit_detector import (
    detect_range_weaken_then_edit,
    detect_same_commit_weaken,
)

HOOK_DIR = Path(__file__).resolve().parent.parent / "src/trw_mcp/data/hooks"

WEAKENED = "contract_id: INTENT-TEST\nmust_not_happen: []\n"


# --- individual adversarial cases -------------------------------------------


def case_malformed_contract(tmp_path: Path) -> bool:
    try:
        load_contract_bytes(b"must_not_happen: [\n")
    except ContractLoadError:
        return True
    return False


def case_unsigned_weakening_commit(tmp_path: Path) -> bool:
    repo, base = seed_contract_repo(tmp_path, "unsigned")
    write(repo, CONTRACT_PATH, WEAKENED)
    head = commit_all(repo, "drop the claim")
    return [v.sha for v in check_commit_range(repo, base, head)] == [head]


def case_same_commit_weaken_and_edit(tmp_path: Path) -> bool:
    repo, _ = seed_contract_repo(tmp_path, "same-commit")
    write(repo, PROTECTED, "def guard():\n    return False\n")
    write(repo, CONTRACT_PATH, WEAKENED)
    head = commit_all(repo, "weaken and edit together")
    return detect_same_commit_weaken(repo, head) is not None


def case_split_commit_range(tmp_path: Path) -> bool:
    repo, base = seed_contract_repo(tmp_path, "split-commit")
    write(repo, CONTRACT_PATH, WEAKENED)
    commit_all(repo, "weaken")
    write(repo, PROTECTED, "def guard():\n    return False\n")
    head = commit_all(repo, "edit")
    return len(detect_range_weaken_then_edit(repo, base, head)) == 1


def case_contract_rename_escape(tmp_path: Path) -> bool:
    repo, base = seed_contract_repo(tmp_path, "rename")
    git(repo, "mv", CONTRACT_PATH, ".trw/contracts/renamed.yaml")
    head = commit_all(repo, "rename the contract")
    violations = check_commit_range(repo, base, head)
    return bool(violations) and any(hit.condition == "C9" for hit in violations[0].weakened)


def case_duplicate_claim_id(tmp_path: Path) -> bool:
    repo, base = seed_contract_repo(tmp_path, "duplicate-id")
    doubled = contract_yaml() + contract_yaml().split("must_not_happen:\n")[1]
    write(repo, CONTRACT_PATH, doubled)
    head = commit_all(repo, "duplicate the claim id")
    violations = check_commit_range(repo, base, head)
    return bool(violations) and any(hit.condition == "C1" for hit in violations[0].weakened)


def case_yaml_alias_injection(tmp_path: Path) -> bool:
    alias_doc = (
        b"seed: &s\n  authority_class: observational\n"
        b"must_not_happen:\n  - claim_id: C-1\n    text: t\n"
        b"    <<: *s\n    state: active\n    machine_checkable: true\n"
        b"    binding_channel: blocking_hook\n"
    )
    duplicate_doc = b"contract_id: a\ncontract_id: b\n"
    caught = 0
    for raw, expected in ((alias_doc, "anchor_alias_or_tag"), (duplicate_doc, "duplicate_key")):
        try:
            load_contract_bytes(raw)
        except ContractLoadError as exc:
            caught += int(exc.reason == expected)
    return caught == 2


def case_timeout_is_not_fail_open(tmp_path: Path) -> bool:
    """Probe the SHELL trap: an un-set intentional-exit flag turns 124 into 0."""
    if shutil.which("timeout") is None or shutil.which("sh") is None:
        pytest.skip("timeout/sh unavailable")
    project = tmp_path / "timeout-probe"
    hooks = project / ".claude/hooks"
    hooks.mkdir(parents=True)
    for name in ("pre-tool-intent-guard.sh", "post-tool-intent-check.sh", "lib-trw.sh"):
        shutil.copy2(HOOK_DIR / name, hooks / name)
    (project / ".trw/contracts").mkdir(parents=True)
    (project / ".trw/contracts/enrollment.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    fake_bin = project / "fakebin"
    fake_bin.mkdir()
    slow = fake_bin / "python3"
    slow.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    slow.chmod(slow.stat().st_mode | stat.S_IXUSR)

    env = {
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "HOME": str(project),
        "TRW_INTENT_POST_EDIT_BUDGET_SECONDS": "1",
        "TRW_INTENT_PRE_WRITE_BUDGET_SECONDS": "1",
    }
    results = []
    for hook in ("pre-tool-intent-guard.sh", "post-tool-intent-check.sh"):
        completed = subprocess.run(
            ["sh", str(hooks / hook)],
            cwd=str(project),
            input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "x.py"}}),
            capture_output=True,
            text=True,
            env=env,
            check=False,
            shell=False,
        )
        results.append(completed.returncode)
    return results == [2, 2]


def case_ledger_tail_truncation(tmp_path: Path) -> bool:
    root = tmp_path / "truncate"
    root.mkdir()
    for index in range(3):
        record_override(
            claim_id=f"C-{index}",
            file_path=PROTECTED,
            control_point="FR07",
            session_id="s",
            reason="operator override",
            block_class="break_glass",
            root=root,
        )
    lines = ledger_path(root).read_text(encoding="utf-8").splitlines()
    ledger_path(root).write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    return verify_override_ledger(root).valid is False


def case_ledger_regenesis(tmp_path: Path) -> bool:
    real = tmp_path / "real"
    fake = tmp_path / "fake"
    real.mkdir()
    fake.mkdir()
    for root in (real, fake):
        record_override(
            claim_id="C-1",
            file_path=PROTECTED,
            control_point="FR07",
            session_id="s",
            reason="operator override" if root is real else "attacker-authored",
            block_class="break_glass",
            root=root,
        )
    ledger_path(real).write_text(ledger_path(fake).read_text(encoding="utf-8"), encoding="utf-8")
    forged_verifies_alone = verify_override_ledger(fake).valid is True
    caught = verify_override_ledger(real).valid is False
    checkpoint = json.loads(checkpoint_path(real).read_text(encoding="utf-8"))
    return forged_verifies_alone and caught and checkpoint["entry_count"] == 1


def case_never_enrolled_vs_stale(tmp_path: Path) -> bool:
    never = make_project(tmp_path / "never", enroll=False)
    enrolled = make_project(tmp_path / "enrolled")
    stale_ok = check_enrollment_status(never, ".trw/contracts/must-not-happen.yaml") == "never_enrolled"
    (enrolled / ".trw/contracts/must-not-happen.yaml").unlink()
    return stale_ok and check_enrollment_status(enrolled, ".trw/contracts/must-not-happen.yaml") == "stale"


def case_falsifier_addition_without_signature(tmp_path: Path) -> bool:
    repo, base = seed_contract_repo(tmp_path, "falsifier-add")
    write(
        repo,
        CONTRACT_PATH,
        contract_yaml().replace(
            'node_id: "tests/test_protected.py::test_guard"',
            'node_id: "tests/test_protected.py::test_guard"\n      - kind: pytest\n        node_id: "tests/t.py::extra"',
        ),
    )
    head = commit_all(repo, "add a falsifier")
    violations = check_commit_range(repo, base, head)
    return bool(violations) and any(hit.condition == "C4" for hit in violations[0].weakened)


def case_vacuous_falsifier_substitution(tmp_path: Path) -> bool:
    """Swapping in an always-passing falsifier is a protected change (C4)."""
    repo, base = seed_contract_repo(tmp_path, "vacuous")
    write(repo, CONTRACT_PATH, contract_yaml(falsifier="tests/test_vacuous.py::test_always_passes"))
    head = commit_all(repo, "substitute a vacuous falsifier")
    violations = check_commit_range(repo, base, head)
    caught_by_signature = bool(violations) and any(hit.condition == "C4" for hit in violations[0].weakened)
    # The same substitution is ALSO caught retroactively if the hooks were skipped.
    write(repo, PROTECTED, "def guard():\n    return False\n")
    commit_all(repo, "edit the protected module")
    caught_retroactively = bool(scan_recent_history(repo, window_commits=50))
    del base, head
    return caught_by_signature and caught_retroactively


def case_control_plane_disarm(tmp_path: Path) -> bool:
    """An unsigned `enabled: false` flip or hook de-registration is weakening."""
    repo, base = seed_contract_repo(tmp_path, "control-plane")
    write(repo, ".trw/config.yaml", "security:\n  intent:\n    enabled: false\n")
    flip = commit_all(repo, "disable the mechanism")
    flip_caught = bool(check_commit_range(repo, base, flip))

    write(
        repo,
        "trw-mcp/src/trw_mcp/data/settings.json",
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Write|Edit|MultiEdit", "hooks": [{"command": "sh pre-tool-intent-guard.sh"}]}
                    ]
                }
            }
        ),
    )
    registered = commit_all(repo, "register the hook")
    write(repo, "trw-mcp/src/trw_mcp/data/settings.json", json.dumps({"hooks": {"PreToolUse": []}}))
    deregistered = commit_all(repo, "quietly de-register the hook")
    deregistration_caught = bool(check_commit_range(repo, registered, deregistered))
    return flip_caught and deregistration_caught


ADVERSARIAL_CASES: dict[str, Callable[[Path], bool]] = {
    "malformed_contract": case_malformed_contract,
    "unsigned_weakening_commit": case_unsigned_weakening_commit,
    "same_commit_weaken_and_edit": case_same_commit_weaken_and_edit,
    "split_commit_range_weaken_then_edit": case_split_commit_range,
    "contract_rename_locator_escape": case_contract_rename_escape,
    "duplicate_claim_id_across_range": case_duplicate_claim_id,
    "yaml_alias_and_duplicate_key_injection": case_yaml_alias_injection,
    "timeout_as_fail_open_regression": case_timeout_is_not_fail_open,
    "ledger_tail_truncation": case_ledger_tail_truncation,
    "ledger_wholesale_regenesis": case_ledger_regenesis,
    "never_enrolled_vs_stale_enrollment": case_never_enrolled_vs_stale,
    "falsifier_addition_without_signature": case_falsifier_addition_without_signature,
    "vacuous_falsifier_substitution": case_vacuous_falsifier_substitution,
    "unsigned_control_plane_disarm": case_control_plane_disarm,
}


@pytest_skip_no_git
def test_adversarial_case_matrix(tmp_path: Path) -> None:
    """NFR03 evidence artifact: every named case is detected; none silently passes."""
    undetected = []
    for name, case in ADVERSARIAL_CASES.items():
        root = tmp_path / name
        root.mkdir(parents=True, exist_ok=True)
        if not case(root):
            undetected.append(name)
    assert undetected == [], f"adversarial cases that silently passed: {undetected}"


@pytest_skip_no_git
@pytest.mark.parametrize("case_name", sorted(ADVERSARIAL_CASES))
def test_each_adversarial_case_individually(case_name: str, tmp_path: Path) -> None:
    """Same matrix, one case per test id, so a failure names the exact bypass."""
    assert ADVERSARIAL_CASES[case_name](tmp_path) is True


def test_break_glass_cannot_be_self_supplied_through_the_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3: the agent-controlled payload carries no authorization whatsoever."""
    monkeypatch.setattr(_hook_common, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(_hook_common, "intent_config", lambda: IntentContractConfig(falsifier_timeout_seconds=2.0))
    from trw_mcp.security.intent_contract._falsifier import FalsifierResult

    root = make_project(tmp_path)
    monkeypatch.setattr(post_edit_check, "run_falsifier", lambda *a, **k: FalsifierResult("fail", "exit 1"))
    assert post_edit_check.run(payload(root)).code == BLOCK
    # And the pre-write path never blocks on a payload claim either way.
    assert check_write.run(payload(root)).code == 0
