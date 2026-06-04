"""Tests for _manifest_loader.py — load/write/alias normalization/auto-recovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.channels._manifest_loader import (
    ChannelManifest,
    ManifestMissingError,
    ManifestValidationError,
    MarkerCollisionError,
    auto_recreate_empty,
    check_marker_collisions,
    load,
    write,
)
from trw_mcp.channels._manifest_models import ChannelEntry, MarkersConfig


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_MINIMAL_YAML = """\
format_version: "manifest/v1"
generated_by: trw-mcp
generated_at: ""
channels: []
"""

VALID_ONE_CHANNEL_YAML = """\
format_version: "manifest/v1"
generated_by: trw-mcp
generated_at: ""
channels:
  - id: cc-01
    client: claude-code
    surface: claude_md_segment
    telemetry_tag: cc_memory
"""


# ---------------------------------------------------------------------------
# load() — happy path
# ---------------------------------------------------------------------------


def test_load_valid_minimal(tmp_path: Path) -> None:
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, VALID_MINIMAL_YAML)
    manifest = load(p)
    assert isinstance(manifest, ChannelManifest)
    assert manifest.channels == []


def test_load_valid_one_channel(tmp_path: Path) -> None:
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, VALID_ONE_CHANNEL_YAML)
    manifest = load(p)
    assert len(manifest.channels) == 1
    assert manifest.channels[0].id == "cc-01"


# ---------------------------------------------------------------------------
# load() — error cases
# ---------------------------------------------------------------------------


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ManifestMissingError):
        load(tmp_path / "nonexistent.yaml")


def test_load_missing_format_version_raises(tmp_path: Path) -> None:
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, "channels: []\n")
    with pytest.raises(ManifestValidationError, match="format_version"):
        load(p)


def test_load_wrong_format_version_raises(tmp_path: Path) -> None:
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, 'format_version: "manifest/v0"\nchannels: []\n')
    with pytest.raises(ManifestValidationError, match="manifest/v1"):
        load(p)


def test_load_not_a_mapping_raises(tmp_path: Path) -> None:
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, "- item1\n- item2\n")
    with pytest.raises(ManifestValidationError):
        load(p)


def test_load_invalid_channel_field_raises(tmp_path: Path) -> None:
    """extra='forbid' on ChannelEntry should cause ManifestValidationError."""
    yaml_str = """\
format_version: "manifest/v1"
channels:
  - id: ch1
    client: codex
    surface: agents_md_segment
    telemetry_tag: t
    bogus_field: boom
"""
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, yaml_str)
    with pytest.raises(ManifestValidationError):
        load(p)


# ---------------------------------------------------------------------------
# Alias normalization (FR03)
# ---------------------------------------------------------------------------


def test_marker_begin_alias_normalized(tmp_path: Path) -> None:
    yaml_str = """\
format_version: "manifest/v1"
channels:
  - id: ch1
    client: codex
    surface: agents_md_segment
    telemetry_tag: t
    marker_begin: "<!-- begin -->"
    marker_end: "<!-- end -->"
"""
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, yaml_str)
    manifest = load(p)
    entry = manifest.channels[0]
    assert isinstance(entry.markers, MarkersConfig)
    assert entry.markers.start == "<!-- begin -->"
    assert entry.markers.end == "<!-- end -->"


def test_start_marker_alias_normalized(tmp_path: Path) -> None:
    yaml_str = """\
format_version: "manifest/v1"
channels:
  - id: ch1
    client: codex
    surface: agents_md_segment
    telemetry_tag: t
    start_marker: "<!-- s -->"
    end_marker: "<!-- e -->"
"""
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, yaml_str)
    manifest = load(p)
    entry = manifest.channels[0]
    assert entry.markers.start == "<!-- s -->"
    assert entry.markers.end == "<!-- e -->"


def test_lock_path_alias_normalized(tmp_path: Path) -> None:
    yaml_str = """\
format_version: "manifest/v1"
channels:
  - id: ch1
    client: codex
    surface: agents_md_segment
    telemetry_tag: t
    lock_path: ".trw/channels/ch.lock"
"""
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, yaml_str)
    manifest = load(p)
    assert manifest.channels[0].lock_file == ".trw/channels/ch.lock"


def test_lock_alias_normalized(tmp_path: Path) -> None:
    yaml_str = """\
format_version: "manifest/v1"
channels:
  - id: ch1
    client: codex
    surface: agents_md_segment
    telemetry_tag: t
    lock: ".trw/channels/ch.lock"
"""
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, yaml_str)
    manifest = load(p)
    assert manifest.channels[0].lock_file == ".trw/channels/ch.lock"


def test_default_tier_alias_normalized(tmp_path: Path) -> None:
    yaml_str = """\
format_version: "manifest/v1"
channels:
  - id: ch1
    client: codex
    surface: agents_md_segment
    telemetry_tag: t
    default_tier: "T3"
