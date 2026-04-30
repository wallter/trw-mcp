"""Targeted analytics report and reflection branch tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._analytics_branches_support import _reader, _write_run, _writer, analytics_mod, trw_dir
from trw_mcp.state.analytics import compute_reflection_quality
from trw_mcp.state.analytics.report import (
    _analyze_single_run,
    compute_ceremony_score,
    scan_all_runs,
)


class TestComputeReflectionQualityExceptionHandling:
    """Lines 929-930: exception handling in compute_reflection_quality."""

    def test_corrupt_reflection_file_skipped(self, trw_dir: Path) -> None:
        """Corrupt reflection YAML is skipped with continue — lines 929-930."""
        reflections_dir = trw_dir / "reflections"
        (reflections_dir / "valid_reflection.yaml").write_text(
            "id: R-valid\nscope: session\nnew_learnings: [L-a, L-b]\n"
            "timestamp: '2026-01-01T00:00:00Z'\nevents_analyzed: 3\n"
            "what_worked: []\nwhat_failed: []\nrepeated_patterns: []\n",
            encoding="utf-8",
        )
        (reflections_dir / "corrupt_reflection.yaml").write_bytes(b"\xff\xfe INVALID YAML \x00")

        result = compute_reflection_quality(trw_dir)
        assert result["diagnostics"]["reflection_count"] == 1
        assert result["score"] >= 0.0

    def test_all_corrupt_reflections_returns_zero_score(self, trw_dir: Path) -> None:
        """All corrupt reflections produce zero reflection components."""
        reflections_dir = trw_dir / "reflections"
        (reflections_dir / "bad1.yaml").write_bytes(b"\xff\xfe\x00\x01")
        (reflections_dir / "bad2.yaml").write_bytes(b"\xff\xfe\x00\x02")

        result = compute_reflection_quality(trw_dir)
        assert result["components"]["reflection_frequency"] == 0.0
        assert result["components"]["productivity"] == 0.0


class TestAnalyzeRunExceptionHandling:
    """Lines 193, 209-210, 215-216: _analyze_single_run exception paths."""

    def test_unreadable_run_yaml_returns_none(self, tmp_path: Path) -> None:
        """Unreadable run.yaml returns None from _analyze_single_run — line 193."""
        run_dir = tmp_path / "meta_run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_bytes(b"\xff\xfe\x00\x01 INVALID \x00")

        result = _analyze_single_run(run_dir)
        assert result is None

    def test_missing_run_yaml_returns_none(self, tmp_path: Path) -> None:
        """Missing run.yaml returns None — line 193 via line 192 exists() check."""
        run_dir = tmp_path / "empty_run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)

        result = _analyze_single_run(run_dir)
        assert result is None

    def test_corrupt_events_jsonl_run_still_scanned(self, tmp_path: Path) -> None:
        """Corrupt events.jsonl is skipped; run still analyzed — lines 209-210."""
        run_dir = tmp_path / "corrupt_events_run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        _writer.write_yaml(
            meta / "run.yaml",
            {
                "run_id": "20260101T000000Z-corrpt00",
                "task": "test",
                "status": "active",
                "phase": "implement",
            },
        )
        (meta / "events.jsonl").write_bytes(b"\xff\xfe INVALID JSON CONTENT \x00")

        original_read_jsonl = _reader.read_jsonl

        def raise_on_read(path: Path) -> list[dict[str, object]]:
            if "corrupt_events" in str(path):
                raise ValueError("simulated parse error")
            return original_read_jsonl(path)

        with patch.object(analytics_mod._reader, "read_jsonl", side_effect=raise_on_read):
            result = _analyze_single_run(run_dir)

        assert result is not None
        assert isinstance(result, dict)
        assert result["score"] == 0
        assert "run_id" in result

    def test_ceremony_score_exception_returns_null_score(self, tmp_path: Path) -> None:
        """compute_ceremony_score exception results in null score — lines 215-216."""
        run_dir = tmp_path / "bad_ceremony_run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        _writer.write_yaml(
            meta / "run.yaml",
            {
                "run_id": "20260101T000000Z-ceremon0",
                "task": "test",
                "status": "active",
                "phase": "implement",
            },
        )

        with patch(
            "trw_mcp.state.analytics.report.compute_ceremony_score",
            side_effect=RuntimeError("scoring exploded"),
        ):
            result = _analyze_single_run(run_dir)

        assert result is not None
        assert isinstance(result, dict)
        assert result["score"] is None
        assert result["session_start"] is False
        assert result["deliver"] is False
        assert "run_id" in result


class TestScanAllRunsExceptionPaths:
    """Lines 146-147, 180-181: scan_all_runs exception handling."""

    def test_run_dir_analysis_exception_added_to_parse_errors(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exception in _analyze_single_run is caught and added to parse_errors — lines 146-147."""
        from trw_mcp.models.config import TRWConfig

        mock_cfg = TRWConfig(task_root="docs")
        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod, "get_config", lambda: mock_cfg)
        monkeypatch.setattr(analytics_mod, "resolve_trw_dir", lambda: tmp_path / ".trw")

        run_dir = tmp_path / ".trw" / "runs" / "task-exc" / "20260101T000000Z-exc00000"
        (run_dir / "meta").mkdir(parents=True)
        _writer.write_yaml(
            run_dir / "meta" / "run.yaml",
            {
                "run_id": "20260101T000000Z-exc00000",
                "task": "task-exc",
                "status": "active",
                "phase": "implement",
            },
        )

        original_analyze = analytics_mod._analyze_single_run

        def raising_analyze(run_dir_arg: Path) -> dict[str, object] | None:
            if "exc00000" in run_dir_arg.name:
                raise RuntimeError("forced analysis error")
            return original_analyze(run_dir_arg)

        monkeypatch.setattr(analytics_mod, "_analyze_single_run", raising_analyze)

        result = scan_all_runs()
        assert any("exc00000" in str(e) for e in result["parse_errors"])
        assert result["runs_scanned"] == 0

    def test_cache_write_exception_does_not_propagate(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cache write exception is swallowed — lines 180-181 (except Exception: pass).

        Must create at least one valid run so the function reaches the cache write
        section rather than returning early via _empty_report.
        """
        from trw_mcp.models.config import TRWConfig

        mock_cfg = TRWConfig(task_root="docs")
        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod, "get_config", lambda: mock_cfg)

        _write_run(
            tmp_path,
            "cache-exc-task",
            "20260101T000000Z-cacheexc0",
            events=[{"event": "session_start"}],
        )

        call_count = [0]

        def counted_resolve_trw_dir() -> Path:
            call_count[0] += 1
            if call_count[0] == 1:
                return tmp_path / ".trw"
            raise RuntimeError("no trw dir available")

        monkeypatch.setattr(analytics_mod, "resolve_trw_dir", counted_resolve_trw_dir)

        result = scan_all_runs()
        assert "runs" in result
        assert "aggregate" in result
        assert result["runs_scanned"] == 1


class TestCeremonyScoreToolInvocationPaths:
    """Additional compute_ceremony_score paths for tool_invocation events."""

    def test_tool_invocation_session_start(self) -> None:
        """tool_invocation event with tool_name=trw_session_start counts as session_start."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_session_start"},
        ]
        result = compute_ceremony_score(events)
        assert result["session_start"] is True
        assert result["score"] == 25

    def test_tool_invocation_deliver(self) -> None:
        """tool_invocation with tool_name=trw_deliver counts as deliver."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_deliver"},
        ]
        result = compute_ceremony_score(events)
        assert result["deliver"] is True
        assert result["score"] == 25

    def test_tool_invocation_reflect(self) -> None:
        """tool_invocation with tool_name=trw_reflect counts as deliver."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_reflect"},
        ]
        result = compute_ceremony_score(events)
        assert result["deliver"] is True

    def test_tool_invocation_checkpoint(self) -> None:
        """tool_invocation with tool_name=trw_checkpoint counts as checkpoint."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_checkpoint"},
        ]
        result = compute_ceremony_score(events)
        assert result["checkpoint_count"] == 1
        assert result["score"] == 20

    def test_tool_invocation_learn(self) -> None:
        """tool_invocation with tool_name=trw_learn counts as learn."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_learn"},
        ]
        result = compute_ceremony_score(events)
        assert result["learn_count"] == 1
        assert result["score"] == 10

    def test_tool_invocation_build_check(self) -> None:
        """tool_invocation with tool_name=trw_build_check counts as build_check."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_build_check", "tests_passed": "true"},
        ]
        result = compute_ceremony_score(events)
        assert result["build_check"] is True
        assert result["score"] == 10

    def test_trw_deliver_complete_event(self) -> None:
        """trw_deliver_complete event counts as deliver."""
        events: list[dict[str, object]] = [
            {"event": "trw_deliver_complete"},
        ]
        result = compute_ceremony_score(events)
        assert result["deliver"] is True
        assert result["score"] == 25
