"""PRD-SEC-013 FR03: append-only, hash-chained, checkpoint-anchored ledger."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.security.intent_contract.ledger import (
    LedgerError,
    checkpoint_path,
    ledger_path,
    record_override,
    record_violation,
    verify_override_ledger,
)


def override(root: Path, claim_id: str = "C-1", reason: str = "operator break-glass") -> dict[str, object]:
    return record_override(
        claim_id=claim_id,
        file_path="protected/module.py",
        control_point="FR07",
        session_id="sess-a",
        reason=reason,
        block_class="break_glass",
        root=root,
    )


# ── DX regression: CLI ``--root`` used to be silently ignored ──────────────


def test_cli_verify_honors_explicit_root_and_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An explicit ``--root`` must verify THAT ledger, not ``repo_root()``'s."""
    from trw_mcp.security.intent_contract import ledger as ledger_module

    decoy = tmp_path / "decoy"
    decoy.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    override(target)  # a valid, single-entry ledger under target
    ledger_path(decoy).parent.mkdir(parents=True, exist_ok=True)
    ledger_path(decoy).write_text("not json\n", encoding="utf-8")  # decoy is corrupt
    monkeypatch.setattr(ledger_module, "repo_root", lambda: decoy)

    exit_code = ledger_module.main(["verify", "--root", str(target)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["valid"] is True
    assert output["entry_count"] == 1


def test_cli_verify_without_root_still_falls_back_to_repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: omitting ``--root`` must keep the pre-existing behavior."""
    from trw_mcp.security.intent_contract import ledger as ledger_module

    decoy = tmp_path / "decoy"
    decoy.mkdir()
    ledger_path(decoy).parent.mkdir(parents=True, exist_ok=True)
    ledger_path(decoy).write_text("not json\n", encoding="utf-8")
    monkeypatch.setattr(ledger_module, "repo_root", lambda: decoy)

    exit_code = ledger_module.main(["verify"])

    assert exit_code == 1


def test_ledger_append_chain_and_tamper_detection(tmp_path: Path) -> None:
    """FR03 evidence artifact: append + chain verify + in-place tamper + re-genesis."""
    for index in range(3):
        override(tmp_path, claim_id=f"C-{index}")
    state = verify_override_ledger(tmp_path)
    assert state.valid is True
    assert state.entry_count == 3

    override(tmp_path, claim_id="C-3")
    after = verify_override_ledger(tmp_path)
    assert after.valid is True
    assert after.entry_count == 4
    checkpoint = json.loads(checkpoint_path(tmp_path).read_text(encoding="utf-8"))
    assert checkpoint["entry_count"] == 4
    assert checkpoint["head_hash"] == after.head_hash

    # In-place mutation of a middle entry -> break reported at that index.
    lines = ledger_path(tmp_path).read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[1])
    entry["reason"] = "rewritten"
    lines[1] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    ledger_path(tmp_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    broken = verify_override_ledger(tmp_path)
    assert broken.valid is False
    assert broken.broken_index == 1


def test_tail_truncation_is_caught_by_the_checkpoint(tmp_path: Path) -> None:
    for index in range(3):
        override(tmp_path, claim_id=f"C-{index}")
    lines = ledger_path(tmp_path).read_text(encoding="utf-8").splitlines()
    ledger_path(tmp_path).write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    state = verify_override_ledger(tmp_path)
    assert state.valid is False
    assert "checkpoint mismatch" in state.reason


def test_wholesale_regenesis_is_caught_even_though_it_self_verifies(tmp_path: Path) -> None:
    """The replacement chain's own links verify; only the checkpoint catches it."""
    for index in range(3):
        override(tmp_path, claim_id=f"C-{index}")
    genuine_checkpoint = json.loads(checkpoint_path(tmp_path).read_text(encoding="utf-8"))

    fake_root = tmp_path / "fake"
    fake_root.mkdir()
    for index in range(3):
        override(fake_root, claim_id=f"C-{index}", reason="attacker-authored")
    forged = ledger_path(fake_root).read_text(encoding="utf-8")
    ledger_path(tmp_path).write_text(forged, encoding="utf-8")

    # The forged chain verifies internally...
    forged_entries = [json.loads(line) for line in forged.splitlines()]
    assert len(forged_entries) == 3
    assert verify_override_ledger(fake_root).valid is True
    # ...but not against the real checkpoint.
    state = verify_override_ledger(tmp_path)
    assert state.valid is False
    assert "checkpoint mismatch" in state.reason
    assert genuine_checkpoint["head_hash"] != state.head_hash


def test_deleted_ledger_with_surviving_checkpoint_is_caught(tmp_path: Path) -> None:
    override(tmp_path)
    ledger_path(tmp_path).unlink()
    state = verify_override_ledger(tmp_path)
    assert state.valid is False


def test_append_refuses_on_an_already_broken_chain(tmp_path: Path) -> None:
    override(tmp_path)
    ledger_path(tmp_path).write_text('{"seq": 0, "entry_hash": "deadbeef"}\n', encoding="utf-8")
    with pytest.raises(LedgerError):
        override(tmp_path)


def test_empty_reason_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(LedgerError):
        override(tmp_path, reason="   ")
    with pytest.raises(LedgerError):
        record_violation(claim_id="C-1", file_path="p.py", session_id="s", reason="", root=tmp_path)


def test_absent_ledger_and_checkpoint_verify_as_empty(tmp_path: Path) -> None:
    state = verify_override_ledger(tmp_path)
    assert state.valid is True
    assert state.entry_count == 0


def test_violation_entries_are_distinguishable_from_overrides(tmp_path: Path) -> None:
    override(tmp_path)
    record_violation(
        claim_id="C-1", file_path="protected/module.py", session_id="s", reason="falsifier failed", root=tmp_path
    )
    entries = [json.loads(line) for line in ledger_path(tmp_path).read_text(encoding="utf-8").splitlines()]
    assert [entry["kind"] for entry in entries] == ["override", "violation"]
    assert verify_override_ledger(tmp_path).valid is True


def test_ledger_path_is_not_configurable(tmp_path: Path) -> None:
    """R13: the canonical location is a module constant, not a config knob."""
    assert ledger_path(tmp_path).relative_to(tmp_path).as_posix() == ".trw/contracts/intent-override-ledger.jsonl"
    assert (
        checkpoint_path(tmp_path).relative_to(tmp_path).as_posix()
        == ".trw/contracts/intent-override-ledger.checkpoint.json"
    )


def test_forged_ledger_and_checkpoint_pair_verifies_and_is_caught_only_by_git(tmp_path: Path) -> None:
    """DISCLOSED RESIDUAL: the checkpoint shares the ledger's trust domain.

    Rewriting BOTH files consistently passes in-file verification — that is a
    property of co-location, not a bug to assert away. The mechanical signal
    that survives is C9: committing either file requires a signature (see
    ``test_intent_contract_signed_commit.py::
    test_ledger_and_checkpoint_rewrite_is_control_plane_weakening``), and an
    uncommitted rewrite is detectable only against history.
    """
    real = tmp_path / "real"
    forged = tmp_path / "forged"
    real.mkdir()
    forged.mkdir()
    override(real, reason="genuine operator override")
    override(forged, reason="attacker-authored")

    ledger_path(real).write_text(ledger_path(forged).read_text(encoding="utf-8"), encoding="utf-8")
    checkpoint_path(real).write_text(checkpoint_path(forged).read_text(encoding="utf-8"), encoding="utf-8")

    # Documented, not asserted away: the forged PAIR verifies.
    assert verify_override_ledger(real).valid is True
    # And the forged content is materially different from what was recorded.
    assert "attacker-authored" in ledger_path(real).read_text(encoding="utf-8")
