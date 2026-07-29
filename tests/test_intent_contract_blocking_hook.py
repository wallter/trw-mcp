"""PRD-SEC-013 FR05 + NFR01 + NFR02: pre-write metadata-only gate and its boundary."""

from __future__ import annotations

import time
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
from trw_mcp.security.intent_contract import _falsifier, check_write, post_edit_check
from trw_mcp.security.intent_contract._hook_common import ALLOW, BLOCK


def fifty_claim_contract() -> str:
    """A 50-claim contract — NFR02's reference corpus size."""
    head = "contract_id: INTENT-TEST\nmust_not_happen:\n"
    body = "".join(
        f"  - claim_id: C-{index}\n"
        f'    text: "claim {index}"\n'
        "    authority_class: human_approved\n"
        "    state: active\n"
        "    machine_checkable: true\n"
        "    binding_channel: blocking_hook\n"
        f'    anchors: ["{PROTECTED if index == 0 else f"other/{index}.py"}"]\n'
        "    falsifiers:\n"
        "      - kind: pytest\n"
        f'        node_id: "tests/test_guard.py::test_{index}"\n'
        for index in range(50)
    )
    return head + body


def test_pre_write_metadata_only_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    """FR05 evidence artifact: never spawns a falsifier; blocks only on infra faults."""
    calls: list[object] = []

    def _spy(*args: object, **kwargs: object) -> object:
        calls.append(args)
        raise AssertionError("the pre-write hook must never spawn a subprocess")

    monkeypatch.setattr(_falsifier.subprocess, "run", _spy)
    root = make_project(tmp_path)

    # A matching, active, blocking-hook claim: allowed — FR07 owns the substance.
    assert check_write.run(payload(root)).code == ALLOW
    assert calls == []

    # No matching claim: allowed.
    assert check_write.run(payload(root, "unrelated.py")).code == ALLOW
    assert calls == []

    # Malformed contract: fail closed.
    (root / CONTRACT_REL).write_text("must_not_happen: [\n", encoding="utf-8")
    assert check_write.run(payload(root)).code == BLOCK
    assert calls == []


