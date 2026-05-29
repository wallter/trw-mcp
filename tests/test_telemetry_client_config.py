"""Tests for TelemetryClient.from_config."""

from __future__ import annotations

from unittest.mock import patch

from trw_mcp.telemetry.client import TelemetryClient


class TestTelemetryClientFromConfig:
    def test_from_config_disabled_when_telemetry_enabled_false(self, tmp_path) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            trw_dir=str(tmp_path / ".trw"),
            telemetry_enabled=False,
        )
        with (
            patch("trw_mcp.models.config.get_config", return_value=cfg),
            patch(
                "trw_mcp.telemetry.client.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ),
        ):
            client = TelemetryClient.from_config()

        assert client.enabled is False

    def test_from_config_enabled_when_telemetry_enabled_true(self, tmp_path) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            trw_dir=str(tmp_path / ".trw"),
            telemetry_enabled=True,
        )
        with (
            patch("trw_mcp.models.config.get_config", return_value=cfg),
            patch(
                "trw_mcp.telemetry.client.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ),
        ):
            client = TelemetryClient.from_config()

        assert client.enabled is True

    def test_from_config_output_path_uses_logs_dir_and_telemetry_file(self, tmp_path) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            trw_dir=str(tmp_path / ".trw"),
            logs_dir="logs",
            telemetry_file="custom-telemetry.jsonl",
        )
        trw_dir = tmp_path / ".trw"
        with (
            patch("trw_mcp.models.config.get_config", return_value=cfg),
            patch(
                "trw_mcp.telemetry.client.resolve_trw_dir",
                return_value=trw_dir,
            ),
        ):
            client = TelemetryClient.from_config()

        expected = trw_dir / "logs" / "custom-telemetry.jsonl"
        assert client._output_path == expected
