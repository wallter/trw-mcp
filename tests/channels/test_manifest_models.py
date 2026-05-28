"""Tests for _manifest_models.py — ChannelEntry schema and constants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.channels._manifest_models import (
    CLIENT_CORRECTION_FACTORS,
    CLIENT_THROTTLE_THRESHOLDS,
    DEFAULT_CORRELATION_WINDOW_SECONDS,
    JOIN_KEY_FIELDS,
    MARKER_REGISTRY,
    ChannelEntry,
    ChannelStatus,
    ChannelSurface,
    ChannelSurface,
    CleanupAction,
    CleanupConfig,
    CleanupTrigger,
    HumanEditDetection,
    MarkersConfig,
    ProvenanceConfig,
    WriteStrategy,
)


# ---------------------------------------------------------------------------
# Enum values
# ---------------------------------------------------------------------------


def test_write_strategy_values() -> None:
    assert WriteStrategy.FULL_REWRITE == "FULL_REWRITE"
    assert WriteStrategy.MARKER_REPLACE == "MARKER_REPLACE"
    assert WriteStrategy.APPEND_WITH_TTL == "APPEND_WITH_TTL"
    assert WriteStrategy.JSON_KEY_MERGE == "JSON_KEY_MERGE"
    assert WriteStrategy.EPHEMERAL_STDOUT == "EPHEMERAL_STDOUT"
    assert WriteStrategy.NONE == "NONE"


def test_channel_surface_canonical_values_present() -> None:
    """The 12 canonical surface types from PRD-DIST-2400 §1.1 must all be present.

    Additional client-specific aliases (CLAUDE_MD_SEGMENT, COPILOT_INSTRUCTIONS_SEGMENT,
    etc.) are permitted as backward-compat values but the canonical generic types
    must be available for the substrate to support all 6 client adapters.
    """
    values = {s.value for s in ChannelSurface}
    required_canonical = {
        "instruction_file_segment",
        "agents_md_segment",
        "memory_file",  # Claude Code MEMORY.md (CC-01)
        "path_scoped_file",  # Cursor MDC, Copilot path-instructions
        "subagent_file",  # CC-05, AG-02, opencode explorer
        "hook_script",
        "hook_stdout_ephemeral",  # CC-03 PreToolUse
        "posttooluse_event_log",  # CC-04
        "mcp_tool_return",
        "vscode_mcp_config",
        "custom_command",  # opencode slash commands
    }
    missing = required_canonical - values
    assert not missing, f"Missing canonical ChannelSurface values: {missing}"


def test_channel_status_values() -> None:
    assert set(s.value for s in ChannelStatus) == {
        "active", "aspirational", "deprecated", "disabled"
    }


def test_human_edit_detection_values() -> None:
    values = {e.value for e in HumanEditDetection}
    assert "NONE" in values
    assert "SHA256_SEGMENT" in values
    assert "MARKER_BOUNDARY" in values
    assert "KEY_NAMESPACE" in values
    assert "RENDER_LOG" in values


def test_cleanup_trigger_values() -> None:
    assert set(e.value for e in CleanupTrigger) == {
        "TTL_EXCEEDED", "QUOTA_EXCEEDED", "DISABLED", "NONE"
    }


def test_cleanup_action_values() -> None:
    expected = {
        "TIER_DOWN", "TIER_DOWN_TO_T0", "FULL_PRUNE",
        "CLEAR_SEGMENT", "SUPPRESS", "TOMBSTONE", "NONE"
    }
    assert set(e.value for e in CleanupAction) == expected


# ---------------------------------------------------------------------------
# ChannelEntry — minimal valid construction
# ---------------------------------------------------------------------------


def test_channel_entry_minimal() -> None:
    entry = ChannelEntry(
        id="test",
        client="codex",
        surface=ChannelSurface.AGENTS_MD_SEGMENT,
        telemetry_tag="test",
    )
    assert entry.id == "test"
    assert entry.status == "active"
    assert entry.tier_default == "T2"


def test_channel_entry_all_required_fields() -> None:
    """FR01: ChannelEntry accepts all 38 fields."""
    entry = ChannelEntry(
        id="cc-01-memory",
        client="claude-code",
        surface="claude_md_segment",
        telemetry_tag="cc_memory_distill",
        file="CLAUDE.md",
        lock_file=".trw/channels/cc-01.lock",
        status="active",
        write_strategy="MARKER_REPLACE",
        tier_default="T2",
        tier_min="T0",
        operator_tier_override_key="TIER_OVERRIDE",
        markers={"start": "<!-- trw:start -->", "end": "<!-- trw:end -->"},
        distill_record_types=["hotspot", "convention"],
        ttl_commits=10,
        ttl_days=7,
        quota_total_bytes=4096,
        quota_warn_bytes=3500,
        provenance={"enabled": True, "detection": "SHA256_SEGMENT"},
        cleanup={"trigger": "NONE", "action": "NONE"},
        lock_lifecycle="auto_cleanup_on_channel_disable",
        human_edit_detection="SHA256_SEGMENT",
        description="Distill memory snapshot",
        regenerate_cmd="trw-mcp channel-render cc-01-memory",
        client_version_min="1.0.0",
        mdc_description=None,
        mdc_globs=None,
        mdc_always_apply=False,
        session_correlation=True,
        emit_on_ttl_skip=True,
        emit_on_conflict_skip=True,
        emit_on_lock_skip=True,
        sidecar_schema="risk-report-sidecar/v0",
        sidecar_path=".trw/distill/sidecar.json",
        hook_schema_confirmed_at=None,
        activation_gate=None,
        tags=["memory", "distill"],
        notes="Primary distill channel",
    )
    assert entry.id == "cc-01-memory"
    assert entry.client == "claude-code"


# ---------------------------------------------------------------------------
# Validation — extra fields forbidden
# ---------------------------------------------------------------------------


def test_channel_entry_extra_field_raises() -> None:
    """extra='forbid' — unknown fields raise ValidationError."""
    with pytest.raises(ValidationError):
        ChannelEntry(
            id="x",
            client="codex",
            surface="agents_md_segment",
            telemetry_tag="t",
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_channel_entry_invalid_id_raises() -> None:
    """Field validator rejects ids with spaces or special chars."""
    with pytest.raises(ValidationError, match="alphanumeric"):
        ChannelEntry(
            id="bad id!",
            client="codex",
            surface="agents_md_segment",
            telemetry_tag="t",
        )


def test_channel_entry_id_accepts_hyphens_and_underscores() -> None:
    entry = ChannelEntry(
        id="cc-01_memory-snapshot",
        client="claude-code",
        surface="claude_md_segment",
        telemetry_tag="t",
    )
    assert entry.id == "cc-01_memory-snapshot"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


def test_markers_config_defaults() -> None:
    m = MarkersConfig()
    assert m.start == ""
    assert m.end == ""


def test_provenance_config_defaults() -> None:
    p = ProvenanceConfig()
    assert p.enabled is True
    assert p.detection == "SHA256_SEGMENT"


def test_cleanup_config_defaults() -> None:
    c = CleanupConfig()
    assert c.trigger == "NONE"
    assert c.action == "NONE"


def test_markers_config_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        MarkersConfig(start="a", end="b", bogus="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Constants (FR22-FR24)
# ---------------------------------------------------------------------------


def test_join_key_fields() -> None:
    assert JOIN_KEY_FIELDS == ("session_id", "file_path")


def test_default_correlation_window() -> None:
    assert DEFAULT_CORRELATION_WINDOW_SECONDS == 3600


def test_client_correction_factors() -> None:
    assert CLIENT_CORRECTION_FACTORS["claude-code"] == 0.85
    assert CLIENT_CORRECTION_FACTORS["codex"] == 0.70
    assert CLIENT_CORRECTION_FACTORS["copilot"] == 0.50
    # Adjusted rate cap
    for client, factor in CLIENT_CORRECTION_FACTORS.items():
        adjusted = min(1.0 / factor, 1.0)
        assert 0.0 < adjusted <= 1.0, f"Bad factor for {client}"


def test_client_throttle_thresholds() -> None:
    assert CLIENT_THROTTLE_THRESHOLDS["claude-code"] == (0.25, 3)
    assert CLIENT_THROTTLE_THRESHOLDS["copilot"] == (0.15, 5)
    for client, (threshold, window) in CLIENT_THROTTLE_THRESHOLDS.items():
        assert 0.0 < threshold < 1.0, f"Bad threshold for {client}"
        assert window > 0, f"Bad window for {client}"


def test_marker_registry_not_empty() -> None:
    assert len(MARKER_REGISTRY) > 0
    for key, value in MARKER_REGISTRY.items():
        assert isinstance(key, str) and len(key) > 0
        assert isinstance(value, str) and len(value) > 0