def test_fail_closed_vs_fail_open_boundary(
    tmp_path: Path, intent_env: IntentContractConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """NFR01 evidence artifact: each named mode lands on its correct control point."""
    # Row 1: no contract file at all -> allow.
    root = make_project(tmp_path, contract=None)
    assert check_write.run(payload(root)).code == ALLOW
    assert post_edit_check.run(payload(root)).code == ALLOW

    # Row 2: never enrolled -> allow, even with a malformed contract present.
    never = make_project(tmp_path / "never", contract="must_not_happen: [\n", enroll=False)
    assert not (never / ".trw/contracts/enrollment.yaml").exists()

    # Row 4: malformed contract while enrolled -> fail closed on BOTH.
    root = make_project(tmp_path, contract="must_not_happen: [\n")
    assert check_write.run(payload(root)).code == BLOCK
    assert post_edit_check.run(payload(root)).code == BLOCK

    # Row 3: enrolled but stale -> fail closed + unsuppressible warning.
    root = make_project(tmp_path, contract=contract_yaml())
    (root / CONTRACT_REL).write_text(contract_yaml(node_id="tests/other.py::test_x"), encoding="utf-8")
    capsys.readouterr()
    assert check_write.run(payload(root)).code == BLOCK
    assert "unsuppressible" in capsys.readouterr().err

    # Row 6: no anchor match -> allow.
    root = make_project(tmp_path, contract=contract_yaml())
    assert check_write.run(payload(root, "unrelated.py")).code == ALLOW

    # Row: machine_checkable false -> claim skipped, allow.
    root = make_project(tmp_path, contract=contract_yaml(machine_checkable="false"))
    assert check_write.run(payload(root)).code == ALLOW
    assert post_edit_check.run(payload(root)).code == ALLOW

    # Row: non-blocking channel -> claim cannot ride this channel, allow.
    root = make_project(tmp_path, contract=contract_yaml(channel="advisory"))
    assert post_edit_check.run(payload(root)).code == ALLOW

    # A non-active claim is not enforced either (and its weakening is FR01's job).
    root = make_project(tmp_path, contract=contract_yaml(state="suspect"))
    assert post_edit_check.run(payload(root)).code == ALLOW


def test_absent_contract_allows(tmp_path: Path, intent_env: IntentContractConfig) -> None:
    root = make_project(tmp_path, contract=None)
    assert check_write.run(payload(root)).code == ALLOW


def test_path_outside_the_repo_is_not_governed(tmp_path: Path, intent_env: IntentContractConfig) -> None:
    root = make_project(tmp_path)
    assert check_write.run(payload(root, "../escape.py")).code == ALLOW


def test_disabled_kill_switch_is_a_total_no_op_before_enrollment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    """The kill switch's remaining scope: a project that never opted in.

    It used to reach enrolled projects too, which made one working-tree line in
    .trw/config.yaml a total disarm — see the N9 section of
    ``test_intent_contract_enrollment_gate.py`` for that ruling and its controls.
    A malformed contract is used deliberately: it is the loudest thing the hooks
    could fail on, so an ALLOW here really is a total no-op.
    """
    from trw_mcp.security.intent_contract import _hook_common

    monkeypatch.setattr(_hook_common, "intent_config", lambda: IntentContractConfig(enabled=False))
    root = make_project(tmp_path, contract="must_not_happen: [\n", enroll=False)
    assert check_write.run(payload(root)).code == ALLOW
    assert post_edit_check.run(payload(root)).code == ALLOW


def test_disabled_kill_switch_does_not_reach_an_enrolled_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    """Same malformed contract, but ENROLLED: both control points must still block."""
    from trw_mcp.security.intent_contract import _hook_common

    root = make_project(tmp_path, contract="must_not_happen: [\n")
    assert post_edit_check.run(payload(root)).code == BLOCK, "the baseline must actually enforce"

    monkeypatch.setattr(_hook_common, "intent_config", lambda: IntentContractConfig(enabled=False))
    assert check_write.run(payload(root)).code == BLOCK
    assert post_edit_check.run(payload(root)).code == BLOCK


def test_unreadable_payload_fails_closed(tmp_path: Path, intent_env: IntentContractConfig) -> None:
    import io

    root = make_project(tmp_path)
    assert check_write.run(io.StringIO("{not json")).code == BLOCK
    assert post_edit_check.run(io.StringIO("{not json")).code == BLOCK
    assert root.exists()


def test_hook_latency_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig) -> None:
    """NFR02 evidence artifact: FR05 <= 1s (no falsifier), FR07 <= 5s (with one)."""
    root = make_project(tmp_path, contract=fifty_claim_contract())

    start = time.monotonic()
    assert check_write.run(payload(root)).code == ALLOW
    assert time.monotonic() - start <= intent_env.pre_write_hook_budget_seconds

    # One anchored falsifier averaging ~1s wall clock, per NFR02's wording.
    monkeypatch.setattr(
        post_edit_check,
        "run_falsifier",
        lambda *a, **k: (time.sleep(1.0), _falsifier.FalsifierResult("pass", ""))[1],
    )
    start = time.monotonic()
    assert post_edit_check.run(payload(root)).code == ALLOW
    assert time.monotonic() - start <= intent_env.post_edit_hook_budget_seconds


def test_pre_write_never_reads_the_target_file_contents(tmp_path: Path, intent_env: IntentContractConfig) -> None:
    """The pre-write path is metadata-only: a missing target still allows."""
    root = make_project(tmp_path)
    (root / PROTECTED).unlink()
    assert check_write.run(payload(root)).code == ALLOW


def test_pre_write_also_matches_a_hardlink_alias(tmp_path: Path, intent_env: IntentContractConfig) -> None:
    """FR05 stays metadata-only, but its telemetry must record the alias as a MATCH."""
    import os

    from trw_mcp.security.intent_contract.telemetry import read_telemetry

    root = make_project(tmp_path)
    os.link(root / PROTECTED, root / "alias.py")
    assert check_write.run(payload(root, "alias.py")).code == ALLOW
    outcomes = read_telemetry(root)["recent_outcomes"]
    assert isinstance(outcomes, list)
    assert outcomes[-1] == "allowed_match"
