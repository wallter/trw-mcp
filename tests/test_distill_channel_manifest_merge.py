"""Shared client channel-manifest merge contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._distill_channel_manifest import merge_distill_channel_manifest
from trw_mcp.channels._manifest_loader import ManifestValidationError, auto_recreate_empty, load, write
from trw_mcp.channels._manifest_models import ChannelEntry


def _source(path: Path) -> Path:
    path.write_text(
        "channels:\n"
        "  - id: client-entry\n"
        "    client: test-client\n"
        "    surface: memory_file\n"
        "    telemetry_tag: test.client\n",
        encoding="utf-8",
    )
    return path


def test_merge_is_additive_and_idempotent(tmp_path: Path) -> None:
    target = tmp_path / ".trw/channels/manifest.yaml"
    auto_recreate_empty(target)
    manifest = load(target)
    manifest.channels.append(
        ChannelEntry(id="foreign", client="other", surface="memory_file", telemetry_tag="other.channel")
    )
    write(manifest, target)
    source = _source(tmp_path / "source.yaml")

    assert merge_distill_channel_manifest(tmp_path, source, "test") == (1, 2)
    assert merge_distill_channel_manifest(tmp_path, source, "test") == (0, 2)
    assert {entry.id for entry in load(target).channels} == {"foreign", "client-entry"}


def test_invalid_source_does_not_mutate_target(tmp_path: Path) -> None:
    target = tmp_path / ".trw/channels/manifest.yaml"
    auto_recreate_empty(target)
    before = target.read_bytes()
    source = tmp_path / "invalid.yaml"
    source.write_text("channels:\n  - id: missing-required-fields\n", encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="test-client manifest entry validation failed"):
        merge_distill_channel_manifest(tmp_path, source, "test-client")

    assert target.read_bytes() == before


def test_corrupt_target_is_recovered_before_merge(tmp_path: Path) -> None:
    target = tmp_path / ".trw/channels/manifest.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("{invalid", encoding="utf-8")

    assert merge_distill_channel_manifest(tmp_path, _source(tmp_path / "source.yaml"), "test") == (1, 1)
    assert [entry.id for entry in load(target).channels] == ["client-entry"]
