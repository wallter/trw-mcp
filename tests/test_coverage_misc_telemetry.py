"""Misc coverage tests for telemetry publishing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


class TestPublisherCoverage:
    """Cover publisher.py uncovered lines."""

    def test_publish_learnings_empty_data_continue(self, tmp_path: Path) -> None:
        """Line 49: reader.read_yaml returns empty/falsy data → continue."""
        from trw_mcp.telemetry import publisher as pub

        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        empty_file = entries_dir / "empty.yaml"
        empty_file.write_text("", encoding="utf-8")

        with patch("trw_mcp.telemetry.publisher.get_config") as mock_cfg:
            cfg = MagicMock()
            cfg.platform_url = "http://test.example.com"
            cfg.effective_platform_urls = ["http://test.example.com"]
            cfg.platform_telemetry_enabled = True
            cfg.installation_id = "test"
            mock_cfg.return_value = cfg

            with patch("trw_mcp.telemetry.publisher.resolve_trw_dir") as mock_trw:
                mock_trw.return_value = tmp_path

                with patch("trw_mcp.telemetry.publisher.FileStateReader") as mock_reader_cls:
                    mock_reader = MagicMock()
                    mock_reader.read_yaml.return_value = {}
                    mock_reader_cls.return_value = mock_reader

                    result = pub.publish_learnings()

        assert result["published"] == 0

    def test_publish_learnings_tags_not_list_coerced_to_empty(self, tmp_path: Path) -> None:
        """Line 76: tags field is not a list → coerced to []."""
        from trw_mcp.telemetry import publisher as pub

        entries_dir = tmp_path / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        yaml_file = entries_dir / "learning.yaml"
        yaml_file.write_text(
            "status: active\nimpact: 0.9\nsummary: Test\ndetail: Detail\ntags: not-a-list\n",
            encoding="utf-8",
        )

        with patch("trw_mcp.telemetry.publisher.get_config") as mock_cfg:
            cfg = MagicMock()
            cfg.platform_url = "http://test.example.com"
            cfg.effective_platform_urls = ["http://test.example.com"]
            cfg.platform_telemetry_enabled = True
            cfg.installation_id = "test"
            cfg.platform_api_key.get_secret_value.return_value = ""
            mock_cfg.return_value = cfg

            with patch("trw_mcp.telemetry.publisher.resolve_trw_dir") as mock_trw:
                mock_trw.return_value = tmp_path

                with patch("trw_mcp.telemetry.publisher.FileStateReader") as mock_reader_cls:
                    mock_reader = MagicMock()
                    mock_reader.read_yaml.return_value = {
                        "status": "active",
                        "impact": 0.9,
                        "summary": "Test summary",
                        "detail": "Test detail",
                        "tags": "not-a-list",
                        "published_to_platform": False,
                    }
                    mock_reader_cls.return_value = mock_reader

                    with patch("trw_mcp.telemetry.publisher.strip_pii", side_effect=lambda x: x):
                        with patch("trw_mcp.telemetry.publisher.embed", return_value=[0.1]):
                            with patch("trw_mcp.telemetry.publisher._post_learning", return_value=True):
                                result = pub.publish_learnings()

        assert result["errors"] == 0
        assert result["published"] == 1
