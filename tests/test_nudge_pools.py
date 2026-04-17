"""Tests for PRD-CORE-129: Nudge Pool System.

Covers:
- NudgePoolWeights model validation (sum=100, rejection of bad sum)
- Profile integration (light profile has zero ceremony weight)
- Pool selection algorithm (weighted random, context bypass, cooldown)
- YAML content loading (workflow and ceremony pools)
- CeremonyState pool field round-trip serialization
- compute_nudge integration with pool selection
- Checkpoint threshold fix (files_modified > 10)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from trw_mcp.models.config._client_profile import NudgePoolWeights
from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.state._nudge_content import load_pool_message
from trw_mcp.state._nudge_rules import (
    _highest_priority_pending_step,
    _select_nudge_pool,
    apply_pool_cooldown,
    is_pool_in_cooldown,
)
from trw_mcp.state._nudge_state import (
    CeremonyState,
    NudgeContext,
    increment_tool_call_counter,
    read_ceremony_state,
    record_pool_ignore,
    record_pool_nudge,
    write_ceremony_state,
)

# ---------------------------------------------------------------------------
# NudgePoolWeights model tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_nudge_pool_weights_sum_100() -> None:
    """NudgePoolWeights with default fields sums to 100."""
    w = NudgePoolWeights()
    assert w.workflow + w.learnings + w.ceremony + w.context == 100


@pytest.mark.unit
def test_nudge_pool_weights_custom_sum_100() -> None:
    """NudgePoolWeights with custom fields summing to 100 constructs."""
    w = NudgePoolWeights(workflow=60, learnings=30, ceremony=0, context=10)
    assert w.workflow == 60
    assert w.ceremony == 0
    assert w.workflow + w.learnings + w.ceremony + w.context == 100


@pytest.mark.unit
def test_nudge_pool_weights_reject_bad_sum() -> None:
    """NudgePoolWeights with fields not summing to 100 raises ValidationError."""
    with pytest.raises(ValidationError, match="must sum to 100"):
        NudgePoolWeights(workflow=50, learnings=30, ceremony=20, context=20)


@pytest.mark.unit
def test_nudge_pool_weights_reject_sum_99() -> None:
    """NudgePoolWeights with sum=99 raises ValidationError."""
    with pytest.raises(ValidationError, match="must sum to 100"):
        NudgePoolWeights(workflow=39, learnings=30, ceremony=20, context=10)


@pytest.mark.unit
def test_nudge_pool_weights_reject_negative() -> None:
    """NudgePoolWeights rejects negative values via ge=0."""
    with pytest.raises(ValidationError):
        NudgePoolWeights(workflow=-10, learnings=30, ceremony=70, context=10)


@pytest.mark.unit
def test_nudge_pool_weights_frozen() -> None:
    """NudgePoolWeights is frozen — assignment raises."""
    w = NudgePoolWeights()
    with pytest.raises((ValidationError, TypeError)):
        w.workflow = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Profile integration tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_light_profile_has_zero_ceremony() -> None:
    """Light-mode profiles (opencode, codex, aider) have ceremony=0."""
    for client_id in ("opencode", "codex", "aider"):
        profile = resolve_client_profile(client_id)
        assert profile.nudge_pool_weights.ceremony == 0, f"{client_id} should have ceremony=0"
        # Verify sum still equals 100
        w = profile.nudge_pool_weights
        assert w.workflow + w.learnings + w.ceremony + w.context == 100


@pytest.mark.unit
def test_claude_code_profile_has_default_weights() -> None:
    """Claude Code profile uses default NudgePoolWeights (40/30/20/10)."""
    profile = resolve_client_profile("claude-code")
    w = profile.nudge_pool_weights
    assert w.workflow == 40
    assert w.learnings == 30
    assert w.ceremony == 20
    assert w.context == 10


@pytest.mark.unit
def test_cursor_ide_profile_has_custom_weights() -> None:
    """cursor-ide profile uses custom NudgePoolWeights (50/30/10/10)."""
    profile = resolve_client_profile("cursor-ide")
    w = profile.nudge_pool_weights
    assert w.workflow == 50
    assert w.learnings == 30
    assert w.ceremony == 10
    assert w.context == 10


@pytest.mark.unit
def test_copilot_profile_has_default_weights() -> None:
    """Copilot profile uses default NudgePoolWeights."""
    profile = resolve_client_profile("copilot")
    w = profile.nudge_pool_weights
    assert w.workflow == 40


# ---------------------------------------------------------------------------
# Pool selection algorithm tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_select_pool_returns_eligible() -> None:
    """_select_nudge_pool returns one of the eligible pool names."""
    state = CeremonyState()
    weights = NudgePoolWeights()
    results: set[str] = set()
    for _ in range(100):
        pool = _select_nudge_pool(state, weights)
        assert pool is not None
        results.add(pool)
    # With enough iterations, should see at least 2 different pools
    assert len(results) >= 2


@pytest.mark.unit
def test_select_pool_context_bypasses_weights() -> None:
    """Context pool is selected on build failure regardless of weights."""
    state = CeremonyState()
    # Weights with context=0 — should still return "context" on build failure
    weights = NudgePoolWeights(workflow=50, learnings=40, ceremony=10, context=0)
    context = NudgeContext(tool_name="build_check", build_passed=False)

    pool = _select_nudge_pool(state, weights, context)
    assert pool == "context"


@pytest.mark.unit
def test_select_pool_context_bypasses_on_p0() -> None:
    """Context pool is selected when P0 findings exist."""
    state = CeremonyState()
    weights = NudgePoolWeights(workflow=50, learnings=40, ceremony=10, context=0)
    context = NudgeContext(tool_name="review", review_p0_count=1)

    pool = _select_nudge_pool(state, weights, context)
    assert pool == "context"


@pytest.mark.unit
def test_select_pool_zero_weight_excluded() -> None:
    """Pools with weight=0 are never selected."""
    state = CeremonyState()
    weights = NudgePoolWeights(workflow=100, learnings=0, ceremony=0, context=0)

    for _ in range(50):
        pool = _select_nudge_pool(state, weights)
        assert pool == "workflow"


@pytest.mark.unit
def test_select_pool_all_zero_returns_none() -> None:
    """When all pools have weight=0 (impossible via validator but test edge)."""
    state = CeremonyState()
    # Use a weights object where all eligible pools are in cooldown
    weights = NudgePoolWeights()
    # Put all pools in cooldown (counter=0, cooldown_until=100 means in cooldown)
    state.pool_cooldown_until = {
        "workflow": 100,
        "learnings": 100,
        "ceremony": 100,
        "context": 100,
    }
    pool = _select_nudge_pool(state, weights)
    assert pool is None


# ---------------------------------------------------------------------------
# Cooldown tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cooldown_suppresses_pool() -> None:
    """Pool in cooldown is not selected."""
    state = CeremonyState(tool_call_counter=5)
    state.pool_cooldown_until["workflow"] = 15  # Cooldown until counter reaches 15

    assert is_pool_in_cooldown(state, "workflow") is True
    assert is_pool_in_cooldown(state, "learnings") is False


@pytest.mark.unit
def test_cooldown_expires_after_counter() -> None:
    """Pool cooldown expires when tool_call_counter reaches cooldown_until."""
    state = CeremonyState(tool_call_counter=15)
    state.pool_cooldown_until["workflow"] = 15

    assert is_pool_in_cooldown(state, "workflow") is False


@pytest.mark.unit
def test_apply_cooldown_activates() -> None:
    """apply_pool_cooldown activates when ignore count reaches threshold."""
    state = CeremonyState(tool_call_counter=10)
    state.pool_ignore_counts["workflow"] = 3

    activated = apply_pool_cooldown(state, "workflow", cooldown_after=3, cooldown_calls=10)
    assert activated is True
    assert state.pool_cooldown_until["workflow"] == 20  # 10 + 10
    assert state.pool_ignore_counts["workflow"] == 0  # Reset


@pytest.mark.unit
def test_apply_cooldown_not_activated() -> None:
    """apply_pool_cooldown does not activate when ignore count is below threshold."""
    state = CeremonyState(tool_call_counter=10)
    state.pool_ignore_counts["workflow"] = 2

    activated = apply_pool_cooldown(state, "workflow", cooldown_after=3, cooldown_calls=10)
    assert activated is False
    assert "workflow" not in state.pool_cooldown_until


# ---------------------------------------------------------------------------
# YAML content loading tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_workflow_yaml_loads() -> None:
    """Workflow pool YAML loads and contains messages."""
    msg = load_pool_message("workflow", phase_hint="implement")
    assert isinstance(msg, str)
    assert len(msg) > 0


@pytest.mark.unit
def test_ceremony_yaml_loads() -> None:
    """Ceremony pool YAML loads and returns content for known steps."""
    msg = load_pool_message("ceremony", phase_hint="session_start")
    assert isinstance(msg, str)
    assert "trw_session_start" in msg


@pytest.mark.unit
def test_load_pool_message_workflow_early() -> None:
    """Workflow pool returns early-phase message."""
    msg = load_pool_message("workflow", phase_hint="early")
    assert "test" in msg.lower() or "Read" in msg


@pytest.mark.unit
def test_load_pool_message_ceremony_deliver() -> None:
    """Ceremony pool returns deliver step message."""
    msg = load_pool_message("ceremony", phase_hint="deliver")
    assert "trw_deliver" in msg


@pytest.mark.unit
def test_load_pool_message_unknown_pool() -> None:
    """Unknown pool returns empty string."""
    msg = load_pool_message("nonexistent_pool", phase_hint="anything")
    assert msg == ""


@pytest.mark.unit
def test_load_pool_message_workflow_no_phase_hint() -> None:
    """Workflow pool returns a message even without phase_hint."""
    msg = load_pool_message("workflow")
    assert isinstance(msg, str)
    assert len(msg) > 0


@pytest.mark.unit
def test_load_pool_message_ceremony_no_phase_hint() -> None:
    """Ceremony pool with no phase_hint returns empty string (keyed format)."""
    msg = load_pool_message("ceremony")
    assert msg == ""


@pytest.mark.unit
def test_load_pool_yaml_malformed_fails_open() -> None:
    """Malformed YAML returns empty dict without raising (fail-open)."""
    from trw_mcp.state._nudge_content import _load_pool_yaml

    # Clear lru_cache so our mock takes effect
    _load_pool_yaml.cache_clear()
    try:
        with patch("trw_mcp.state._nudge_content._DATA_DIR", Path("/nonexistent/path")):
            result = _load_pool_yaml("malformed")
            assert result == {}
    finally:
        _load_pool_yaml.cache_clear()


@pytest.mark.unit
def test_load_pool_message_validate_phase() -> None:
    """Workflow pool returns validate-phase message."""
    msg = load_pool_message("workflow", phase_hint="validate")
    assert isinstance(msg, str)
    assert len(msg) > 0
    assert "test" in msg.lower() or "verify" in msg.lower()


@pytest.mark.unit
def test_load_pool_message_ceremony_all_steps() -> None:
    """Ceremony pool returns messages for all ceremony steps."""
    for step in ("session_start", "checkpoint", "build_check", "review", "deliver"):
        msg = load_pool_message("ceremony", phase_hint=step)
        assert isinstance(msg, str), f"Step {step} returned non-string"
        assert len(msg) > 0, f"Step {step} returned empty message"


@pytest.mark.unit
def test_compute_nudge_context_pool_on_build_failure() -> None:
    """compute_nudge uses context pool content on build failure."""
    from trw_mcp.state.ceremony_nudge import compute_nudge

    state = CeremonyState(session_started=True, phase="validate")
    context = NudgeContext(tool_name="build_check", build_passed=False)
    result = compute_nudge(state, available_learnings=0, context=context)
    assert isinstance(result, str)
    assert "TRW" in result  # Contains header


# ---------------------------------------------------------------------------
# CeremonyState pool fields round-trip
# ---------------------------------------------------------------------------


def test_ceremony_state_pool_fields_round_trip(tmp_path: Path) -> None:
    """Pool tracking fields survive JSON serialization round-trip."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()

    state = CeremonyState(
        session_started=True,
        pool_nudge_counts={"workflow": 5, "ceremony": 2},
        pool_ignore_counts={"learnings": 3},
        pool_cooldown_until={"workflow": 20},
        tool_call_counter=15,
        last_nudge_pool="workflow",
    )
    write_ceremony_state(trw_dir, state)

    loaded = read_ceremony_state(trw_dir)
    assert loaded.pool_nudge_counts == {"workflow": 5, "ceremony": 2}
    assert loaded.pool_ignore_counts == {"learnings": 3}
    assert loaded.pool_cooldown_until == {"workflow": 20}
    assert loaded.tool_call_counter == 15
    assert loaded.last_nudge_pool == "workflow"


