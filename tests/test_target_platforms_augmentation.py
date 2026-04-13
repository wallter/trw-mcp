"""Tests for _update_config_target_platforms augmentation contract.

PRD-FIX-076 / Sprint 91 follow-up: the prior implementation REPLACED
target_platforms with the override-supplied list, destroying multi-platform
configurations whenever ``--ide <single>`` was passed to update-project.
The fixed implementation augments rather than narrows.

Contract verified here:
  - Existing entries are preserved (never narrowed).
  - New ide_targets entries are appended in order.
  - Legacy bare ``cursor`` is silently migrated to ``cursor-ide``.
  - Duplicates are deduplicated (first occurrence wins).
  - When merge is no-op, file is preserved.
  - All other config fields preserved.
  - YAML/IO failures → fail-open warning, no crash.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import yaml


def _seed_config(target_dir: Path, target_platforms: list[str], **extra: object) -> Path:
    """Write .trw/config.yaml with the given platforms + arbitrary extra fields."""
    cfg = target_dir / ".trw" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"target_platforms": list(target_platforms), **extra}
    cfg.write_text(yaml.safe_dump(payload, default_flow_style=False, sort_keys=False))
    return cfg


def _read_platforms(cfg_path: Path) -> list[str]:
    data = yaml.safe_load(cfg_path.read_text())
    return cast(list[str], data["target_platforms"])


@pytest.mark.integration
class TestAugmentationPreservesExistingPlatforms:
    """The user's existing list must NEVER be narrowed."""

    def test_single_ide_override_does_not_narrow_multi_platform_list(
        self, tmp_path: Path
    ) -> None:
        """Pre-fix bug: --ide cursor-ide narrowed list to [cursor-ide]."""
        from trw_mcp.bootstrap._ide_targets import _update_config_target_platforms

        cfg = _seed_config(
            tmp_path,
            target_platforms=[
                "claude-code", "cursor-ide", "opencode", "codex", "copilot", "gemini",
            ],
        )
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}

        # Simulate --ide cursor-ide: ide_targets resolves to just ["cursor-ide"]
        _update_config_target_platforms(tmp_path, ["cursor-ide"], result)

        platforms = _read_platforms(cfg)
        # All originals preserved
        assert "claude-code" in platforms
        assert "cursor-ide" in platforms
        assert "opencode" in platforms
        assert "codex" in platforms
        assert "copilot" in platforms
        assert "gemini" in platforms
        # File should be reported as preserved (no diff — cursor-ide already present)
        assert str(cfg) in result["preserved"]
        assert str(cfg) not in result["updated"]

    def test_existing_entries_in_original_order(self, tmp_path: Path) -> None:
        """Augmentation preserves the user's preferred ordering."""
        from trw_mcp.bootstrap._ide_targets import _update_config_target_platforms

        original = ["gemini", "claude-code", "opencode"]
        cfg = _seed_config(tmp_path, target_platforms=original)
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}

        _update_config_target_platforms(tmp_path, ["cursor-cli"], result)

        platforms = _read_platforms(cfg)
        # Original ordering preserved + new entry appended at end
        assert platforms == ["gemini", "claude-code", "opencode", "cursor-cli"]


@pytest.mark.integration
class TestAugmentationAddsNewIdes:
    """New entries from ide_targets append in order; never replace existing."""

    def test_new_ide_appended_when_not_already_present(self, tmp_path: Path) -> None:
        """User has [claude-code]; --ide cursor-cli adds it without removing claude-code."""
        from trw_mcp.bootstrap._ide_targets import _update_config_target_platforms

        cfg = _seed_config(tmp_path, target_platforms=["claude-code"])
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}

        _update_config_target_platforms(tmp_path, ["cursor-cli"], result)

        platforms = _read_platforms(cfg)
        assert platforms == ["claude-code", "cursor-cli"]
        assert str(cfg) in result["updated"]

    def test_multiple_new_ides_appended_in_order(self, tmp_path: Path) -> None:
        """ide_targets order is honored when appending multiple new IDEs."""
        from trw_mcp.bootstrap._ide_targets import _update_config_target_platforms

        cfg = _seed_config(tmp_path, target_platforms=["claude-code"])
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}

        _update_config_target_platforms(
            tmp_path, ["cursor-ide", "cursor-cli", "gemini"], result
        )

        platforms = _read_platforms(cfg)
        assert platforms == ["claude-code", "cursor-ide", "cursor-cli", "gemini"]


@pytest.mark.integration
class TestLegacyCursorMigration:
    """Bare `cursor` identifier is silently migrated to `cursor-ide`."""

    def test_cursor_migrated_to_cursor_ide(self, tmp_path: Path) -> None:
        """Legacy entry rewritten in-place; file marked as updated."""
        from trw_mcp.bootstrap._ide_targets import _update_config_target_platforms

        cfg = _seed_config(tmp_path, target_platforms=["claude-code", "cursor"])
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}

        # No new ide_targets — pure migration of existing list
        _update_config_target_platforms(tmp_path, ["claude-code"], result)

        platforms = _read_platforms(cfg)
        assert "cursor" not in platforms, "Legacy `cursor` should be removed"
        assert "cursor-ide" in platforms, "Should be replaced with cursor-ide"
        assert "claude-code" in platforms
        assert str(cfg) in result["updated"]

    def test_cursor_migration_dedupes_when_cursor_ide_already_present(
        self, tmp_path: Path
    ) -> None:
        """If both `cursor` and `cursor-ide` exist, migration deduplicates."""
        from trw_mcp.bootstrap._ide_targets import _update_config_target_platforms

        cfg = _seed_config(
            tmp_path, target_platforms=["claude-code", "cursor", "cursor-ide"]
        )
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}

        _update_config_target_platforms(tmp_path, [], result)

        platforms = _read_platforms(cfg)
        # Only one cursor-ide should remain
        assert platforms.count("cursor-ide") == 1
        assert "cursor" not in platforms
        assert platforms == ["claude-code", "cursor-ide"]


