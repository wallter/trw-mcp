"""group_by parameter tests for the usage report tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_tools_usage_support import _get_report_tool_fn, _write_usage_record
from trw_mcp.state.persistence import FileStateWriter


@pytest.mark.unit
class TestUsageReportGroupBy:
    """Tests for group_by parameter on trw_usage_report."""

    async def test_group_by_model(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """group_by='model' groups records by model field."""
        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)
        log_path = logs_dir / "llm_usage.jsonl"

        writer = FileStateWriter()
        _write_usage_record(writer, log_path, model="claude-haiku-4-5-20251001", input_tokens=100, output_tokens=50)
        _write_usage_record(writer, log_path, model="claude-sonnet-4-6", input_tokens=200, output_tokens=100)
        _write_usage_record(writer, log_path, model="claude-haiku-4-5-20251001", input_tokens=50, output_tokens=25)
        monkeypatch.setattr("trw_mcp.tools.usage.resolve_trw_dir", lambda: tmp_path / ".trw")

        tool_fn = _get_report_tool_fn()
        result = tool_fn(group_by="model")

        assert "grouped_by" in result
        grouped = result["grouped_by"]
        assert "claude-haiku-4-5-20251001" in grouped
        assert "claude-sonnet-4-6" in grouped
        assert grouped["claude-haiku-4-5-20251001"]["calls"] == 2
        assert grouped["claude-sonnet-4-6"]["calls"] == 1
        assert result["group_by"] == "model"

    async def test_group_by_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """group_by='none' (default) does not add grouped_by key."""
        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)
        log_path = logs_dir / "llm_usage.jsonl"

        writer = FileStateWriter()
        _write_usage_record(writer, log_path)
        monkeypatch.setattr("trw_mcp.tools.usage.resolve_trw_dir", lambda: tmp_path / ".trw")

        tool_fn = _get_report_tool_fn()
        result = tool_fn(group_by="none")

        assert "grouped_by" not in result

    async def test_group_by_invalid_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid group_by value raises ValueError."""
        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)
        log_path = logs_dir / "llm_usage.jsonl"

        writer = FileStateWriter()
        _write_usage_record(writer, log_path)
        monkeypatch.setattr("trw_mcp.tools.usage.resolve_trw_dir", lambda: tmp_path / ".trw")

        tool_fn = _get_report_tool_fn()
        with pytest.raises(ValueError, match="group_by must be one of"):
            tool_fn(group_by="invalid_field")