def test_ceremony_state_pool_fields_default_on_missing(tmp_path: Path) -> None:
    """Pool fields default to empty when reading state without them."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    ctx_dir = trw_dir / "context"
    ctx_dir.mkdir()
    # Write state without pool fields (simulating old format)
    old_state = {"session_started": True, "phase": "implement"}
    (ctx_dir / "ceremony-state.json").write_text(json.dumps(old_state))

    loaded = read_ceremony_state(trw_dir)
    assert loaded.pool_nudge_counts == {}
    assert loaded.pool_ignore_counts == {}
    assert loaded.pool_cooldown_until == {}
    assert loaded.tool_call_counter == 0
    assert loaded.last_nudge_pool == ""


# ---------------------------------------------------------------------------
# State mutation helper tests
# ---------------------------------------------------------------------------


def test_increment_tool_call_counter(tmp_path: Path) -> None:
    """increment_tool_call_counter increments the counter."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()
    write_ceremony_state(trw_dir, CeremonyState())

    increment_tool_call_counter(trw_dir)
    state = read_ceremony_state(trw_dir)
    assert state.tool_call_counter == 1

    increment_tool_call_counter(trw_dir)
    state = read_ceremony_state(trw_dir)
    assert state.tool_call_counter == 2


def test_record_pool_nudge(tmp_path: Path) -> None:
    """record_pool_nudge increments count and sets last_nudge_pool."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()
    write_ceremony_state(trw_dir, CeremonyState())

    record_pool_nudge(trw_dir, "workflow")
    state = read_ceremony_state(trw_dir)
    assert state.pool_nudge_counts["workflow"] == 1
    assert state.last_nudge_pool == "workflow"


def test_record_pool_ignore(tmp_path: Path) -> None:
    """record_pool_ignore increments ignore count."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()
    write_ceremony_state(trw_dir, CeremonyState())

    record_pool_ignore(trw_dir, "ceremony")
    state = read_ceremony_state(trw_dir)
    assert state.pool_ignore_counts["ceremony"] == 1