@pytest.mark.integration
class TestPreservesOtherConfigFields:
    """All non-target_platforms config fields must survive the rewrite."""

    def test_other_fields_preserved(self, tmp_path: Path) -> None:
        """Augmentation rewrites only target_platforms; everything else stays."""
        from trw_mcp.bootstrap._ide_targets import _update_config_target_platforms

        cfg = _seed_config(
            tmp_path,
            target_platforms=["claude-code"],
            mcp_transport="streamable-http",
            mcp_host="127.0.0.1",
            mcp_port=8100,
            installation_id="test-dev",
            embeddings_enabled=True,
            platform_api_key="trw_test_secret",
        )
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}

        _update_config_target_platforms(tmp_path, ["cursor-cli"], result)

        data = yaml.safe_load(cfg.read_text())
        assert data["target_platforms"] == ["claude-code", "cursor-cli"]
        assert data["mcp_transport"] == "streamable-http"
        assert data["mcp_host"] == "127.0.0.1"
        assert data["mcp_port"] == 8100
        assert data["installation_id"] == "test-dev"
        assert data["embeddings_enabled"] is True
        assert data["platform_api_key"] == "trw_test_secret"


@pytest.mark.integration
class TestDeduplication:
    """Duplicates within the existing list or between existing+new are removed."""

    def test_existing_duplicates_collapsed(self, tmp_path: Path) -> None:
        """A user list with duplicates is normalized to first-occurrence-wins."""
        from trw_mcp.bootstrap._ide_targets import _update_config_target_platforms

        cfg = _seed_config(
            tmp_path,
            target_platforms=["claude-code", "opencode", "claude-code", "gemini"],
        )
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}

        _update_config_target_platforms(tmp_path, [], result)

        platforms = _read_platforms(cfg)
        assert platforms.count("claude-code") == 1
        assert platforms == ["claude-code", "opencode", "gemini"]
        # Duplicate collapse is a meaningful change → updated, not preserved
        assert str(cfg) in result["updated"]

    def test_new_ide_already_in_existing_list_no_op(self, tmp_path: Path) -> None:
        """Adding an IDE already in the list is a no-op — file preserved."""
        from trw_mcp.bootstrap._ide_targets import _update_config_target_platforms

        cfg = _seed_config(tmp_path, target_platforms=["claude-code", "cursor-ide"])
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}

        _update_config_target_platforms(tmp_path, ["cursor-ide"], result)

        platforms = _read_platforms(cfg)
        assert platforms == ["claude-code", "cursor-ide"]
        assert str(cfg) in result["preserved"]


@pytest.mark.integration
class TestFailOpen:
    """YAML parse + I/O errors do not block the dispatch chain."""

    def test_missing_config_returns_silently(self, tmp_path: Path) -> None:
        """No .trw/config.yaml → no-op, no error."""
        from trw_mcp.bootstrap._ide_targets import _update_config_target_platforms

        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}
        _update_config_target_platforms(tmp_path, ["cursor-ide"], result)

        # Empty result, no warnings
        assert result["created"] == []
        assert result["updated"] == []
        assert result["preserved"] == []
        assert "warnings" not in result or result["warnings"] == []

    def test_malformed_yaml_warning_recorded(self, tmp_path: Path) -> None:
        """Garbage YAML triggers a warning in result, not an exception."""
        from trw_mcp.bootstrap._ide_targets import _update_config_target_platforms

        cfg_dir = tmp_path / ".trw"
        cfg_dir.mkdir()
        (cfg_dir / "config.yaml").write_text("not: valid: yaml: [[[")

        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}
        _update_config_target_platforms(tmp_path, ["cursor-ide"], result)

        warnings = result.get("warnings", [])
        assert len(warnings) == 1
        assert "target_platforms config update skipped" in warnings[0]


from tests._structlog_capture import captured_structlog  # noqa: F401


@pytest.mark.integration
class TestObservability:
    """Structured logging emits the right events for monitoring."""

    def test_augmentation_emits_info_log_with_added_field(
        self, tmp_path: Path, captured_structlog: list[dict]
    ) -> None:
        """When new IDEs are added, structlog emits config_target_platforms_augmented."""
        from trw_mcp.bootstrap._ide_targets import _update_config_target_platforms

        _seed_config(tmp_path, target_platforms=["claude-code"])
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}

        _update_config_target_platforms(tmp_path, ["cursor-cli", "gemini"], result)

        info_logs = [
            log_entry for log_entry in captured_structlog
            if log_entry.get("event") == "config_target_platforms_augmented"
        ]
        assert len(info_logs) == 1
        log = info_logs[0]
        assert log["outcome"] == "success"
        assert log["previous"] == ["claude-code"]
        assert log["current"] == ["claude-code", "cursor-cli", "gemini"]
        assert log["added"] == ["cursor-cli", "gemini"]
        assert log["requested"] == ["cursor-cli", "gemini"]

    def test_no_change_emits_debug_unchanged_log(
        self, tmp_path: Path, captured_structlog: list[dict]
    ) -> None:
        """When merge is a no-op, structlog emits config_target_platforms_unchanged."""
        from trw_mcp.bootstrap._ide_targets import _update_config_target_platforms

        _seed_config(tmp_path, target_platforms=["claude-code", "cursor-ide"])
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": []}

        _update_config_target_platforms(tmp_path, ["cursor-ide"], result)

        unchanged_logs = [
            log_entry for log_entry in captured_structlog
            if log_entry.get("event") == "config_target_platforms_unchanged"
        ]
        assert len(unchanged_logs) == 1