"""
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, yaml_str)
    manifest = load(p)
    assert manifest.channels[0].tier_default == "T3"


def test_content_types_alias_normalized(tmp_path: Path) -> None:
    yaml_str = """\
format_version: "manifest/v1"
channels:
  - id: ch1
    client: codex
    surface: agents_md_segment
    telemetry_tag: t
    content_types:
      - hotspot
      - edge_case
"""
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, yaml_str)
    manifest = load(p)
    assert manifest.channels[0].distill_record_types == ["hotspot", "edge_case"]


def test_stale_action_cleanup_trigger_alias_normalized(tmp_path: Path) -> None:
    yaml_str = """\
format_version: "manifest/v1"
channels:
  - id: ch1
    client: codex
    surface: agents_md_segment
    telemetry_tag: t
    stale_action: "TIER_DOWN"
    cleanup_trigger: "TTL_EXCEEDED"
"""
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, yaml_str)
    manifest = load(p)
    cleanup = manifest.channels[0].cleanup
    assert cleanup.trigger == "TTL_EXCEEDED"
    assert cleanup.action == "TIER_DOWN"


def test_tier_override_key_alias_normalized(tmp_path: Path) -> None:
    yaml_str = """\
format_version: "manifest/v1"
channels:
  - id: ch1
    client: codex
    surface: agents_md_segment
    telemetry_tag: t
    tier_override_key: "MY_TIER_KEY"
"""
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, yaml_str)
    manifest = load(p)
    assert manifest.channels[0].operator_tier_override_key == "MY_TIER_KEY"


# ---------------------------------------------------------------------------
# write() + round-trip
# ---------------------------------------------------------------------------


def test_write_creates_file(tmp_path: Path) -> None:
    manifest = ChannelManifest(format_version="manifest/v1")
    out = tmp_path / "out" / "manifest.yaml"
    write(manifest, out)
    assert out.exists()


def test_roundtrip_preserves_channel_count(tmp_path: Path) -> None:
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, VALID_ONE_CHANNEL_YAML)
    original = load(p)
    out = tmp_path / "out.yaml"
    write(original, out)
    reloaded = load(out)
    assert len(reloaded.channels) == len(original.channels)
    assert reloaded.channels[0].id == original.channels[0].id


def test_write_is_atomic_failed_replace_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash between the temp dump and the rename must leave the EXISTING
    manifest intact (not truncated/half-written) and leave no orphan temp file.
    The manifest is the channel registry — a partial write breaks every channel.
    """
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, VALID_ONE_CHANNEL_YAML)
    original_text = p.read_text(encoding="utf-8")

    def _boom(src: str, dst: str) -> None:
        raise OSError("simulated crash before rename")

    monkeypatch.setattr("os.replace", _boom)

    with pytest.raises(OSError):
        write(ChannelManifest(format_version="manifest/v1"), p)

    # Original manifest is byte-for-byte intact — never truncated in place.
    assert p.read_text(encoding="utf-8") == original_text
    # No orphan temp file left behind in the directory.
    leftovers = [f.name for f in p.parent.iterdir() if f.name.startswith(f".{p.name}.tmp.")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# auto_recreate_empty()
# ---------------------------------------------------------------------------


def test_auto_recreate_empty_creates_valid_manifest(tmp_path: Path) -> None:
    p = tmp_path / "new" / "manifest.yaml"
    auto_recreate_empty(p)
    assert p.exists()
    manifest = load(p)
    assert manifest.format_version == "manifest/v1"
    assert manifest.channels == []


def test_auto_recreate_empty_overwrites_existing(tmp_path: Path) -> None:
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, VALID_ONE_CHANNEL_YAML)
    auto_recreate_empty(p)
    manifest = load(p)
    assert manifest.channels == []


