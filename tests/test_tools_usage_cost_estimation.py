"""Usage report cost estimation tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_tools_usage_support import _get_report_tool_fn, _write_usage_record
from trw_mcp.state.persistence import FileStateWriter


class TestUsageReportCostEstimation:
    """Test cost estimation in the usage report via the registered tool."""

    async def _run_report(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> dict[str, object]:
        """Patch resolve_trw_dir and call the registered tool function."""
        monkeypatch.setattr("trw_mcp.tools.usage.resolve_trw_dir", lambda: tmp_path / ".trw")
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
            writer,
            log_path,
            model="claude-haiku-4-5-20251001",
            input_tokens=1_000_000,
            output_tokens=0,
        )

        result = await self._run_report(tmp_path, monkeypatch)
        assert result["total_cost_estimate_usd"] == pytest.approx(0.80, rel=1e-5)

    async def test_usage_report_cost_haiku_output(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify haiku output pricing: $4.00 per Mtok."""
        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True)
        log_path = log_dir / "llm_usage.jsonl"
        _write_usage_record(
            writer,
            log_path,
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
            writer,
            log_path,
            model="claude-sonnet-4-6",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )

        result = await self._run_report(tmp_path, monkeypatch)
        assert result["total_cost_estimate_usd"] == pytest.approx(18.00, rel=1e-5)

    async def test_usage_report_cost_unknown_model(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unknown model uses default sonnet rate."""
        log_dir = tmp_path / ".trw" / "logs"
        log_dir.mkdir(parents=True)
        log_path = log_dir / "llm_usage.jsonl"
        _write_usage_record(
            writer,
            log_path,
            model="some-unknown-model-v999",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )

        result = await self._run_report(tmp_path, monkeypatch)
        assert result["total_cost_estimate_usd"] == pytest.approx(18.00, rel=1e-5)

    async def test_usage_report_result_includes_log_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Result dict includes log_path and period keys."""
        result = await self._run_report(tmp_path, monkeypatch)
        assert "log_path" in result
        assert "period" in result
        assert result["period"] == "all"
