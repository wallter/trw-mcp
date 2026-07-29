"""PRD-SEC-013 R3 break-glass tokens + FR07's open-violation deliver-gate seam."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from trw_mcp.security.intent_contract.break_glass import break_glass_dir, consume_token, mint_token
from trw_mcp.security.intent_contract.violations import (
    clear_violation,
    intent_violation_gate_block,
    open_violations,
    record_open_violation,
    violations_path,
)

CLAIM = "C-1"
FILE = "protected/module.py"


# ── DX regression: CLI ``--root`` used to be silently ignored ──────────────


def test_cli_mint_honors_explicit_root_and_not_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit ``--root`` must mint THERE, not under ``repo_root()``'s cwd."""
    from trw_mcp.security.intent_contract import paths as paths_module
    from trw_mcp.security.intent_contract.break_glass import main as break_glass_main

    decoy = tmp_path / "decoy"
    decoy.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    monkeypatch.setattr(paths_module, "repo_root", lambda: decoy)

    exit_code = break_glass_main(
        ["mint", "--claim-id", CLAIM, "--file", FILE, "--ttl-seconds", "60", "--root", str(target)]
    )

    assert exit_code == 0
    assert list(break_glass_dir(target).glob("*.token"))
    assert not break_glass_dir(decoy).exists()


def test_valid_token_is_consumed_exactly_once(tmp_path: Path) -> None:
    mint_token(tmp_path, claim_id=CLAIM, file_path=FILE, ttl_seconds=60, max_ttl_seconds=3600)
    assert consume_token(tmp_path, claim_id=CLAIM, file_path=FILE) is not None
    assert consume_token(tmp_path, claim_id=CLAIM, file_path=FILE) is None


def test_absent_token_means_no_override(tmp_path: Path) -> None:
    assert consume_token(tmp_path, claim_id=CLAIM, file_path=FILE) is None


def test_expired_token_is_treated_as_absent(tmp_path: Path) -> None:
    path = mint_token(tmp_path, claim_id=CLAIM, file_path=FILE, ttl_seconds=1, max_ttl_seconds=3600)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["expires_at"] = int(time.time()) - 5
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert consume_token(tmp_path, claim_id=CLAIM, file_path=FILE) is None


def test_token_for_a_different_claim_or_file_does_not_apply(tmp_path: Path) -> None:
    mint_token(tmp_path, claim_id=CLAIM, file_path=FILE, ttl_seconds=60, max_ttl_seconds=3600)
    assert consume_token(tmp_path, claim_id="C-2", file_path=FILE) is None
    assert consume_token(tmp_path, claim_id=CLAIM, file_path="other.py") is None


def test_group_readable_token_is_rejected(tmp_path: Path) -> None:
    path = mint_token(tmp_path, claim_id=CLAIM, file_path=FILE, ttl_seconds=60, max_ttl_seconds=3600)
    path.chmod(0o644)
    assert consume_token(tmp_path, claim_id=CLAIM, file_path=FILE) is None


def test_ttl_cap_is_enforced_at_mint_time(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="break_glass_token_ttl_max_seconds"):
        mint_token(tmp_path, claim_id=CLAIM, file_path=FILE, ttl_seconds=99999, max_ttl_seconds=3600)


def test_tokens_live_outside_the_git_tracked_contracts_tree(tmp_path: Path) -> None:
    """R3: .trw/runtime/ is classified ephemeral, so a token is never committed."""
    assert break_glass_dir(tmp_path).relative_to(tmp_path).as_posix() == ".trw/runtime/break-glass"


def test_open_violation_marker_blocks_and_clears(tmp_path: Path) -> None:
    assert intent_violation_gate_block(tmp_path) is None

    record_open_violation(
        tmp_path,
        claim_id=CLAIM,
        file_path=FILE,
        claim_text="No dual submission window",
        falsifier="pytest:tests/test_x.py::test_y",
        detail="exit 1",
    )
    block = intent_violation_gate_block(tmp_path)
    assert block is not None
    assert "BLOCKED" in block
    assert CLAIM in block
    assert "No dual submission window" in block

    assert clear_violation(tmp_path, claim_id=CLAIM, file_path=FILE) is True
    assert intent_violation_gate_block(tmp_path) is None
    assert open_violations(tmp_path) == []