# ---------------------------------------------------------------------------
# compute_nudge integration with pools
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compute_nudge_with_pools() -> None:
    """compute_nudge uses pool-based selection and returns non-empty content."""
    from trw_mcp.state.ceremony_nudge import compute_nudge

    state = CeremonyState(session_started=True, phase="implement")
    # Should return some content regardless of which pool is selected
    result = compute_nudge(state, available_learnings=3)
    assert isinstance(result, str)
    assert len(result) > 0
    assert "TRW" in result  # Should contain the header


@pytest.mark.unit
def test_compute_nudge_disabled_returns_empty() -> None:
    """compute_nudge returns empty string when nudges are disabled."""
    from trw_mcp.models.config._loader import get_config
    from trw_mcp.state.ceremony_nudge import compute_nudge

    config = get_config()
    # Patch effective_nudge_enabled to return False
    with patch.object(type(config), "effective_nudge_enabled", new_callable=lambda: property(lambda self: False)):
        state = CeremonyState()
        result = compute_nudge(state, available_learnings=5)
        assert result == ""


# ---------------------------------------------------------------------------
# Checkpoint threshold fix tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_checkpoint_threshold_raised() -> None:
    """files_modified <= 10 does not trigger checkpoint nudge (raised from 3)."""
    state = CeremonyState(
        session_started=True,
        checkpoint_count=1,
        files_modified_since_checkpoint=10,
        phase="implement",
    )
    pending = _highest_priority_pending_step(state)
    assert pending != "checkpoint"


