"""Shared client channel-manifest merge contract."""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog

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


@pytest.mark.parametrize(
    "content",
    [
        "{invalid",
        # An upgrade from a manifest written before RC-014: a custom entry plus the retired tier keys.
        "format_version: manifest/v1\n"
        "channels:\n"
        "  - id: my-custom-channel\n"
        "    client: codex\n"
        "    surface: agents_md_segment\n"
        "    telemetry_tag: my.custom\n"
        "    tier_default: T2\n"
        "    tier_min: T0\n",
    ],
    ids=["unparseable", "pre-rc014-manifest"],
)
def test_an_invalid_target_fails_loudly_and_is_never_replaced(tmp_path: Path, content: str) -> None:
    """RC-014 review P0: recovery used to overwrite an invalid manifest with an empty one, deleting custom
    entries on the documented update path. Now the merge raises and the file is byte-identical."""
    target = tmp_path / ".trw/channels/manifest.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    before = target.read_bytes()

    with pytest.raises(ManifestValidationError, match="was left unchanged") as err:
        merge_distill_channel_manifest(tmp_path, _source(tmp_path / "source.yaml"), "test")

    assert target.read_bytes() == before
    # The operator is told which keys and what to do (lead ruling: fail loudly, no migration code).
    assert "tier_default and tier_min" in str(err.value) and "trw-mcp update-project" in str(err.value)


def test_missing_target_is_created_without_warning_or_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First init-project (no manifest.yaml on disk yet) is a normal create.

    Regression: the caller used to catch a bare ``Exception`` around
    ``load()``, so ``ManifestMissingError`` (normal — nothing exists yet) was
    treated identically to ``ManifestValidationError`` (real corruption) and
    always logged ``manifest_auto_recreated`` at WARNING plus a
    ``manifest_recovered`` telemetry event, even on a project's very first
    install.
    """
    monkeypatch.setenv("TRW_REPO_ROOT", str(tmp_path))
    # No .trw/channels/ directory at all — the literal first-init shape.

    with structlog.testing.capture_logs() as logs:
        added, total = merge_distill_channel_manifest(tmp_path, _source(tmp_path / "source.yaml"), "test")

    assert (added, total) == (1, 1)
    assert not any(entry.get("event") == "manifest_auto_recreated" for entry in logs)
    assert any(entry.get("event") == "manifest_created" and entry.get("log_level") == "info" for entry in logs)
    telemetry_log = tmp_path / ".trw/telemetry/channel-events.jsonl"
    assert not telemetry_log.exists()