def test_recording_the_same_violation_twice_is_idempotent(tmp_path: Path) -> None:
    for _ in range(3):
        record_open_violation(tmp_path, claim_id=CLAIM, file_path=FILE, claim_text="t", falsifier="pytest:x::y")
    assert len(open_violations(tmp_path)) == 1


def test_clearing_an_unknown_violation_is_a_no_op(tmp_path: Path) -> None:
    assert clear_violation(tmp_path, claim_id="ghost", file_path=FILE) is False


# ── probe round: the open-violation marker must not fail OPEN ──────────────


def _open_one(root: Path) -> None:
    record_open_violation(
        root, claim_id=CLAIM, file_path=FILE, claim_text="No dual submission", falsifier="pytest:x::y"
    )


def test_corrupt_marker_fails_closed(tmp_path: Path) -> None:
    """An unparseable enforcement marker must BLOCK, never silently clear."""
    _open_one(tmp_path)
    violations_path(tmp_path).write_text("{not json", encoding="utf-8")
    block = intent_violation_gate_block(tmp_path)
    assert block is not None
    assert "corrupt" in block.lower()


def test_deleting_the_marker_does_not_clear_a_ledgered_violation(tmp_path: Path) -> None:
    """The append-only ledger is the durable authority; the marker is a cache."""
    from trw_mcp.security.intent_contract.ledger import record_violation

    _open_one(tmp_path)
    record_violation(claim_id=CLAIM, file_path=FILE, session_id="s", reason="falsifier failed", root=tmp_path)
    violations_path(tmp_path).unlink()
    assert intent_violation_gate_block(tmp_path) is not None


def test_a_ledgered_resolution_clears_the_block(tmp_path: Path) -> None:
    from trw_mcp.security.intent_contract.ledger import record_violation

    record_violation(claim_id=CLAIM, file_path=FILE, session_id="s", reason="falsifier failed", root=tmp_path)
    assert intent_violation_gate_block(tmp_path) is not None
    clear_violation(tmp_path, claim_id=CLAIM, file_path=FILE, session_id="s")
    assert intent_violation_gate_block(tmp_path) is None


def test_concurrent_record_and_clear_do_not_lose_a_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 8: load-modify-store must hold one lock, or a stale snapshot wins."""
    import threading
    import time

    from trw_mcp.security.intent_contract import violations as violations_module
    from trw_mcp.security.intent_contract._atomic_json import atomic_write_json as original_store

    record_open_violation(tmp_path, claim_id="C-other", file_path="other.py", claim_text="t", falsifier="pytest:a::b")

    delay_done = threading.Event()

    def _slow_store(path: Path, entries: object) -> None:
        # Widen the load->store window so an unlocked implementation loses a write.
        if not delay_done.is_set():
            delay_done.set()
            time.sleep(0.2)
        original_store(path, entries)

    # Patched on `violations`, not on `_atomic_json`, so only THIS module's
    # load->store window widens — the shared helper also backs the ledger.
    monkeypatch.setattr(violations_module, "atomic_write_json", _slow_store)
    writer = threading.Thread(target=_open_one, args=(tmp_path,))
    clearer = threading.Thread(
        target=clear_violation,
        kwargs={"root": tmp_path, "claim_id": "C-other", "file_path": "other.py"},
    )
    writer.start()
    time.sleep(0.05)
    clearer.start()
    writer.join()
    clearer.join()

    keys = {(item["claim_id"], item["file_path"]) for item in open_violations(tmp_path)}
    assert (CLAIM, FILE) in keys, "a concurrent clear overwrote another claim's open violation"
    assert ("C-other", "other.py") not in keys, "a concurrent record resurrected a cleared violation"
    assert intent_violation_gate_block(tmp_path) is not None
