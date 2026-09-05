"""PRD-CORE-208 FR03: exhaustive executable delivery-effect inventory."""

from __future__ import annotations

import importlib

import pytest

from trw_mcp.tools._delivery_effect_registry import (
    DEFERRED_ROSTER_IDS,
    DELIVERY_EFFECT_REGISTRY,
    OperationStateImpact,
    ReplayClass,
    all_effect_ids,
    effects_by_replay_class,
    get_descriptor,
    is_auto_replayable_after_started,
    reconcile_static_roster,
    required_effect_ids,
)

# The approved §6.6 census: S01-S21 plus D00-D24, extended by PRD-CORE-244 FR09
# with D25 (the importance-decay deferred step, which mutates importance and
# appends outcome_history on every row it touches) and by PRD-CORE-249 FR02 with
# S22 (the deliver-time project-handoff write, a keyed marker-bounded merge into
# a checked-in file that outlives the run), and by PRD-FIX-127 FR05 with S23 (the
# gate decision-set receipt writes) and D26 (the meta-tune rollout linkage event
# append) -- two durable delivery mutations that had NO descriptor at all until the
# input/output tracer observed them on a live deliver.
_EXPECTED_IDS = frozenset([f"S{n:02d}" for n in range(1, 24)] + [f"D{n:02d}" for n in range(27)])

# Every ``owner_call_point`` value that appears in the census, mapped to the
# fully-qualified module it is DEFINED in (verified 2026-09-03, diagnostic
# finding item 2 round 1: S21's owner ``delivery_logger`` resolved to no symbol
# anywhere and the prior test only asserted truthiness; round 2: the round-1
# fix renamed the owner to ``run_trw_deliver``, the top-level ``trw_deliver``
# tool's entry-point function -- a real, resolvable, callable symbol, so the
# reachability test above passed, but it names the SCOPE the log lines are
# emitted within, not the function whose body actually emits them. The real
# writer is ``log_deliver_complete``, whose entire body is "Emit deliver_ok /
# deliver_failed / trw_deliver_complete log lines."). ``"Class.method"``
# names (e.g. ``BatchSender.send``) are resolved via attribute traversal
# below, not a dict lookup of the dotted string.
_OWNER_MODULES: dict[str, str] = {
    "try_update_phase": "trw_mcp.state.phase",
    "update_run_phase": "trw_mcp.state.phase",
    "copy_compliance_artifacts": "trw_mcp.tools._delivery_helpers",
    "write_override_ledger": "trw_mcp.tools._acceptable_failure_validation",
    "_log_gate_override": "trw_mcp.tools._deliver_gate_dispatch",
    "_persist_decision_set": "trw_mcp.tools._deliver_gate_dispatch",
    "_do_reflect": "trw_mcp.tools._ceremony_runtime_helpers",
    "update_analytics": "trw_mcp.state.analytics.counters",
    "_do_checkpoint": "trw_mcp.tools.checkpoint",
    "_probe_integrity": "trw_mcp.tools._ceremony_deliver_tool",
    "step_clear_score": "trw_mcp.tools._ceremony_deliver_steps",
    "step_knowledge_sync": "trw_mcp.tools._ceremony_deliver_steps",
    "step_session_changelog": "trw_mcp.tools._ceremony_deliver_steps",
    "step_project_handoff": "trw_mcp.tools._ceremony_deliver_steps",
    "mark_deliver": "trw_mcp.state._ceremony_progress_state",
    "_write_nudge_analysis_artifact": "trw_mcp.tools._ceremony_deliver_tool",
    "_log_deliver_event": "trw_mcp.tools._ceremony_deliver_tool",
    "log_deliver_complete": "trw_mcp.tools._ceremony_deliver_steps",
    "_try_acquire_deferred_lock": "trw_mcp.tools._deferred_delivery",
    "_step_auto_prune": "trw_mcp.tools._deferred_steps_memory",
    "_step_consolidation": "trw_mcp.tools._deferred_steps_memory",
    "_step_tier_sweep": "trw_mcp.tools._deferred_steps_memory",
    "_do_index_sync": "trw_mcp.tools._deferred_steps_learning",
    "_step_auto_progress": "trw_mcp.tools._deferred_steps_learning",
    "_step_publish_learnings": "trw_mcp.tools._deferred_steps_learning",
    "publish_learnings": "trw_mcp.telemetry.publisher",
    "_step_outcome_correlation": "trw_mcp.tools._deferred_steps_learning",
    "_step_recall_outcome": "trw_mcp.tools._deferred_steps_learning",
    "_step_telemetry": "trw_mcp.tools._deferred_steps_telemetry",
    "_step_batch_send": "trw_mcp.tools._deferred_steps_telemetry",
    "BatchSender.send": "trw_mcp.telemetry.sender",
    "_step_trust_increment": "trw_mcp.tools._deferred_steps_learning",
    "_step_ceremony_feedback": "trw_mcp.tools._deferred_steps_telemetry",
    "_process_ceremony_proposal": "trw_mcp.tools._deferred_steps_telemetry",
    "apply_auto_escalation": "trw_mcp.state._ceremony_escalation",
    "_persist_session_metrics": "trw_mcp.tools._deferred_persistence",
    "_persist_deferred_results": "trw_mcp.tools._deferred_persistence",
    "_log_deferred_result": "trw_mcp.tools._deferred_delivery",
    "_step_memory_decay": "trw_mcp.tools._deferred_steps_memory",
    "_step_delivery_metrics": "trw_mcp.tools._deferred_steps_learning",
}

