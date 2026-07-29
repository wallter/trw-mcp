"""Ceremony-obligation consequences must match the gate that enforces them.

These obligations are written into ``compact_instructions.txt`` and
``pre_compact_state.json`` — read by the next session right after a compaction,
when the agent can least afford to verify a claim. A consequence asserted here
and not enforced by the deliver path is a CONSTITUTION HB-1 violation, so each
test below pins a consequence to the config that actually decides it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.tools import _checkpoint_obligations as obligations
from trw_mcp.tools._checkpoint_obligations import compute_pending_ceremony

_ALL_PENDING: dict[str, object] = {}


def _row(pending: list[str], tool: str) -> str:
    """Return the single obligation line for *tool*, asserting it is present."""
    matches = [line for line in pending if line.startswith(f"{tool} —")]
    assert matches, f"{tool} obligation missing from {pending}"
    return matches[0]


def test_satisfied_obligations_are_omitted() -> None:
    """Only pending obligations are rendered — a done one carries no line."""
    pending = compute_pending_ceremony(
        {"session_started": True, "build_checked": True, "review_done": True, "delivered": True}
    )
    assert pending == []


def test_all_four_obligations_render_when_pending() -> None:
    """The full ceremony set stays visible; this fix corrects wording, not coverage."""
    pending = compute_pending_ceremony(_ALL_PENDING)
    tools = [line.split(" — ")[0] for line in pending]
    assert tools == ["trw_session_start()", "trw_build_check()", "trw_review()", "trw_deliver()"]


def test_build_obligation_claims_block_only_when_the_gate_would_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build consequence tracks ``_build_gate_would_block``, not a fixed string."""
    monkeypatch.setattr(obligations, "_build_consequence", lambda run_dir: obligations._BLOCKS)
    assert obligations._BLOCKS in _row(compute_pending_ceremony(_ALL_PENDING), "trw_build_check()")


def test_build_obligation_is_advisory_for_a_non_blocking_task_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A docs run delivers with no build check, so the line must NOT claim a block.

    This is the regression the module exists for: the old flat string said
    "required before delivery" for every run, including the majority for which
    the shipped ``deliver_gate_mode=block_coding`` never blocks.
    """
    import trw_mcp.tools._orchestration_gate_scan as gate_scan

    monkeypatch.setattr(gate_scan, "_build_gate_would_block", lambda run_path, missing_build: False)
    line = _row(compute_pending_ceremony(_ALL_PENDING, run_dir=tmp_path), "trw_build_check()")
    assert obligations._BLOCKS not in line
    assert "recommended" in line


def test_review_obligation_claims_block_when_the_review_gate_would_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under a blocking review gate the line must say so, not "recommended".

    The old string under-claimed: an agent reading "recommended" skips review
    and then hits a hard block at deliver time.
    """
    import trw_mcp.tools._orchestration_gate_scan as gate_scan

    monkeypatch.setattr(gate_scan, "_review_gate_would_block", lambda run_path, events: True)
    line = _row(compute_pending_ceremony(_ALL_PENDING, run_dir=tmp_path), "trw_review()")
    assert obligations._BLOCKS in line


def test_review_obligation_is_advisory_under_warn_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shipped default ``review_gate_mode=warn`` delivers without a review."""
    import trw_mcp.tools._orchestration_gate_scan as gate_scan

    monkeypatch.setattr(gate_scan, "_review_gate_would_block", lambda run_path, events: False)
    line = _row(compute_pending_ceremony(_ALL_PENDING, run_dir=tmp_path), "trw_review()")
    assert obligations._BLOCKS not in line


def test_no_run_degrades_to_advisory_never_to_a_claimed_block() -> None:
    """With no resolvable run, neither gate can be evaluated — so neither claims a block."""
    pending = compute_pending_ceremony(_ALL_PENDING, run_dir=None)
    assert obligations._BLOCKS not in _row(pending, "trw_build_check()")
    assert obligations._BLOCKS not in _row(pending, "trw_review()")


def test_gate_predicate_failure_degrades_to_advisory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising gate predicate must weaken the claim, never propagate or over-claim."""
    import trw_mcp.tools._orchestration_gate_scan as gate_scan

    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("gate unavailable")

    monkeypatch.setattr(gate_scan, "_build_gate_would_block", _boom)
    monkeypatch.setattr(gate_scan, "_review_gate_would_block", _boom)
    pending = compute_pending_ceremony(_ALL_PENDING, run_dir=tmp_path)
    assert obligations._BLOCKS not in _row(pending, "trw_build_check()")
    assert obligations._BLOCKS not in _row(pending, "trw_review()")


def test_deliver_obligation_states_loss_not_an_unenforced_gate() -> None:
    """No tool blocks on a missing trw_deliver, so the line must not imply one.

    The obligation is real (VISION Principle 5) and stays in the list; what it
    may not do is borrow the authority of a gate that does not exist.
    """
    line = _row(compute_pending_ceremony(_ALL_PENDING), "trw_deliver()")
    assert "persists this session's learnings" in line
    assert "block" not in line.lower()


def test_no_obligation_line_claims_an_unproven_block() -> None:
    """Whole-surface guard: with every gate non-blocking, no line may say "blocks"."""
    import trw_mcp.tools._orchestration_gate_scan as gate_scan

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gate_scan, "_build_gate_would_block", lambda run_path, missing_build: False)
        mp.setattr(gate_scan, "_review_gate_would_block", lambda run_path, events: False)
        pending = compute_pending_ceremony(_ALL_PENDING, run_dir=None)
    assert not [line for line in pending if "blocks" in line.lower()]
