"""Tests for _manifest_loader.py — load/write/alias normalization/auto-recovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.channels._manifest_loader import (
    ChannelManifest,
    ManifestMissingError,
    ManifestValidationError,
    auto_recreate_empty,
    load,
    write,
)
from trw_mcp.channels._manifest_models import MarkersConfig


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


@pytest.mark.parametrize(
    "content",
    [
        "format_version: manifest/v1\nchannels: [\n",  # unterminated flow sequence
        "format_version: manifest/v1\n\tchannels: []\n",  # tab indentation
        'a: "unterminated\n',  # unterminated quoted scalar
        "key: *undefined_anchor\n",  # undefined alias
    ],
)
def test_load_malformed_yaml_raises_validation_error(tmp_path: Path, content: str) -> None:
    """Unparseable YAML must surface as ManifestValidationError, not a raw YAMLError.

    Callers document a never-raises contract and only catch the two Manifest*
    errors, so a bare ruamel YAMLError would escape to the operator as a
    traceback (PRD-CORE-231 FR05 review finding F3).
    """
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, content)

    with pytest.raises(ManifestValidationError, match="not valid YAML"):
        load(p)


def test_malformed_manifest_raises_a_typed_error_not_a_bare_yaml_crash(tmp_path: Path) -> None:
    """The loader's own contract, kept after its drift-gate caller was retired.

    This asserted the same corrupt-YAML input through
    `_instruction_drift.check_instruction_drift`, whose FR05 never-crashes
    contract it was proving. PRD-CORE-239 FR01 removed that gate: evaluating
    its `_is_checkable()` predicate across all six bundled manifests matched
    exactly two entries, both channels the same PRD deletes, so it would have
    reported "0 checked" forever afterwards.

    The loader behaviour underneath is still worth pinning — every surviving
    caller (`bootstrap/_distill_channel_manifest.py`, `cli/channel_doctor.py`)
    depends on corrupt YAML surfacing as `ManifestValidationError` rather than
    a raw parser exception, so this now tests that directly instead of through
    a deleted consumer.
    """
    manifest = tmp_path / ".trw" / "channels" / "manifest.yaml"
    _write_yaml(manifest, "format_version: manifest/v1\nchannels: [\n")

    with pytest.raises(ManifestValidationError):
        load(manifest)


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


def test_tier_default_key_raises_no_compat_shim(tmp_path: Path) -> None:
    """``tier_default``/``tier_min`` were removed with no normalization (RC-014).

    Unlike ``default_tier`` -> ``tier_default`` before it, these two keys are
    NOT dropped or renamed on load: the standing "no compat shims" policy
    means an old manifest that still carries them must fail loudly via
    ChannelEntry's extra="forbid" rather than silently lose the field. The
    merge never replaces such a file (test_distill_channel_manifest_merge.py);
    the operator removes the two keys or deletes the file to regenerate it.
    """
    yaml_str = """\
format_version: "manifest/v1"
channels:
  - id: ch1
    client: codex
    surface: agents_md_segment
    telemetry_tag: t
    tier_default: "T2"
"""
    p = tmp_path / "manifest.yaml"
    _write_yaml(p, yaml_str)
    with pytest.raises(ManifestValidationError):
        load(p)


def test_content_types_is_dropped_not_renamed(tmp_path: Path) -> None:
    """A retired key must still LOAD, and must no longer be renamed.

    Same shape as the tier-override case: `distill_record_types` and its
    `content_types` / `record_types` aliases were set per channel with
    purpose-built values and read by nothing, so PRD-CORE-239 removed the field.
    An old manifest that still carries the legacy key must keep loading.
    """
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
    assert manifest.channels[0].id == "ch1"
    assert not hasattr(manifest.channels[0], "distill_record_types")


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


def test_tier_override_key_is_dropped_not_renamed(tmp_path: Path) -> None:
    """A retired key must still LOAD, and must no longer be renamed.

    This asserted the legacy `tier_override_key` was renamed onto
    `operator_tier_override_key`. PRD-CORE-239 removed that field from
    `ChannelEntry` — it was authored per channel with real variation, documented
    in CHANNEL-ARCHITECTURE.md as an operator-facing override, and read by no
    consumer — so the rename now targets nothing and, under `extra="forbid"`,
    would raise on a manifest a previous version wrote.

    The property worth keeping is backward compatibility: the key is dropped and
    the entry still loads.
    """
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
    assert manifest.channels[0].id == "ch1"
    assert not hasattr(manifest.channels[0], "operator_tier_override_key")


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


def test_write_is_atomic_failed_replace_preserves_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert recovered_events, "No manifest_recovered event found in telemetry JSONL. Events written: " + str(
        [e.get("event_type") for e in events]
    )
    ev = recovered_events[0]
    assert ev["channel_id"] == "__system__"
    assert ev["client"] == "__system__"
    assert ev["outcome"] == "auto_recreated_empty"
