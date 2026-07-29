"""PRD-SEC-013 FR07: falsifier execution against the ACTUAL post-edit tree."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests._intent_contract_hooks import (
    CONTRACT_REL,
    PROTECTED,
    contract_yaml,
    intent_env,  # noqa: F401 — fixture
    make_project,
    payload,
)
from trw_mcp.models.config._sub_models import IntentContractConfig
from trw_mcp.security.intent_contract import _falsifier, post_edit_check
from trw_mcp.security.intent_contract._hook_common import ALLOW, BLOCK
from trw_mcp.security.intent_contract.break_glass import mint_token
from trw_mcp.security.intent_contract.ledger import ledger_path, verify_override_ledger
from trw_mcp.security.intent_contract.telemetry import read_telemetry
from trw_mcp.security.intent_contract.violations import intent_violation_gate_block, open_violations


def stub_falsifier(monkeypatch: pytest.MonkeyPatch, outcome: str, detail: str = "") -> None:
    monkeypatch.setattr(
        post_edit_check,
        "run_falsifier",
        lambda *a, **k: _falsifier.FalsifierResult(outcome, detail),  # type: ignore[arg-type]
    )


def test_post_edit_falsifier_fail_closed_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    """FR07 evidence artifact: pass / fail / timeout / malformed — no silent allow."""
    root = make_project(tmp_path)

    stub_falsifier(monkeypatch, "pass")
    assert post_edit_check.run(payload(root)).code == ALLOW
    assert intent_violation_gate_block(root) is None

    stub_falsifier(monkeypatch, "fail", "exit 1: assert guard()")
    decision = post_edit_check.run(payload(root))
    assert decision.code == BLOCK
    assert "the guard must not be removed" in decision.message
    assert len(open_violations(root)) == 1

    stub_falsifier(monkeypatch, "timeout", "falsifier exceeded 2.0s")
    assert post_edit_check.run(payload(root)).code == BLOCK

    (root / CONTRACT_REL).write_text("must_not_happen: [\n", encoding="utf-8")
    assert post_edit_check.run(payload(root)).code == BLOCK


def test_falsifier_launch_error_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    root = make_project(tmp_path)
    stub_falsifier(monkeypatch, "error", "falsifier could not be launched (FileNotFoundError)")
    assert post_edit_check.run(payload(root)).code == BLOCK


def test_violation_marker_is_the_hard_deliver_gate_enforcement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    """The exit-2 is the surface; the persisted marker is what actually blocks."""
    root = make_project(tmp_path)
    stub_falsifier(monkeypatch, "fail", "exit 1")
    assert post_edit_check.run(payload(root)).code == BLOCK

    block = intent_violation_gate_block(root)
    assert block is not None and "BLOCKED" in block

    # A later re-pass on the same claim/file clears the marker.
    stub_falsifier(monkeypatch, "pass")
    assert post_edit_check.run(payload(root)).code == ALLOW
    assert intent_violation_gate_block(root) is None


def test_violation_is_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    root = make_project(tmp_path)
    stub_falsifier(monkeypatch, "fail", "exit 1")
    post_edit_check.run(payload(root))
    entries = [json.loads(line) for line in ledger_path(root).read_text(encoding="utf-8").splitlines()]
    assert [entry["kind"] for entry in entries] == ["violation"]
    assert verify_override_ledger(root).valid is True


def test_break_glass_token_allows_past_a_failure_and_is_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    root = make_project(tmp_path)
    mint_token(root, claim_id="C-1", file_path=PROTECTED, ttl_seconds=60, max_ttl_seconds=3600)
    stub_falsifier(monkeypatch, "fail", "exit 1")

    assert post_edit_check.run(payload(root)).code == ALLOW
    entries = [json.loads(line) for line in ledger_path(root).read_text(encoding="utf-8").splitlines()]
    assert [entry["block_class"] for entry in entries] == ["break_glass"]
    assert read_telemetry(root)["break_glass"] == 1
    # Single use: the next failure blocks again.
    assert post_edit_check.run(payload(root)).code == BLOCK


def test_agent_supplied_payload_cannot_authorize_an_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    """codex #7: break-glass is not stdin-suppliable."""
    import io

    root = make_project(tmp_path)
    stub_falsifier(monkeypatch, "fail", "exit 1")
    stream = io.StringIO(
        json.dumps(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(root / PROTECTED),
                    "allow_unverified": True,
                    "break_glass": True,
                    "unverified_reason": "I decided this is fine",
                },
            }
        )
    )
    assert post_edit_check.run(stream).code == BLOCK


def test_symlinked_target_fails_closed(tmp_path: Path, intent_env: IntentContractConfig) -> None:
    """A symlink swapped in after the write must not retarget the read."""
    root = make_project(tmp_path)
    target = root / PROTECTED
    target.unlink()
    outside = tmp_path.parent / "outside.py"
    outside.write_text("attacker content\n", encoding="utf-8")
    target.symlink_to(outside)
    assert post_edit_check.run(payload(root)).code == BLOCK


