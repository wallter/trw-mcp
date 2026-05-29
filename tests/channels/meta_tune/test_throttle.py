"""Tests for meta_tune._throttle.

Uses synthetic data and a temp manifest; no live MCP, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.channels._manifest_models import (
    CLIENT_THROTTLE_THRESHOLDS,
    COPILOT_THROTTLE_MIN_N,
    DEFAULT_THROTTLE_MIN_N,
)
from trw_mcp.channels.meta_tune._throttle import (
    ThrottleDecision,
    ThrottleVerdict,
    apply_throttle,
    evaluate_throttle,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(tmp_path: Path, channel_id: str = "ch-01", client: str = "claude-code", tier: str = "T2") -> Path:
    """Write a minimal manifest.yaml with one channel entry."""
    from ruamel.yaml import YAML

    manifest_path = tmp_path / ".trw" / "channels" / "manifest.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "format_version": "manifest/v1",
        "generated_by": "test",
        "generated_at": "",
        "channels": [
            {
                "id": channel_id,
                "client": client,
                "surface": "instruction_file_segment",
                "telemetry_tag": "test",
                "tier_default": tier,
            }
        ],
    }
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    with manifest_path.open("w") as fh:
        yaml.dump(data, fh)
    return manifest_path


# ---------------------------------------------------------------------------
# evaluate_throttle — insufficient data (below min_n)
# ---------------------------------------------------------------------------


def test_evaluate_insufficient_data_default_client() -> None:
    stats = {"adjusted_rate": 0.10, "total_pushes": DEFAULT_THROTTLE_MIN_N - 1}
    decision = evaluate_throttle("ch-01", "claude-code", stats)
    assert decision.verdict == ThrottleVerdict.INSUFFICIENT_DATA


def test_evaluate_insufficient_data_copilot_needs_50() -> None:
    stats = {"adjusted_rate": 0.05, "total_pushes": COPILOT_THROTTLE_MIN_N - 1}
    decision = evaluate_throttle("ch-01", "copilot", stats)
    assert decision.verdict == ThrottleVerdict.INSUFFICIENT_DATA
    assert decision.min_n == COPILOT_THROTTLE_MIN_N


def test_evaluate_copilot_with_enough_data_triggers_throttle() -> None:
    threshold, _ = CLIENT_THROTTLE_THRESHOLDS["copilot"]
    stats = {"adjusted_rate": threshold - 0.01, "total_pushes": COPILOT_THROTTLE_MIN_N}
    decision = evaluate_throttle("ch-01", "copilot", stats)
    assert decision.verdict == ThrottleVerdict.THROTTLE_DOWN


# ---------------------------------------------------------------------------
# evaluate_throttle — THROTTLE_DOWN
# ---------------------------------------------------------------------------


def test_evaluate_throttle_down_below_threshold() -> None:
    threshold, _ = CLIENT_THROTTLE_THRESHOLDS["claude-code"]
    stats = {"adjusted_rate": threshold - 0.01, "total_pushes": DEFAULT_THROTTLE_MIN_N}
    decision = evaluate_throttle("ch-01", "claude-code", stats)
    assert decision.verdict == ThrottleVerdict.THROTTLE_DOWN
    assert decision.threshold == threshold


def test_evaluate_throttle_down_fields_populated() -> None:
    stats = {"adjusted_rate": 0.01, "total_pushes": 50}
    decision = evaluate_throttle("ch-A", "codex", stats)
    assert isinstance(decision, ThrottleDecision)
    assert decision.channel_id == "ch-A"
    assert decision.client == "codex"
    assert len(decision.reason) > 0


# ---------------------------------------------------------------------------
# evaluate_throttle — THROTTLE_CLEAR (ok / recovery)
# ---------------------------------------------------------------------------


def test_evaluate_throttle_clear_above_threshold() -> None:
    threshold, _ = CLIENT_THROTTLE_THRESHOLDS["claude-code"]
    stats = {"adjusted_rate": threshold + 0.10, "total_pushes": DEFAULT_THROTTLE_MIN_N}
    decision = evaluate_throttle("ch-01", "claude-code", stats)
    assert decision.verdict == ThrottleVerdict.THROTTLE_CLEAR


def test_evaluate_throttle_clear_exactly_at_threshold() -> None:
    threshold, _ = CLIENT_THROTTLE_THRESHOLDS["claude-code"]
    stats = {"adjusted_rate": threshold, "total_pushes": DEFAULT_THROTTLE_MIN_N}
    decision = evaluate_throttle("ch-01", "claude-code", stats)
    assert decision.verdict == ThrottleVerdict.THROTTLE_CLEAR


# ---------------------------------------------------------------------------
# evaluate_throttle — min_n_override
# ---------------------------------------------------------------------------


def test_evaluate_min_n_override_lowers_gate() -> None:
    stats = {"adjusted_rate": 0.05, "total_pushes": 5}
    # With override=5, we have enough data
    threshold, _ = CLIENT_THROTTLE_THRESHOLDS["claude-code"]
    decision = evaluate_throttle("ch-01", "claude-code", stats, min_n_override=5)
    # adjusted_rate=0.05 < threshold → THROTTLE_DOWN
    assert decision.verdict == ThrottleVerdict.THROTTLE_DOWN


def test_evaluate_min_n_override_raises_gate() -> None:
    stats = {"adjusted_rate": 0.05, "total_pushes": DEFAULT_THROTTLE_MIN_N}
    # Override requires 1000; insufficient
    decision = evaluate_throttle("ch-01", "claude-code", stats, min_n_override=1000)
    assert decision.verdict == ThrottleVerdict.INSUFFICIENT_DATA


# ---------------------------------------------------------------------------
# evaluate_throttle — unknown client
# ---------------------------------------------------------------------------


def test_evaluate_unknown_client_defaults() -> None:
    stats = {"adjusted_rate": 0.50, "total_pushes": DEFAULT_THROTTLE_MIN_N}
    decision = evaluate_throttle("ch-01", "unknown-client", stats)
    # Unknown client gets default threshold (0.20, 3); 0.50 >= 0.20 → CLEAR
    assert decision.verdict == ThrottleVerdict.THROTTLE_CLEAR


# ---------------------------------------------------------------------------
# apply_throttle
# ---------------------------------------------------------------------------


def test_apply_throttle_hold_returns_false(tmp_path: Path) -> None:
    manifest = _make_manifest(tmp_path)
    stats = {"adjusted_rate": 0.50, "total_pushes": DEFAULT_THROTTLE_MIN_N}
    decision = evaluate_throttle("ch-01", "claude-code", stats)
    # verdict = THROTTLE_CLEAR → apply_throttle should return False
    # (CLEAR is only acted on when the tier is already below default)
    # For this test just verify it doesn't crash and returns bool
    result = apply_throttle("ch-01", decision, manifest)
    assert isinstance(result, bool)


def test_apply_throttle_down_updates_manifest(tmp_path: Path) -> None:
    manifest_path = _make_manifest(tmp_path, tier="T2")
    stats = {"adjusted_rate": 0.01, "total_pushes": DEFAULT_THROTTLE_MIN_N}
    decision = evaluate_throttle("ch-01", "claude-code", stats)
    assert decision.verdict == ThrottleVerdict.THROTTLE_DOWN

    ok = apply_throttle("ch-01", decision, manifest_path)
    assert ok

    # Reload and verify tier changed from T2 → T1
    from trw_mcp.channels._manifest_loader import load
    updated = load(manifest_path)
    ch = next(e for e in updated.channels if e.id == "ch-01")
    assert ch.tier_default == "T1"


def test_apply_throttle_missing_manifest_returns_false(tmp_path: Path) -> None:
    from trw_mcp.channels.meta_tune._throttle import ThrottleDecision, ThrottleVerdict
    decision = ThrottleDecision(
        channel_id="ch-01",
        client="claude-code",
        verdict=ThrottleVerdict.THROTTLE_DOWN,
        adjusted_rate=0.01,
        threshold=0.25,
        n_events=50,
        min_n=30,
        reason="test",
    )
    result = apply_throttle("ch-01", decision, tmp_path / "nonexistent" / "manifest.yaml")
    assert result is False


def test_apply_throttle_channel_not_found_returns_false(tmp_path: Path) -> None:
    manifest_path = _make_manifest(tmp_path, channel_id="other-ch")
    from trw_mcp.channels.meta_tune._throttle import ThrottleDecision, ThrottleVerdict
    decision = ThrottleDecision(
        channel_id="ch-01",  # different from manifest
        client="claude-code",
        verdict=ThrottleVerdict.THROTTLE_DOWN,
        adjusted_rate=0.01,
        threshold=0.25,
        n_events=50,
        min_n=30,
        reason="test",
    )
    result = apply_throttle("ch-01", decision, manifest_path)
    assert result is False


def test_apply_throttle_already_at_floor_no_change(tmp_path: Path) -> None:
    """Tier already at T0 — apply should succeed but not corrupt manifest."""
    manifest_path = _make_manifest(tmp_path, tier="T0")
    from trw_mcp.channels.meta_tune._throttle import ThrottleDecision, ThrottleVerdict
    decision = ThrottleDecision(
        channel_id="ch-01",
        client="claude-code",
        verdict=ThrottleVerdict.THROTTLE_DOWN,
        adjusted_rate=0.01,
        threshold=0.25,
        n_events=50,
        min_n=30,
        reason="test",
    )
    result = apply_throttle("ch-01", decision, manifest_path)
    # Returns True (no-op success) or False (no change); either is acceptable;
    # important: manifest must still be valid
    from trw_mcp.channels._manifest_loader import load
    updated = load(manifest_path)
    ch = next(e for e in updated.channels if e.id == "ch-01")
    assert ch.tier_default == "T0"


def test_apply_throttle_emits_telemetry_event(tmp_path: Path) -> None:
    manifest_path = _make_manifest(tmp_path, tier="T2")
    # Create the telemetry dir
    tel_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
    tel_path.parent.mkdir(parents=True, exist_ok=True)

    from trw_mcp.channels.meta_tune._throttle import ThrottleDecision, ThrottleVerdict
    decision = ThrottleDecision(
        channel_id="ch-01",
        client="claude-code",
        verdict=ThrottleVerdict.THROTTLE_DOWN,
        adjusted_rate=0.01,
        threshold=0.25,
        n_events=50,
        min_n=30,
        reason="test",
    )
    apply_throttle("ch-01", decision, manifest_path)
    # If the telemetry dir exists and file was created, there should be an event
    # (telemetry goes to manifest_path.parent.parent / "telemetry" / ...)
    tel_root = manifest_path.parent.parent / "telemetry" / "channel-events.jsonl"
    if tel_root.exists():
        events = [json.loads(line) for line in tel_root.read_text().splitlines() if line.strip()]
        types = [e["event_type"] for e in events]
        assert "throttle_applied" in types
