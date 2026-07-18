"""Exact state, numeric-limit, renewal, exception, and projection-authority matrix
for state/requirements_registry.py (PRD-QUAL-121 FR03/FR04, NFR01)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from trw_mcp.models.requirements import ExecutionState, PrdActiveLimits
from trw_mcp.state.requirements_registry import (
    GENESIS_DIGEST,
    RegistryWriter,
    SchedulingLedgerError,
    action_digest,
    build_registry,
    derive_evaluation_epoch,
    evaluate_activation,
    load_ledger,
    persist_registry,
)

AUTH = "receipt-test-0001"


def _write_prd(
    prds_dir: Path,
    prd_id: str,
    *,
    status: str = "approved",
    priority: str = "P0",
    owner: str = "",
    updated: str = "2026-07-01",
) -> None:
    prds_dir.mkdir(parents=True, exist_ok=True)
    owner_line = f"\n  owner: {owner}" if owner else ""
    (prds_dir / f"{prd_id}.md").write_text(
        f"---\nprd:\n  id: {prd_id}\n  title: {prd_id} title\n  status: {status}\n"
        f"  priority: {priority}\n  category: CORE{owner_line}\n"
        f"  dates:\n    updated: '{updated}'\n---\n# {prd_id}\n",
        encoding="utf-8",
    )


def _writer(tmp_path: Path, today: str = "2026-07-11") -> tuple[RegistryWriter, Path]:
    ledger = tmp_path / "registry" / "scheduling-ledger.jsonl"
    return RegistryWriter(ledger, utc_today=lambda: date.fromisoformat(today)), ledger


# --- ledger chain integrity -------------------------------------------------


class TestSchedulingLedger:
    def test_empty_ledger_derives_genesis_epoch(self, tmp_path: Path) -> None:
        _, ledger = _writer(tmp_path)
        actions = load_ledger(ledger)
        epoch = derive_evaluation_epoch(actions)
        assert epoch.sequence == 0
        assert epoch.ledger_head_digest == GENESIS_DIGEST

    def test_appends_chain_and_epoch_advances(self, tmp_path: Path) -> None:
        writer, ledger = _writer(tmp_path)
        first = writer.advance_evaluation_epoch(authorization_receipt=AUTH, actor="operator")
        second = writer.advance_evaluation_epoch(authorization_receipt=AUTH, actor="operator")
        assert second.sequence == 2
        assert second.previous_action_digest == action_digest(first)
        epoch = derive_evaluation_epoch(load_ledger(ledger))
        assert epoch.sequence == 2
        assert epoch.effective_utc_date == "2026-07-11"

    def test_no_api_surface_accepts_a_caller_date(self, tmp_path: Path) -> None:
        # Callers cannot supply an epoch or a date: the append methods take none.
        writer, _ = _writer(tmp_path)
        with pytest.raises(TypeError):
            writer.advance_evaluation_epoch(  # type: ignore[call-arg]
                effective_utc_date="2030-01-01", authorization_receipt=AUTH, actor="x"
            )

    def test_tampered_chain_is_a_typed_failure(self, tmp_path: Path) -> None:
        writer, ledger = _writer(tmp_path)
        writer.advance_evaluation_epoch(authorization_receipt=AUTH, actor="operator")
        writer.renew("PRD-CORE-001", authorization_receipt=AUTH, actor="operator")
        lines = ledger.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[1])
        tampered["previous_action_digest"] = "0" * 64  # fork / stale head
        ledger.write_text(lines[0] + "\n" + json.dumps(tampered) + "\n", encoding="utf-8")
        with pytest.raises(SchedulingLedgerError, match="stale or forked"):
            load_ledger(ledger)

    def test_sequence_gap_is_a_typed_failure(self, tmp_path: Path) -> None:
        writer, ledger = _writer(tmp_path)
        writer.advance_evaluation_epoch(authorization_receipt=AUTH, actor="operator")
        record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
        record["sequence"] = 5
        ledger.write_text(json.dumps(record) + "\n", encoding="utf-8")
        with pytest.raises(SchedulingLedgerError, match="gap or fork"):
            load_ledger(ledger)

    def test_rollback_date_advance_is_rejected(self, tmp_path: Path) -> None:
        ledger = tmp_path / "registry" / "ledger.jsonl"
        RegistryWriter(ledger, utc_today=lambda: date(2026, 7, 11)).advance_evaluation_epoch(
            authorization_receipt=AUTH, actor="operator"
        )
        past_writer = RegistryWriter(ledger, utc_today=lambda: date(2026, 7, 1))
        with pytest.raises(SchedulingLedgerError, match="roll back"):
            past_writer.advance_evaluation_epoch(authorization_receipt=AUTH, actor="operator")

    def test_missing_authorization_receipt_is_rejected_without_write(self, tmp_path: Path) -> None:
        writer, ledger = _writer(tmp_path)
        with pytest.raises(SchedulingLedgerError, match="authorization"):
            writer.advance_evaluation_epoch(authorization_receipt="  ", actor="operator")
        assert not ledger.exists()

    def test_stale_ledger_yields_unknown_registry_that_cannot_activate(self, tmp_path: Path) -> None:
        writer, ledger = _writer(tmp_path)
        writer.advance_evaluation_epoch(authorization_receipt=AUTH, actor="operator")
        writer.renew("PRD-CORE-001", authorization_receipt=AUTH, actor="operator")
        # Rewriting a NON-TAIL action breaks the successor's chained digest.
        lines = ledger.read_text(encoding="utf-8").splitlines()
        ledger.write_text(lines[0].replace(AUTH, "forged") + "\n" + lines[1] + "\n", encoding="utf-8")
        prds = tmp_path / "prds"
        _write_prd(prds, "PRD-CORE-001")
        registry = build_registry(prds, ledger)
        assert registry.status == "stale_scheduling_head"
        decision = evaluate_activation(registry, "PRD-CORE-001")
        assert not decision.allowed
        assert "unknown" in decision.reason


# --- registry build + NFR01 determinism --------------------------------------


class TestRegistryBuild:
    def test_terminal_statuses_are_not_executable(self, tmp_path: Path) -> None:
        prds = tmp_path / "prds"
        _write_prd(prds, "PRD-CORE-001", status="approved")
        _write_prd(prds, "PRD-CORE-002", status="done")
        _write_prd(prds, "PRD-CORE-003", status="implemented")
        registry = build_registry(prds, tmp_path / "ledger.jsonl")
        assert [entry.prd_id for entry in registry.entries] == ["PRD-CORE-001"]

    def test_nfr01_same_inputs_produce_byte_identical_registry(self, tmp_path: Path) -> None:
        """NFR01: same commit/config/ledger head -> byte-identical canonical bytes;
        ambient wall-clock is not an input (build_registry takes no clock)."""
        prds = tmp_path / "prds"
        _write_prd(prds, "PRD-CORE-001")
        writer, ledger = _writer(tmp_path)
        writer.advance_evaluation_epoch(authorization_receipt=AUTH, actor="operator")

        first = build_registry(prds, ledger)
        second = build_registry(prds, ledger)
        assert first.canonical_bytes() == second.canonical_bytes()
        assert first.receipt_digest() == second.receipt_digest()
        assert first.receipt_digest().startswith("sha256:")

    def test_set_execution_state_and_owner_applied_from_ledger(self, tmp_path: Path) -> None:
        prds = tmp_path / "prds"
        _write_prd(prds, "PRD-CORE-001")
        writer, ledger = _writer(tmp_path)
        writer.set_execution_state(
            "PRD-CORE-001",
            ExecutionState.ACTIVE,
            prds_dir=prds,
            authorization_receipt=AUTH,
            actor="operator",
            owner="team-a",
        )
        registry = build_registry(prds, ledger)
        entry = registry.entries[0]
        assert str(entry.execution_state) == "active"
        assert entry.owner == "team-a"
        assert entry.renewal_date == "2026-07-11"

    def test_expired_candidate_leaves_hot_path_without_lifecycle_change(self, tmp_path: Path) -> None:
        """FR04: expiry compares renewal date against the HEAD-DERIVED epoch, and
        removes the record from the hot path without touching lifecycle status."""
        prds = tmp_path / "prds"
        _write_prd(prds, "PRD-CORE-001", status="draft", updated="2026-05-01")  # 41 days stale
        writer, ledger = _writer(tmp_path, today="2026-06-11")
        writer.advance_evaluation_epoch(authorization_receipt=AUTH, actor="operator")
        registry = build_registry(prds, ledger)
        assert registry.expired == ["PRD-CORE-001"]
        assert registry.hot_path == []
        assert registry.entries[0].lifecycle_status == "draft"  # untouched

    def test_renew_restores_hot_path_membership(self, tmp_path: Path) -> None:
        prds = tmp_path / "prds"
        _write_prd(prds, "PRD-CORE-001", status="draft", updated="2026-05-01")
        writer, ledger = _writer(tmp_path, today="2026-06-11")
        writer.advance_evaluation_epoch(authorization_receipt=AUTH, actor="operator")
        writer.renew("PRD-CORE-001", authorization_receipt=AUTH, actor="operator")
        registry = build_registry(prds, ledger)
        assert registry.hot_path == ["PRD-CORE-001"]
        assert registry.expired == []

    def test_blocked_external_uses_seven_day_window(self, tmp_path: Path) -> None:
        prds = tmp_path / "prds"
        _write_prd(prds, "PRD-CORE-001")
        writer, ledger = _writer(tmp_path, today="2026-07-01")
        writer.set_execution_state(
            "PRD-CORE-001", ExecutionState.BLOCKED_EXTERNAL, prds_dir=prds, authorization_receipt=AUTH, actor="operator"
        )
        # Advance epoch 8 days later than the block action.
        late_writer = RegistryWriter(ledger, utc_today=lambda: date(2026, 7, 9))
        late_writer.advance_evaluation_epoch(authorization_receipt=AUTH, actor="operator")
        registry = build_registry(prds, ledger)
        assert registry.expired == ["PRD-CORE-001"]

    def test_limits_reject_unbounded_values(self) -> None:
        with pytest.raises(Exception):
            PrdActiveLimits(global_p0_active_max=10_000)

    def test_persist_registry_writes_canonical_document_and_receipt(self, tmp_path: Path) -> None:
        prds = tmp_path / "prds"
        _write_prd(prds, "PRD-CORE-001")
        registry = build_registry(prds, tmp_path / "ledger.jsonl")
        target = persist_registry(registry, tmp_path / "registry")
        stored = json.loads(target.read_text(encoding="utf-8"))
        assert stored["receipt_digest"] == registry.receipt_digest()
        assert stored["registry"]["schema"] == "requirements-registry/v1"


# --- WIP limit matrix (FR04) --------------------------------------------------


def _registry_with_wip(
    tmp_path: Path,
    active: list[tuple[str, str, str]],  # (prd_id, priority, owner)
    candidate: tuple[str, str, str],
    *,
    blocked: list[tuple[str, str]] | None = None,  # (prd_id, owner)
) -> object:
    prds = tmp_path / "prds"
    writer, ledger = _writer(tmp_path)
    for prd_id, priority, owner in active:
        _write_prd(prds, prd_id, priority=priority)
        writer.set_execution_state(
            prd_id, ExecutionState.ACTIVE, prds_dir=prds, authorization_receipt=AUTH, actor="operator", owner=owner
        )
    for prd_id, owner in blocked or []:
        _write_prd(prds, prd_id, priority="P0")
        writer.set_execution_state(
            prd_id,
            ExecutionState.BLOCKED_EXTERNAL,
            prds_dir=prds,
            authorization_receipt=AUTH,
            actor="operator",
            owner=owner,
        )
    prd_id, priority, owner = candidate
    _write_prd(prds, prd_id, priority=priority, owner=owner)
    return build_registry(prds, ledger)


class TestWipLimits:
    def test_fourth_global_p0_activation_fails_with_occupied_slots(self, tmp_path: Path) -> None:
        registry = _registry_with_wip(
            tmp_path,
            active=[("PRD-CORE-001", "P0", "a"), ("PRD-CORE-002", "P0", "b"), ("PRD-CORE-003", "P0", "c")],
            candidate=("PRD-CORE-004", "P0", "d"),
        )
        decision = evaluate_activation(registry, "PRD-CORE-004")  # type: ignore[arg-type]
        assert not decision.allowed
        assert "global P0 active limit 3" in decision.reason
        assert decision.occupied_slots == ["PRD-CORE-001", "PRD-CORE-002", "PRD-CORE-003"]

    def test_thirteenth_global_p0_p1_activation_fails(self, tmp_path: Path) -> None:
        # 2 P0 (under the P0 cap) + 10 P1 across many owners = 12 in WIP.
        active = [("PRD-CORE-00%d" % i, "P0", f"o{i}") for i in (1, 2)]
        active += [(f"PRD-CORE-0{i:02d}", "P1", f"o{i}") for i in range(3, 13)]
        registry = _registry_with_wip(tmp_path, active=active, candidate=("PRD-CORE-099", "P1", "z"))
        decision = evaluate_activation(registry, "PRD-CORE-099")  # type: ignore[arg-type]
        assert not decision.allowed
        assert "global P0/P1 active limit 12" in decision.reason
        assert len(decision.occupied_slots) == 12

    def test_second_owner_p0_activation_fails(self, tmp_path: Path) -> None:
        registry = _registry_with_wip(
            tmp_path,
            active=[("PRD-CORE-001", "P0", "team-a")],
            candidate=("PRD-CORE-002", "P0", "team-a"),
        )
        decision = evaluate_activation(registry, "PRD-CORE-002")  # type: ignore[arg-type]
        assert not decision.allowed
        assert "per-owner P0 active (team-a) limit 1" in decision.reason
        assert decision.occupied_slots == ["PRD-CORE-001"]

    def test_fourth_owner_p0_p1_activation_fails(self, tmp_path: Path) -> None:
        registry = _registry_with_wip(
            tmp_path,
            active=[
                ("PRD-CORE-001", "P1", "team-a"),
                ("PRD-CORE-002", "P1", "team-a"),
                ("PRD-CORE-003", "P1", "team-a"),
            ],
            candidate=("PRD-CORE-004", "P1", "team-a"),
        )
        decision = evaluate_activation(registry, "PRD-CORE-004")  # type: ignore[arg-type]
        assert not decision.allowed
        assert "per-owner P0/P1 active (team-a) limit 3" in decision.reason

    def test_second_owner_blocked_external_exception_fails(self, tmp_path: Path) -> None:
        """FR04: one blocked-external exception per ownership domain — the sole
        production mutation point (the writer) refuses BEFORE any ledger write."""
        from trw_mcp.state.requirements_registry import ActivationRefusedError

        prds = tmp_path / "prds"
        writer, ledger = _writer(tmp_path)
        _write_prd(prds, "PRD-CORE-001", priority="P0")
        writer.set_execution_state(
            "PRD-CORE-001",
            ExecutionState.BLOCKED_EXTERNAL,
            prds_dir=prds,
            authorization_receipt=AUTH,
            actor="op",
            owner="team-a",
        )
        _write_prd(prds, "PRD-CORE-002", priority="P2")
        ledger_before = ledger.read_text(encoding="utf-8")
        with pytest.raises(ActivationRefusedError) as excinfo:
            writer.set_execution_state(
                "PRD-CORE-002",
                ExecutionState.BLOCKED_EXTERNAL,
                prds_dir=prds,
                authorization_receipt=AUTH,
                actor="op",
                owner="team-a",
            )
        assert "blocked-external exception limit 1 for owner team-a" in str(excinfo.value)
        assert excinfo.value.occupied_slots == ["PRD-CORE-001"]
        assert ledger.read_text(encoding="utf-8") == ledger_before  # nothing appended

    def test_activation_within_limits_is_permitted(self, tmp_path: Path) -> None:
        registry = _registry_with_wip(
            tmp_path,
            active=[("PRD-CORE-001", "P0", "team-a")],
            candidate=("PRD-CORE-002", "P0", "team-b"),
        )
        decision = evaluate_activation(registry, "PRD-CORE-002")  # type: ignore[arg-type]
        assert decision.allowed


# --- anti-rollback anchor (adversarial-audit finding 4) -----------------------


class TestLedgerHeadAnchor:
    def test_truncated_ledger_is_stale_not_ok(self, tmp_path: Path) -> None:
        """A clean truncation is a valid prefix — only the anchor catches it."""
        prds = tmp_path / "prds"
        _write_prd(prds, "PRD-CORE-001")
        writer, ledger = _writer(tmp_path)
        writer.advance_evaluation_epoch(authorization_receipt=AUTH, actor="operator")
        writer.renew("PRD-CORE-001", authorization_receipt=AUTH, actor="operator")
        lines = ledger.read_text(encoding="utf-8").splitlines()
        ledger.write_text(lines[0] + "\n", encoding="utf-8")  # drop the tail
        registry = build_registry(prds, ledger)
        assert registry.status == "stale_scheduling_head"
        assert "anchor" in registry.error

    def test_tail_rewrite_is_stale_not_ok(self, tmp_path: Path) -> None:
        """Nothing chains atop the tail — the anchored head digest catches a rewrite."""
        prds = tmp_path / "prds"
        _write_prd(prds, "PRD-CORE-001")
        writer, ledger = _writer(tmp_path)
        writer.advance_evaluation_epoch(authorization_receipt=AUTH, actor="operator")
        ledger.write_text(ledger.read_text(encoding="utf-8").replace(AUTH, "forgedfrgd"), encoding="utf-8")
        registry = build_registry(prds, ledger)
        assert registry.status == "stale_scheduling_head"

    def test_stale_ledger_cannot_extend(self, tmp_path: Path) -> None:
        writer, ledger = _writer(tmp_path)
        writer.advance_evaluation_epoch(authorization_receipt=AUTH, actor="operator")
        writer.renew("PRD-X", authorization_receipt=AUTH, actor="operator")
        lines = ledger.read_text(encoding="utf-8").splitlines()
        ledger.write_text(lines[0] + "\n", encoding="utf-8")
        with pytest.raises(SchedulingLedgerError, match="anchor"):
            writer.renew("PRD-Y", authorization_receipt=AUTH, actor="operator")


# --- NFR02 cross-process concurrency (P1 release-blocker) --------------------


class TestConcurrentActivationLock:
    """PRD-QUAL-121-NFR02: the WIP-limit gate + ledger append are one atomic
    critical section under a cross-process advisory lock. trw-mcp runs one OS
    process per MCP client, so two workers can race the sole mutation point;
    the ledger ``.lock`` (``lock_for_rmw``) is what keeps the invariant."""

    def test_two_racing_activations_respect_per_owner_wip_limit(self, tmp_path: Path) -> None:
        """Two workers race to activate a P0 for the same owner (per-owner P0
        limit = 1). The lock forces serialization: exactly one wins and one is
        refused — the registry never shows two owner-P0 actives. Without the
        lock both would read a 0-active registry, both pass the gate, and both
        append (invariant busted)."""
        import threading

        from trw_mcp.state.requirements_registry import ActivationRefusedError

        prds = tmp_path / "prds"
        _write_prd(prds, "PRD-CORE-001", priority="P0", owner="team-a")
        _write_prd(prds, "PRD-CORE-002", priority="P0", owner="team-a")
        writer, ledger = _writer(tmp_path)

        barrier = threading.Barrier(2)
        result_lock = threading.Lock()
        outcomes: dict[str, str] = {}

        def _activate(prd_id: str) -> None:
            barrier.wait()  # release both threads into the gate together
            try:
                writer.set_execution_state(
                    prd_id,
                    ExecutionState.ACTIVE,
                    prds_dir=prds,
                    authorization_receipt=AUTH,
                    actor="op",
                    owner="team-a",
                )
                verdict = "activated"
            except ActivationRefusedError:
                verdict = "refused"
            with result_lock:
                outcomes[prd_id] = verdict

        threads = [
            threading.Thread(target=_activate, args=(prd_id,))
            for prd_id in ("PRD-CORE-001", "PRD-CORE-002")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert sorted(outcomes.values()) == ["activated", "refused"], outcomes
        registry = build_registry(prds, ledger)
        assert registry.status == "ok"
        active = [
            entry.prd_id
            for entry in registry.entries
            if str(entry.execution_state) == ExecutionState.ACTIVE.value
        ]
        assert active == [outcome for outcome, verdict in outcomes.items() if verdict == "activated"]
        assert len(active) == 1, f"WIP invariant violated under contention: {active}"

    def test_concurrent_appends_produce_an_unbroken_hash_chain(self, tmp_path: Path) -> None:
        """Non-WIP appends racing through ``_append`` still serialize on the
        ledger lock, so the resulting chain is gap-free and anchor-consistent
        (a lost read-verify-append interleave would fork or gap the chain)."""
        import threading

        writer, ledger = _writer(tmp_path)
        barrier = threading.Barrier(5)

        def _renew(prd_id: str) -> None:
            barrier.wait()
            writer.renew(prd_id, authorization_receipt=AUTH, actor="op")

        threads = [threading.Thread(target=_renew, args=(f"PRD-CORE-{i:03d}",)) for i in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        actions = load_ledger(ledger)  # raises SchedulingLedgerError on gap/fork
        assert [action.sequence for action in actions] == [1, 2, 3, 4, 5]