def test_auto_recreate_empty_emits_manifest_recovered_telemetry_event(tmp_path: Path) -> None:
    """BLOCKER-1 behavioral test: auto_recreate_empty must write a manifest_recovered
    event to the JSONL telemetry file (FR15-AC4).

    This verifies the actual JSONL file is written — not just that the event type
    exists in VALID_EVENT_TYPES.
    """
    manifest_path = tmp_path / ".trw" / "channels" / "manifest.yaml"
    telemetry_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"

    auto_recreate_empty(manifest_path, log_path=telemetry_path)

    # The manifest must be created
    assert manifest_path.exists()
    # The telemetry file must be written
    assert telemetry_path.exists(), "manifest_recovered telemetry event was never written"

    lines = [l for l in telemetry_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert lines, "telemetry file is empty — no events written"

    events = [json.loads(l) for l in lines]
    recovered_events = [e for e in events if e.get("event_type") == "manifest_recovered"]
    assert recovered_events, (
        "No manifest_recovered event found in telemetry JSONL. "
        "Events written: " + str([e.get("event_type") for e in events])
    )
    ev = recovered_events[0]
    assert ev["channel_id"] == "__system__"
    assert ev["client"] == "__system__"
    assert ev["outcome"] == "auto_recreated_empty"


# ---------------------------------------------------------------------------
# check_marker_collisions()
# ---------------------------------------------------------------------------


def test_check_marker_collisions_clean_file_no_error(tmp_path: Path) -> None:
    target = tmp_path / "AGENTS.md"
    target.write_text("# Hello world\nSome content\n", encoding="utf-8")
    entry = ChannelEntry(
        id="ch1",
        client="codex",
        surface="agents_md_segment",
        telemetry_tag="t",
        markers={"start": "<!-- trw:unique:start -->", "end": "<!-- trw:unique:end -->"},
    )
    # Should not raise
    check_marker_collisions(target, entry)


def test_check_marker_collisions_detects_collision(tmp_path: Path) -> None:
    # MED-7: ceremony markers (trw:start / trw:end) are now excluded from the
    # collision scope.  Use a distill-channel marker to test real collision
    # detection (<!-- trw:codex:start --> is a DISTILL_MARKER_KEYS marker).
    target = tmp_path / "CLAUDE.md"
    target.write_text(
        "# Hello\n<!-- trw:codex:start -->\nsome content\n<!-- trw:codex:end -->\n",
        encoding="utf-8",
    )
    entry = ChannelEntry(
        id="ch1",
        client="claude-code",  # different client from codex markers → foreign
        surface="instruction_file_segment",
        telemetry_tag="t",
    )
    with pytest.raises(MarkerCollisionError):
        check_marker_collisions(target, entry)


def test_check_marker_collisions_skips_aspirational(tmp_path: Path) -> None:
    # Use a distill-channel marker (would normally trigger a collision) to
    # confirm the aspirational skip takes priority over collision detection.
    target = tmp_path / "AGENTS.md"
    target.write_text("<!-- trw:codex:start -->\n<!-- trw:codex:end -->\n", encoding="utf-8")
    entry = ChannelEntry(
        id="ch1",
        client="claude-code",
        surface="agents_md_segment",
        telemetry_tag="t",
        status="aspirational",
    )
    # Aspirational channels skip collision check — should not raise
    check_marker_collisions(target, entry)


def test_check_marker_collisions_missing_file_no_error(tmp_path: Path) -> None:
    entry = ChannelEntry(
        id="ch1",
        client="codex",
        surface="agents_md_segment",
        telemetry_tag="t",
    )
    # Missing target file — no collision possible
    check_marker_collisions(tmp_path / "nonexistent.md", entry)


def test_check_marker_collisions_own_markers_not_a_collision(tmp_path: Path) -> None:
    """Re-install scenario: entry's OWN markers already in the file → not a collision.

    When a channel was previously installed, its own start/end markers will
    already be present in the target file.  Re-running check_marker_collisions
    must NOT flag those as a collision — only foreign markers from other channels
    are collisions.
    """
    own_start = "<!-- trw:distill:start -->"
    own_end = "<!-- trw:distill:end -->"
    target = tmp_path / "AGENTS.md"
    target.write_text(
        f"# Agents\n{own_start}\nSome distill content\n{own_end}\n",
        encoding="utf-8",
    )
    entry = ChannelEntry(
        id="ch-agents-distill",
        client="codex",
        surface="agents_md_segment",
        telemetry_tag="t",
        markers={"start": own_start, "end": own_end},
    )
    # Should not raise — finding the channel's own markers is expected on re-install
    check_marker_collisions(target, entry)


def test_check_marker_collisions_ceremony_markers_not_a_collision(tmp_path: Path) -> None:
    """MED-7 behavioral test: ceremony markers in CLAUDE.md must NOT trigger MarkerCollisionError.

    CLAUDE.md / AGENTS.md files always contain <!-- trw:start --> and <!-- trw:end -->
    as part of the standard TRW bootstrap.  Treating them as distill-channel
    collisions would produce a false-positive on every standard deployment.
    """
    target = tmp_path / "CLAUDE.md"
    # Standard TRW-bootstrapped CLAUDE.md contains ceremony markers
    target.write_text(
        "# Claude guidance\n<!-- trw:start -->\nCeremony section\n<!-- trw:end -->\n"
        "## Other content\n",
        encoding="utf-8",
    )
    entry = ChannelEntry(
        id="cc-01",
        client="claude-code",
        surface="instruction_file_segment",
        telemetry_tag="t",
        markers={"start": "<!-- trw:distill:start -->", "end": "<!-- trw:distill:end -->"},
    )
    # Must NOT raise — ceremony markers are excluded from the distill collision scope
    check_marker_collisions(target, entry)