@pytest.mark.unit
def test_checkpoint_threshold_triggers_above_10() -> None:
    """files_modified > 10 triggers checkpoint nudge."""
    state = CeremonyState(
        session_started=True,
        checkpoint_count=1,
        files_modified_since_checkpoint=11,
        phase="implement",
    )
    pending = _highest_priority_pending_step(state)
    assert pending == "checkpoint"


@pytest.mark.unit
def test_checkpoint_suppressed_in_validate_phase() -> None:
    """Checkpoint nudge is suppressed in validate phase even with many files modified."""
    state = CeremonyState(
        session_started=True,
        checkpoint_count=1,
        files_modified_since_checkpoint=20,
        phase="validate",
    )
    pending = _highest_priority_pending_step(state)
    # In validate phase, checkpoint is suppressed; build_check should be pending instead
    assert pending != "checkpoint"


@pytest.mark.unit
def test_checkpoint_suppressed_in_deliver_phase() -> None:
    """Checkpoint nudge is suppressed in deliver phase."""
    state = CeremonyState(
        session_started=True,
        checkpoint_count=1,
        files_modified_since_checkpoint=20,
        phase="deliver",
        build_check_result="passed",
        review_called=True,
    )
    pending = _highest_priority_pending_step(state)
    assert pending != "checkpoint"
    assert pending == "deliver"