def test_missing_post_edit_target_fails_closed(tmp_path: Path, intent_env: IntentContractConfig) -> None:
    root = make_project(tmp_path)
    (root / PROTECTED).unlink()
    assert post_edit_check.run(payload(root)).code == BLOCK


def test_real_falsifier_runs_against_the_actual_written_file(tmp_path: Path, intent_env: IntentContractConfig) -> None:
    """End-to-end with a REAL pytest node id in a temp package (no stubbing)."""
    root = make_project(tmp_path, contract=contract_yaml(node_id="tests/test_guard.py::test_guard_is_present"))
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests/test_guard.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_guard_is_present() -> None:\n"
        "    assert 'return True' in Path('protected/module.py').read_text()\n",
        encoding="utf-8",
    )
    assert post_edit_check.run(payload(root)).code == ALLOW

    # Now make the ACTUAL post-edit file violate the claim.
    (root / PROTECTED).write_text("def guard():\n    return False\n", encoding="utf-8")
    decision = post_edit_check.run(payload(root))
    assert decision.code == BLOCK
    assert "pytest:tests/test_guard.py::test_guard_is_present" in decision.message


def test_toctou_edit_that_became_unsafe_after_pre_write_is_caught(
    tmp_path: Path, intent_env: IntentContractConfig
) -> None:
    """FR07 reads the true final state, so FR05's earlier allow is not load-bearing."""
    from trw_mcp.security.intent_contract import check_write

    root = make_project(tmp_path, contract=contract_yaml(node_id="tests/test_guard.py::test_guard_is_present"))
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests/test_guard.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_guard_is_present() -> None:\n"
        "    assert 'return True' in Path('protected/module.py').read_text()\n",
        encoding="utf-8",
    )
    # FR05 allows the still-safe proposal...
    assert check_write.run(payload(root)).code == ALLOW
    # ...the write lands and makes it unsafe...
    (root / PROTECTED).write_text("def guard():\n    return False\n", encoding="utf-8")
    # ...and FR07 catches it because it reads the real file.
    assert post_edit_check.run(payload(root)).code == BLOCK


def test_hardlink_alias_to_an_anchored_file_is_still_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    """A hardlink alias edits the SAME inode, so it must not evade the falsifier.

    No-follow resolution defeats symlinks, but a hardlink has no link to follow:
    the alias path simply does not match the anchor glob. Identity therefore
    falls back to (st_dev, st_ino) whenever the target has more than one link.
    """
    root = make_project(tmp_path)
    alias = root / "alias.py"
    os.link(root / PROTECTED, alias)

    stub_falsifier(monkeypatch, "fail", "exit 1")
    decision = post_edit_check.run(payload(root, "alias.py"))
    assert decision.code == BLOCK
    assert "C-1" in decision.message


def test_unrelated_file_with_multiple_links_is_not_falsely_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    """Inode matching must not turn every hardlinked file into a protected one."""
    root = make_project(tmp_path)
    other = root / "other.py"
    other.write_text("x\n", encoding="utf-8")
    os.link(other, root / "other-alias.py")

    stub_falsifier(monkeypatch, "fail", "exit 1")
    assert post_edit_check.run(payload(root, "other-alias.py")).code == ALLOW


def test_unevaluable_falsifier_blocks_as_infra_error_not_as_a_violation(tmp_path: Path) -> None:
    """A check that could not RUN must not be reported as a claim violation.

    pytest exits 2/3/4/5 for interrupted / internal error / usage-or-collection
    error / no-tests-collected. Treating those as "must_not_happen violated"
    blames the edit's author for a renamed test or a missing dependency, and
    pollutes the false-block rate the Track T gate is measured against. It must
    still fail closed — an unevaluable guard is not a passing guard.
    """
    from trw_mcp.security.intent_contract._falsifier import run_falsifier
    from trw_mcp.security.intent_contract._models import PytestFalsifier

    # A node id that cannot be collected -> pytest usage/collection error, not a failure.
    missing = PytestFalsifier(kind="pytest", node_id="tests/nope_does_not_exist.py::test_x")
    result = run_falsifier(tmp_path, missing, timeout_seconds=60, allowed_commands=("pytest",))
    assert not result.passed, "an unevaluable falsifier must never read as a pass"
    assert result.outcome == "error", (
        f"expected an unevaluable check to be classified 'error', got {result.outcome!r} "
        f"({result.detail}) — 'fail' would report it to the operator as a claim violation"
    )


def test_a_genuinely_failing_falsifier_is_still_classified_as_a_violation(tmp_path: Path) -> None:
    """The other half: a real failure must stay 'fail', or enforcement is toothless."""
    from trw_mcp.security.intent_contract._falsifier import run_falsifier
    from trw_mcp.security.intent_contract._models import PytestFalsifier

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_real.py").write_text("def test_boom():\n    assert False\n", encoding="utf-8")
    ref = PytestFalsifier(kind="pytest", node_id="tests/test_real.py::test_boom")
    result = run_falsifier(tmp_path, ref, timeout_seconds=60, allowed_commands=("pytest",))
    assert result.outcome == "fail", f"a failing test must classify as 'fail', got {result.outcome!r}"
