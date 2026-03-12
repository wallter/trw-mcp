"""Tests for TRW LLM usage report tool — PRD-CORE-020."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.tools.usage import _compute_cost

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_usage_record(
    writer: FileStateWriter,
    log_path: Path,
    *,
    model: str = "claude-haiku-4-5-20251001",
    input_tokens: int = 150,
    output_tokens: int = 80,
    latency_ms: float = 1234.5,
    caller: str = "ask",
    success: bool = True,
) -> None:
    """Write a single usage record to the JSONL log."""
    writer.append_jsonl(log_path, {
        "ts": "2026-02-20T12:00:00Z",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
        "caller": caller,
        "success": success,
    })


def _get_report_tool_fn() -> Callable[..., Any]:
    """Extract the trw_usage_report fn from the FastMCP server."""
    from tests.conftest import extract_tool_fn, make_test_server

    return extract_tool_fn(make_test_server("usage"), "trw_usage_report")  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Cost Computation Tests
# ---------------------------------------------------------------------------


class TestComputeCost:
    """Tests for _compute_cost helper function."""

    def test_usage_report_cost_haiku(self) -> None:
        """Haiku pricing: $0.80 input, $4.00 output per Mtok."""
        cost = _compute_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        assert cost == pytest.approx(4.80, rel=1e-6)

    def test_usage_report_cost_haiku_small(self) -> None:
        """Haiku pricing for small token counts."""
        cost = _compute_cost("claude-haiku-4-5-20251001", 1000, 500)
        # input: 1000 * 0.80 / 1M = 0.0008
        # output: 500 * 4.00 / 1M = 0.002
        assert cost == pytest.approx(0.0028, rel=1e-5)

    def test_usage_report_cost_sonnet(self) -> None:
        """Sonnet pricing: $3.00 input, $15.00 output per Mtok."""
        cost = _compute_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
        assert cost == pytest.approx(18.00, rel=1e-6)

    def test_usage_report_cost_sonnet_small(self) -> None:
        """Sonnet pricing for small token counts."""
        cost = _compute_cost("claude-sonnet-4-6", 1000, 500)
        # input: 1000 * 3.00 / 1M = 0.003
        # output: 500 * 15.00 / 1M = 0.0075
        assert cost == pytest.approx(0.0105, rel=1e-5)

    def test_usage_report_cost_unknown_model(self) -> None:
        """Unknown model uses default sonnet rate ($3.00/$15.00)."""
        cost_unknown = _compute_cost("gpt-4-turbo", 1_000_000, 1_000_000)
        cost_sonnet = _compute_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
        assert cost_unknown == cost_sonnet

    def test_cost_zero_tokens(self) -> None:
        """Zero tokens yields zero cost."""
        cost = _compute_cost("claude-haiku-4-5-20251001", 0, 0)
        assert cost == 0.0

    def test_cost_rounded_to_six_places(self) -> None:
        """Cost is rounded to 6 decimal places."""
        cost = _compute_cost("claude-haiku-4-5-20251001", 1, 1)
        # (1 * 0.80 + 1 * 4.00) / 1_000_000 = 4.8e-6
        # round(4.8e-6, 6) = round(0.0000048, 6) = 0.000005 = 5e-6
        assert cost == pytest.approx(5e-6, rel=1e-4)


# ---------------------------------------------------------------------------
# Usage Report Tool Tests (using the registered tool fn via FastMCP)
# ---------------------------------------------------------------------------


class TestUsageReportEmpty:
    """Test usage report with no JSONL file."""

    async def test_usage_report_empty_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No JSONL file returns zero counts with message."""
        (tmp_path / ".trw" / "logs").mkdir(parents=True)

        monkeypatch.setattr(
            "trw_mcp.tools.usage.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )
        report_fn = _get_report_tool_fn()
        result = report_fn()

        assert result["total_calls"] == 0
        assert result["total_input_tokens"] == 0
        assert result["total_output_tokens"] == 0
        assert result["total_cost_estimate_usd"] == 0.0
        assert result["by_model"] == {}
        assert result["by_caller"] == {}
        assert "No LLM usage data found" in str(result.get("message", ""))

    async def test_usage_report_missing_trw_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing .trw dir returns empty result without error."""
        monkeypatch.setattr(
            "trw_mcp.tools.usage.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )
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
            writer, log_path,
            model="claude-haiku-4-5-20251001",
            input_tokens=150,
            output_tokens=80,
        )

        monkeypatch.setattr(
            "trw_mcp.tools.usage.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )
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

        monkeypatch.setattr(
            "trw_mcp.tools.usage.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )
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
            writer, log_path,
            model="claude-haiku-4-5-20251001",
            input_tokens=100, output_tokens=50,
        )
        _write_usage_record(
            writer, log_path,
            model="claude-haiku-4-5-20251001",
            input_tokens=200, output_tokens=100,
        )
        _write_usage_record(
            writer, log_path,
            model="claude-sonnet-4-6",
            input_tokens=500, output_tokens=250,
        )

        monkeypatch.setattr(
            "trw_mcp.tools.usage.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )
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

        monkeypatch.setattr(
            "trw_mcp.tools.usage.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )
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


class TestUsageReportCostEstimation:
    """Test cost estimation in the usage report via the registered tool."""

    async def _run_report(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> dict[str, object]:
        """Patch resolve_trw_dir and call the registered tool function."""
        monkeypatch.setattr(
            "trw_mcp.tools.usage.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )
        report_fn = _get_report_tool_fn()
        return report_fn()  # type: ignore[return-value]

    async def test_usage_report_cost_haiku(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify haiku input pricing: $0.80 per Mtok."""
        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True)
        log_path = log_dir / "llm_usage.jsonl"

        _write_usage_record(
            writer, log_path,
            model="claude-haiku-4-5-20251001",
            input_tokens=1_000_000,
            output_tokens=0,
        )

        result = await self._run_report(tmp_path, monkeypatch)
        # 1M input * $0.80/Mtok = $0.80
        assert result["total_cost_estimate_usd"] == pytest.approx(0.80, rel=1e-5)

    async def test_usage_report_cost_haiku_output(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify haiku output pricing: $4.00 per Mtok."""
        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True)
        log_path = log_dir / "llm_usage.jsonl"

        _write_usage_record(
            writer, log_path,
            model="claude-haiku-4-5-20251001",
            input_tokens=0,
            output_tokens=1_000_000,
        )

        result = await self._run_report(tmp_path, monkeypatch)
        assert result["total_cost_estimate_usd"] == pytest.approx(4.00, rel=1e-5)

    async def test_usage_report_cost_sonnet(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify sonnet pricing: $3.00/$15.00 per Mtok."""
        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True)
        log_path = log_dir / "llm_usage.jsonl"

        _write_usage_record(
            writer, log_path,
            model="claude-sonnet-4-6",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )

        result = await self._run_report(tmp_path, monkeypatch)
        # $3.00 + $15.00 = $18.00
        assert result["total_cost_estimate_usd"] == pytest.approx(18.00, rel=1e-5)

    async def test_usage_report_cost_unknown_model(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unknown model uses default sonnet rate."""
        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True)
        log_path = log_dir / "llm_usage.jsonl"

        _write_usage_record(
            writer, log_path,
            model="some-unknown-model-v999",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )

        result = await self._run_report(tmp_path, monkeypatch)
        # Same as sonnet rate (default fallback)
        assert result["total_cost_estimate_usd"] == pytest.approx(18.00, rel=1e-5)

    async def test_usage_report_result_includes_log_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Result dict includes log_path and period keys."""
        result = await self._run_report(tmp_path, monkeypatch)
        assert "log_path" in result
        assert "period" in result
        assert result["period"] == "all"


# ---------------------------------------------------------------------------
# Config Field Tests
# ---------------------------------------------------------------------------


class TestConfigUsageFields:
    """Test TRWConfig fields added for PRD-CORE-020."""

    def test_config_usage_fields(self) -> None:
        """TRWConfig has llm_usage_log_enabled=True and llm_usage_log_file='llm_usage.jsonl'."""
        config = TRWConfig()
        assert config.llm_usage_log_enabled is True
        assert config.llm_usage_log_file == "llm_usage.jsonl"

    def test_config_usage_log_enabled_default_true(self) -> None:
        """llm_usage_log_enabled defaults to True."""
        config = TRWConfig()
        assert config.llm_usage_log_enabled is True

    def test_config_usage_log_file_default(self) -> None:
        """llm_usage_log_file defaults to 'llm_usage.jsonl'."""
        config = TRWConfig()
        assert config.llm_usage_log_file == "llm_usage.jsonl"

    def test_config_usage_log_enabled_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """llm_usage_log_enabled can be overridden via env var."""
        monkeypatch.setenv("TRW_LLM_USAGE_LOG_ENABLED", "false")
        config = TRWConfig()
        assert config.llm_usage_log_enabled is False

    def test_config_usage_log_file_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """llm_usage_log_file can be overridden via env var."""
        monkeypatch.setenv("TRW_LLM_USAGE_LOG_FILE", "custom_usage.jsonl")
        config = TRWConfig()
        assert config.llm_usage_log_file == "custom_usage.jsonl"


# ---------------------------------------------------------------------------
# group_by parameter tests (INFRA-029 FR02)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUsageReportGroupBy:
    """Tests for group_by parameter on trw_usage_report."""

    async def test_group_by_model(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    async def test_group_by_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    async def test_group_by_invalid_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