#: Owners that are deliberately NOT a resolvable module:symbol (e.g. a
#: third-party/process boundary rather than in-repo code). Empty today —
#: every current owner is in-repo and importable — but kept as a typed,
#: documented escape so a genuinely non-code owner does not force a fake
#: module mapping into ``_OWNER_MODULES``.
_NON_CODE_OWNER_ALLOWLIST: frozenset[str] = frozenset()

#: Top-level MCP tool entry-point function names. None of these may appear as
#: an ``owner_call_point`` -- a tool entry point NAMES A SCOPE (everything
#: reachable from the tool call), not a single mutation's writer, so any
#: effect claiming one as its owner is unfalsifiable: the reachability test
#: above passes for a real, callable, but wrong symbol (round-2 regression,
#: S21, feedback-triage-framework-release-2026-09).
_TOOL_ENTRY_POINT_OWNERS: frozenset[str] = frozenset({"run_trw_deliver"})


def _resolve_owner_symbol(owner_call_point: str) -> object:
    """Import ``owner_call_point``'s module and resolve the symbol.

    Supports ``module:function`` style bare names (looked up in
    ``_OWNER_MODULES``) and ``Class.method`` attribute chains. Raises
    ``AssertionError``/``ImportError``/``AttributeError`` on any resolution
    failure -- the caller asserts callability.
    """
    module_path = _OWNER_MODULES[owner_call_point]
    module = importlib.import_module(module_path)
    target: object = module
    for part in owner_call_point.split("."):
        target = getattr(target, part)
    return target


def test_current_delivery_side_effect_inventory_is_exhaustive() -> None:
    """FR03: registry equals the approved §6.6 census with no gaps or duplicates."""
    assert all_effect_ids() == _EXPECTED_IDS
    assert len(DELIVERY_EFFECT_REGISTRY) == len(_EXPECTED_IDS) == 50
    # Every descriptor's own effect_id matches its dict key (no duplicate/orphan).
    for effect_id, descriptor in DELIVERY_EFFECT_REGISTRY.items():
        assert descriptor.effect_id == effect_id
        assert descriptor.owner_call_point  # reachable owner declared


def test_every_owner_call_point_resolves_to_a_real_importable_symbol() -> None:
    """FR03 reachability: every non-allowlisted owner is a real, callable symbol.

    Regression for diagnostic finding item 2 (feedback-triage-framework-
    release-2026-09): S21's owner ``owner_call_point="delivery_logger"`` was
    not defined anywhere -- the prior test asserted only truthiness
    (``assert descriptor.owner_call_point``), which a typo or a renamed/
    deleted function passes trivially. This resolves the owner's module and
    imports the real symbol, so a future dangling owner fails here instead.
    """
    for effect_id, descriptor in DELIVERY_EFFECT_REGISTRY.items():
        owner = descriptor.owner_call_point
        if owner in _NON_CODE_OWNER_ALLOWLIST:
            continue
        assert owner in _OWNER_MODULES, (
            f"{effect_id}: owner_call_point={owner!r} has no module mapping in "
            "_OWNER_MODULES and is not in _NON_CODE_OWNER_ALLOWLIST"
        )
        symbol = _resolve_owner_symbol(owner)
        assert callable(symbol), f"{effect_id}: resolved owner {owner!r} is not callable"


