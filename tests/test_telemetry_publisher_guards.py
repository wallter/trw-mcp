"""Guard-condition tests for trw_mcp.telemetry.publisher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._test_telemetry_publisher_support import _make_config
from trw_mcp.telemetry.publisher import publish_learnings


class TestPublishOfflineMode:
    def test_publish_offline_mode_no_url(self) -> None:
        """Empty platform_url returns skipped_reason='offline_mode'."""
        cfg = _make_config(platform_url="", platform_telemetry_enabled=True)
        with patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg):
            result = publish_learnings()

        assert result["published"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert result["skipped_reason"] == "offline_mode"

    def test_publish_offline_mode_telemetry_disabled(self) -> None:
        """platform_telemetry_enabled=False returns skipped_reason='offline_mode'."""
        cfg = _make_config(platform_telemetry_enabled=False)
        with patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg):
            result = publish_learnings()

        assert result["skipped_reason"] == "offline_mode"


class TestPublishNoEntriesDir:
    def test_publish_no_entries_dir(self, tmp_path: Path) -> None:
        """Non-existent entries dir returns skipped_reason='no_entries'."""
        cfg = _make_config()
        trw_dir = tmp_path / ".trw"

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
        ):
            result = publish_learnings()

        assert result["published"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert result["skipped_reason"] == "no_entries"
