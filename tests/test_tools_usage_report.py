"""Usage report aggregation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_tools_usage_support import _get_report_tool_fn, _write_usage_record
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.tools.usage import _compute_cost


class TestUsageReportEmpty:
    """Test usage report with no JSONL file."""

    async def test_usage_report_empty_log(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """No JSONL file returns zero counts with message."""
        (tmp_path / ".trw" / "logs").mkdir(parents=True)
        monkeypatch.setattr("trw_mcp.tools.usage.resolve_trw_dir", lambda: tmp_path / ".trw")

        report_fn = _get_report_tool_fn()
        result = report_fn()

        assert result["total_calls"] == 0
        assert result["total_input_tokens"] == 0
        assert result["total_output_tokens"] == 0
        assert result["total_cost_estimate_usd"] == 0.0
        assert result["by_model"] == {}
        assert result["by_caller"] == {}
        assert "No LLM usage data found" in str(result.get("message", ""))

    async def test_usage_report_missing_trw_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing .trw dir returns empty result without error."""
        monkeypatch.setattr("trw_mcp.tools.usage.resolve_trw_dir", lambda: tmp_path / ".trw")

        report_fn = _get_report_tool_fn()
        result = report_fn()

        assert result["total_calls"] == 0


class TestUsageReportSingleRecord:
    """Test usage report with a single JSONL record."""

    async def test_usage_report_single_record(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One record returns correct totals."""
        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True)
        log_path = log_dir / "llm_usage.jsonl"
        _write_usage_record(
            writer,
            log_path,
            model="claude-haiku-4-5-20251001",
            input_tokens=150,
            output_tokens=80,
        )
        monkeypatch.setattr("trw_mcp.tools.usage.resolve_trw_dir", lambda: tmp_path / ".trw")

        report_fn = _get_report_tool_fn()
        result = report_fn()

        assert result["total_calls"] == 1
        assert result["total_input_tokens"] == 150
        assert result["total_output_tokens"] == 80
        expected_cost = _compute_cost("claude-haiku-4-5-20251001", 150, 80)
        assert result["total_cost_estimate_usd"] == pytest.approx(expected_cost, rel=1e-5)


class TestUsageReportMultipleRecords:
    """Test usage report aggregation across multiple records."""

    async def test_usage_report_multiple_records(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multiple records are aggregated correctly."""
        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True)
        log_path = log_dir / "llm_usage.jsonl"

        _write_usage_record(writer, log_path, input_tokens=100, output_tokens=50)
        _write_usage_record(writer, log_path, input_tokens=200, output_tokens=100)
        _write_usage_record(writer, log_path, input_tokens=300, output_tokens=150)
        monkeypatch.setattr("trw_mcp.tools.usage.resolve_trw_dir", lambda: tmp_path / ".trw")

        report_fn = _get_report_tool_fn()
        result = report_fn()

        assert result["total_calls"] == 3
        assert result["total_input_tokens"] == 600
        assert result["total_output_tokens"] == 300

    async def test_usage_report_by_model_grouping(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Different models are grouped correctly in by_model."""
        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True)
        log_path = log_dir / "llm_usage.jsonl"

        _write_usage_record(
            writer,
            log_path,
            model="claude-haiku-4-5-20251001",
            input_tokens=100,
            output_tokens=50,
        )
        _write_usage_record(
            writer,
            log_path,
            model="claude-haiku-4-5-20251001",
            input_tokens=200,
            output_tokens=100,
        )
        _write_usage_record(writer, log_path, model="claude-sonnet-4-6", input_tokens=500, output_tokens=250)
        monkeypatch.setattr("trw_mcp.tools.usage.resolve_trw_dir", lambda: tmp_path / ".trw")

        report_fn = _get_report_tool_fn()
        result = report_fn()

        by_model = result["by_model"]
        assert isinstance(by_model, dict)
        assert "claude-haiku-4-5-20251001" in by_model
        assert "claude-sonnet-4-6" in by_model

        haiku_entry = by_model["claude-haiku-4-5-20251001"]
        assert isinstance(haiku_entry, dict)
        assert haiku_entry["calls"] == 2
        assert haiku_entry["input_tokens"] == 300
        assert haiku_entry["output_tokens"] == 150

        sonnet_entry = by_model["claude-sonnet-4-6"]
        assert isinstance(sonnet_entry, dict)
        assert sonnet_entry["calls"] == 1
        assert sonnet_entry["input_tokens"] == 500
        assert sonnet_entry["output_tokens"] == 250

    async def test_usage_report_by_caller_grouping(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Different callers are grouped correctly in by_caller."""
        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True)
        log_path = log_dir / "llm_usage.jsonl"

        _write_usage_record(writer, log_path, caller="ask", input_tokens=100, output_tokens=50)
        _write_usage_record(writer, log_path, caller="ask", input_tokens=200, output_tokens=100)
        _write_usage_record(writer, log_path, caller="reflect", input_tokens=300, output_tokens=150)
        monkeypatch.setattr("trw_mcp.tools.usage.resolve_trw_dir", lambda: tmp_path / ".trw")

        report_fn = _get_report_tool_fn()
        result = report_fn()

        by_caller = result["by_caller"]
        assert isinstance(by_caller, dict)
        assert "ask" in by_caller
        assert "reflect" in by_caller

        ask_entry = by_caller["ask"]
        assert isinstance(ask_entry, dict)
        assert ask_entry["calls"] == 2
        assert ask_entry["input_tokens"] == 300
        assert ask_entry["output_tokens"] == 150

        reflect_entry = by_caller["reflect"]
        assert isinstance(reflect_entry, dict)
        assert reflect_entry["calls"] == 1
        assert reflect_entry["input_tokens"] == 300
        assert reflect_entry["output_tokens"] == 150