def test_owner_call_point_is_never_a_top_level_tool_entry_point() -> None:
    """A dangling-symbol check alone accepts a real-but-wrong owner (round-2 regression).

    S21's owner was fixed from a nonexistent ``delivery_logger`` to
    ``run_trw_deliver`` -- a real, resolvable, callable symbol, which made
    ``test_every_owner_call_point_resolves_to_a_real_importable_symbol`` pass
    even though ``run_trw_deliver`` is the ``trw_deliver`` tool's own entry
    point, not the function whose body emits S21's log lines. No registered
    effect IS the tool call itself (the tool call is the container every
    effect runs inside), so a top-level entry point can never be a truthful
    ``owner_call_point`` for any of them.
    """
    for effect_id, descriptor in DELIVERY_EFFECT_REGISTRY.items():
        assert descriptor.owner_call_point not in _TOOL_ENTRY_POINT_OWNERS, (
            f"{effect_id}: owner_call_point={descriptor.owner_call_point!r} names the "
            "top-level tool entry point (a scope), not the function that writes the effect"
        )


def test_thirteen_deferred_roster_and_post_batch_ids_present() -> None:
    """FR03 acceptance: 14 roster entries + post-batch + D00 lock are represented."""
    # D01-D13 roster, D14-D24 post-batch/nested, D25 memory decay, D26 meta-tune
    # rollout linkage, D00 lock.
    for n in range(27):
        assert f"D{n:02d}" in DEFERRED_ROSTER_IDS


def test_nonreplayable_effects_never_auto_replay_after_started() -> None:
    """FR04 authority lives in the registry, not code comments."""
    non_replayable = effects_by_replay_class(ReplayClass.NON_REPLAYABLE)
    assert non_replayable  # trust/telemetry/publish/destructive effects exist
    for descriptor in non_replayable:
        assert is_auto_replayable_after_started(descriptor.effect_id) is False
    # Trust increment (D16) and external sends (D07/D14) must be non-replayable.
    for effect_id in ("D16", "D07", "D14", "D01"):
        assert get_descriptor(effect_id).replay_class is ReplayClass.NON_REPLAYABLE
        assert is_auto_replayable_after_started(effect_id) is False


def test_diagnostic_and_coordination_are_not_success_authority() -> None:
    """Gate reads / lock release classified as diagnostic/coordination, reviewable."""
    assert get_descriptor("S21").replay_class is ReplayClass.DIAGNOSTIC
    assert get_descriptor("D00").replay_class is ReplayClass.COORDINATION
    assert get_descriptor("D11").replay_class is ReplayClass.COORDINATION
    # Diagnostic/coordination effects are optional — they never gate success.
    assert get_descriptor("S21").impact is OperationStateImpact.OPTIONAL
    assert get_descriptor("D00").impact is OperationStateImpact.OPTIONAL


def test_required_effects_gate_operation_success() -> None:
    """The required set names the critical mutations that must be proven."""
    required = required_effect_ids()
    for effect_id in ("S01", "S05", "S06", "S18", "S20", "D16"):
        assert effect_id in required


def test_reconcile_flags_orphan_and_missing_mutations() -> None:
    """FR03 census gate: an unregistered write fails; a missing owner fails."""
    clean = reconcile_static_roster(all_effect_ids())
    assert clean == {"missing": (), "orphan": (), "unclassified": ()}

    # A synthetic test-only write with no descriptor is an orphan (fails gate).
    with_orphan = reconcile_static_roster(all_effect_ids() | {"X99_synthetic_write"})
    assert with_orphan["orphan"] == ("X99_synthetic_write",)
    assert with_orphan["unclassified"] == ("X99_synthetic_write",)

    # A registered descriptor whose owner disappeared is missing.
    dropped = reconcile_static_roster(all_effect_ids() - {"D16"})
    assert dropped["missing"] == ("D16",)


def test_get_descriptor_unknown_raises() -> None:
    with pytest.raises(KeyError):
        get_descriptor("nope")


def test_registry_descriptors_are_immutable() -> None:
    """Frozen models: a descriptor cannot be mutated after construction (FR03)."""
    descriptor = get_descriptor("S01")
    with pytest.raises(Exception):
        descriptor.replay_class = ReplayClass.KEYED_IDEMPOTENT  # type: ignore[misc]
