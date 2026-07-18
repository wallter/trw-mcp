"""PRD-CORE-208 LIVE WIRING: the journal owns the real trw_deliver path.

These exercise the wiring (not the substrate in isolation): a real
``trw_deliver`` invocation claims a caller-stable operation FIRST (FR01), journals
each synchronous critical effect (FR02), records the deferred batch (FR06), keeps
the legacy no-ID path compatible (NFR01), and proves the production dispatcher
reaches the registry's declared synchronous + deferred effect families (FR03 /
FPI-8 dispatcher-reachability). The public status/recover tools are registered
and read the live operation (FR05/FR04).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server
from tests._delivery_support import make_uuid7, strong_capability


def _seed_run(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    for sub in ("learnings/entries", "reflections", "context"):
        (trw_dir / sub).mkdir(parents=True, exist_ok=True)
    run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(
        "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n", encoding="utf-8"
    )
    (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    return run_dir


def _deliver(tools: dict, tmp_path: Path, run_dir: Path, **kwargs: object) -> dict:
    """Drive a real trw_deliver past the gate with the standard patch set."""
    trw_dir = tmp_path / ".trw"
    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
        patch(
            "trw_mcp.tools.ceremony._do_instruction_sync",
            return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
        ),
        patch(
            "trw_mcp.tools._deferred_delivery._do_index_sync",
            return_value={"status": "success", "index": {}, "roadmap": {}},
        ),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
    ):
        return tools["trw_deliver"].fn(
            allow_unverified=True,
            unverified_reason="test fixture: no build_check recorded for this synthetic run",
            **kwargs,
        )


def _join_deferred() -> None:
    from trw_mcp.tools import _deferred_state as _ds

    thread = _ds._deferred_thread
    if thread is not None:
        thread.join(timeout=30)


def _coord(tmp_path: Path):  # type: ignore[no-untyped-def]
    from trw_mcp.tools._delivery_operations import DeliveryCoordinator

    return DeliveryCoordinator(tmp_path / ".trw")


@pytest.mark.integration
def test_explicit_id_claim_is_bound_and_recoverable(tmp_path, monkeypatch) -> None:
    """FR01: an explicit delivery_id is claimed and reported caller-recoverable."""
    tools = _make_ceremony_server(monkeypatch, tmp_path)
    run_dir = _seed_run(tmp_path)
    did = make_uuid7()

    result = _deliver(tools, tmp_path, run_dir, delivery_id=did, capability_token=strong_capability())

    op = result["delivery_operation"]
    assert op["operation_id"] == did
    assert op["caller_recoverable"] is True
    assert op["enabled"] is True
    # The operation is durable and readable after the call (timeout recovery).
    status = _coord(tmp_path).project_status(did)
    assert status["result"] == "ok"
    assert status["operation_id"] == did


@pytest.mark.integration
def test_conflicting_explicit_id_returns_zero_effect_conflict(tmp_path, monkeypatch) -> None:
    """FR01: reusing an ID with a different bound request yields zero effects."""
    tools = _make_ceremony_server(monkeypatch, tmp_path)
    run_dir = _seed_run(tmp_path)
    did = make_uuid7()
    cap = strong_capability()

    first = _deliver(tools, tmp_path, run_dir, delivery_id=did, capability_token=cap, skip_index_sync=False)
    assert first["delivery_operation"]["caller_recoverable"] is True
    _join_deferred()

    # Same ID, DIFFERENT bound field (skip_index_sync) -> conflict, zero effects.
    second = _deliver(tools, tmp_path, run_dir, delivery_id=did, capability_token=cap, skip_index_sync=True)
    assert second["success"] is False
    assert "delivery_request_conflict" in second["delivery_blocked"]
    assert second["delivery_operation"]["effect_calls"] == 0
    # Zero delivery effects: the conflict returned BEFORE reflect/checkpoint ran.
    assert "reflect" not in second
    assert "checkpoint" not in second


@pytest.mark.integration
def test_live_delivery_dispatches_registered_synchronous_effects(tmp_path, monkeypatch) -> None:
    """FR03 / FPI-8: the production path journals the declared synchronous families."""
    from trw_mcp.tools._delivery_tracer import (
        SYNCHRONOUS_DISPATCH_EFFECTS,
        reconcile_runtime_dispatch,
        trace_journaled_effects,
    )

    tools = _make_ceremony_server(monkeypatch, tmp_path)
    run_dir = _seed_run(tmp_path)
    did = make_uuid7()

    _deliver(tools, tmp_path, run_dir, delivery_id=did, capability_token=strong_capability())

    coord = _coord(tmp_path)
    observed = trace_journaled_effects(coord, did)
    # Every synchronous dispatch effect is reachable AND every observed sync
    # effect resolves to exactly one descriptor (no unclassified mutation).
    report = reconcile_runtime_dispatch(
        observed & {e for e in observed if e.startswith("S")}, expected=SYNCHRONOUS_DISPATCH_EFFECTS
    )
    assert report["orphan"] == ()
    assert report["uncovered"] == ()
    assert SYNCHRONOUS_DISPATCH_EFFECTS <= observed


@pytest.mark.integration
def test_live_deferred_batch_journals_trust_increment(tmp_path, monkeypatch) -> None:
    """FR02/FR03: the live deferred batch journals D16 so a kill is crash-safe.

    A ``started``-before / terminal-after D16 (NON_REPLAYABLE trust increment)
    means a process death mid-batch leaves the durable ``started`` step FR04
    recovery marks indeterminate — the exact FPI-1 property, now on the LIVE path.
    """
    from trw_mcp.tools._delivery_tracer import DEFERRED_DISPATCH_EFFECTS

    tools = _make_ceremony_server(monkeypatch, tmp_path)
    run_dir = _seed_run(tmp_path)
    did = make_uuid7()

    _deliver(tools, tmp_path, run_dir, delivery_id=did, capability_token=strong_capability())
    _join_deferred()

    status = _coord(tmp_path).project_status(did)
    steps = status["steps"]
    assert steps["D16"]["state"] in {"started", "succeeded"}
    # The live deferred batch reaches the declared deferred families (no ghosts).
    # Compact mode omits not_started steps, so absence == not_started here.
    journaled = {
        eid for eid in DEFERRED_DISPATCH_EFFECTS if steps.get(eid, {}).get("state", "not_started") != "not_started"
    }
    assert "D16" in journaled
    assert journaled, "deferred batch journaled no roster step"


@pytest.mark.integration
def test_live_deferred_batch_finalizes_operation_success(tmp_path, monkeypatch) -> None:
    """FR02/FR05: a completed live batch becomes aggregate success."""
    tools = _make_ceremony_server(monkeypatch, tmp_path)
    run_dir = _seed_run(tmp_path)
    did = make_uuid7()
    cap = strong_capability()

    _deliver(tools, tmp_path, run_dir, delivery_id=did, capability_token=cap)
    _join_deferred()

    status = _coord(tmp_path).project_status(did)
    assert status["state"] == "succeeded"
    assert status["aggregate_success"] is True

    retry = _deliver(tools, tmp_path, run_dir, delivery_id=did, capability_token=cap)
    assert retry["success"] is True
    assert retry["delivery_operation"]["reason_code"] == "already_succeeded"
    assert retry["delivery_operation"]["effect_calls"] == 0


@pytest.mark.integration
def test_live_deferred_batch_finalizes_operation_failure(tmp_path, monkeypatch) -> None:
    """FR02/FR05: a captured deferred-step error makes the operation failed."""
    tools = _make_ceremony_server(monkeypatch, tmp_path)
    run_dir = _seed_run(tmp_path)
    did = make_uuid7()

    with patch("trw_mcp.tools._deferred_delivery._step_consolidation", side_effect=RuntimeError("boom")):
        _deliver(tools, tmp_path, run_dir, delivery_id=did, capability_token=strong_capability())
        _join_deferred()

    status = _coord(tmp_path).project_status(did)
    assert status["state"] == "failed"
    assert status["aggregate_success"] is False


@pytest.mark.integration
def test_idempotent_retry_of_same_request_follows_existing_operation(tmp_path, monkeypatch) -> None:
    """FR01: an identical retry resolves to the same operation without replay."""
    tools = _make_ceremony_server(monkeypatch, tmp_path)
    run_dir = _seed_run(tmp_path)
    did = make_uuid7()
    cap = strong_capability()

    first = _deliver(tools, tmp_path, run_dir, delivery_id=did, capability_token=cap)
    _join_deferred()
    second = _deliver(tools, tmp_path, run_dir, delivery_id=did, capability_token=cap)

    # A completed operation returns its terminal success projection with zero
    # new effect calls; incomplete operations still require status/recovery.
    assert second["success"] is True
    assert second["delivery_operation"]["reason_code"] == "already_succeeded"
    assert second["delivery_operation"]["operation_id"] == did == first["delivery_operation"]["operation_id"]
    assert second["delivery_operation"]["effect_calls"] == 0


def test_prd_core_215_nfr04() -> None:
    """PRD-CORE-215 NFR04 — no compatibility escape.

    A legacy result projector (``ToolResultEnvelope.from_legacy`` with a
    ``compatibility`` record) may keep a conflicting legacy outcome ONLY while an
    approved, named-external, complete, UNEXPIRED CompatibilityException is active.
    No-exception fixtures apply no projector (the typed outcome stays
    authoritative); expired, incomplete, or internal-only records cannot rescue
    the decision — the outcome degrades to ``uncertain``.
    """
    from datetime import datetime, timedelta, timezone

    from trw_mcp.models.tool_result import (
        CompatibilityException,
        Outcome,
        ToolResultEnvelope,
        compatibility_permits_projection,
    )

    now = datetime(2026, 7, 12, tzinfo=timezone.utc)
    conflicting = {"success": True, "status": "success"}  # legacy asserts success

    def _record(**overrides: object) -> CompatibilityException:
        base: dict[str, object] = {
            "external_caller": "acme-ci",
            "breakage_evidence": "external build parses the legacy result dict",
            "migration_owner": "team-mcp",
            "expiry_iso": (now + timedelta(days=30)).isoformat(),
            "telemetry_field": "compat.acme_ci",
            "removal_test_ref": "tests/test_delivery_wiring.py::test_prd_core_215_nfr04",
        }
        base.update(overrides)
        return CompatibilityException(**base)  # type: ignore[arg-type]

    # (a) No exception -> NO projector -> typed REJECTED stays authoritative; the
    # legacy "success" is recorded as a conflict but cannot rescue the decision.
    no_exc = ToolResultEnvelope.from_legacy(outcome=Outcome.REJECTED, legacy=conflicting)
    assert no_exc.outcome is Outcome.REJECTED
    assert no_exc.legacy_conflicts
    assert "compatibility.refused" not in no_exc.diagnostics

    # (b) Active approved external exception -> projector documented until expiry;
    # the typed result REMAINS authoritative and telemetry names the caller.
    active = _record()
    assert compatibility_permits_projection(active, now=now) is True
    kept = ToolResultEnvelope.from_legacy(outcome=Outcome.REJECTED, legacy=conflicting, compatibility=active, now=now)
    assert kept.outcome is Outcome.REJECTED
    assert kept.diagnostics["compatibility.external_caller"] == "acme-ci"
    assert kept.diagnostics["compatibility.telemetry_field"] == "compat.acme_ci"

    # (c) EXPIRED exception cannot rescue -> outcome degrades to uncertain.
    expired = _record(expiry_iso=(now - timedelta(days=1)).isoformat())
    assert compatibility_permits_projection(expired, now=now) is False
    degraded = ToolResultEnvelope.from_legacy(
        outcome=Outcome.REJECTED, legacy=conflicting, compatibility=expired, now=now
    )
    assert degraded.outcome is Outcome.UNCERTAIN
    assert degraded.diagnostics["compatibility.refused"]

    # (d) INCOMPLETE record (missing removal test ref) is refused -> uncertain.
    incomplete = _record(removal_test_ref="")
    assert compatibility_permits_projection(incomplete, now=now) is False
    inc_env = ToolResultEnvelope.from_legacy(
        outcome=Outcome.REJECTED, legacy=conflicting, compatibility=incomplete, now=now
    )
    assert inc_env.outcome is Outcome.UNCERTAIN

    # (e) INTERNAL-ONLY record (no named external caller) is refused.
    internal_only = _record(external_caller="")
    assert compatibility_permits_projection(internal_only, now=now) is False
    internal_env = ToolResultEnvelope.from_legacy(
        outcome=Outcome.REJECTED, legacy=conflicting, compatibility=internal_only, now=now
    )
    assert internal_env.outcome is Outcome.UNCERTAIN

    # (f) A malformed expiry string fails closed rather than raising.
    assert compatibility_permits_projection(_record(expiry_iso="not-a-date"), now=now) is False


@pytest.mark.integration
def test_delivery_status_and_recover_tools_are_registered_and_read_live_op(tmp_path, monkeypatch) -> None:
    """FR05/FR04: the public tools read the live operation without mutating it."""
    from fastmcp import FastMCP

    from trw_mcp.tools.delivery_ops import register_delivery_tools

    tools = _make_ceremony_server(monkeypatch, tmp_path)
    run_dir = _seed_run(tmp_path)
    did = make_uuid7()
    _deliver(tools, tmp_path, run_dir, delivery_id=did, capability_token=strong_capability())

    server = FastMCP("t")
    register_delivery_tools(server)
    from tests.conftest import extract_tool_fn

    status_fn = extract_tool_fn(server, "trw_delivery_status")
    recover_fn = extract_tool_fn(server, "trw_delivery_recover")
    with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=tmp_path / ".trw"):
        status = status_fn(delivery_id=did)
        # A read-only status call never authorizes recovery; an unknown action is
        # refused, not fabricated.
        bad = recover_fn(delivery_id=did, action="nope")
    assert status["result"] == "ok"
    assert status["operation_id"] == did
    assert bad["result"] == "unsupported_action"
