"""Coverage gap tests for resources/config.py, export.py, and telemetry/sender.py.

Targets:
  - trw_mcp.resources.config  (missing lines 99-100, 112-122, 127-131)
  - trw_mcp.export             (missing lines 35-36, 50, 55, 58-59, 66-68,
                                 111, 128-131, 139-140, 143, 151-155, 258,
                                 261, 269, 273-274, 283, 295, 347)
  - trw_mcp.telemetry.sender   (missing lines 127-143)
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.state.persistence import FileStateWriter

_writer = FileStateWriter()


# ===========================================================================
# Helpers shared across sections
# ===========================================================================


def _setup_trw(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal .trw/ structure. Returns (project_root, trw_dir)."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "context").mkdir(parents=True)
    return tmp_path, trw_dir


def _write_learning(entries_dir: Path, name: str, data: dict[str, object]) -> None:
    _writer.write_yaml(entries_dir / name, data)


# ===========================================================================
# resources/config.py — get_learnings_summary coverage gaps
# ===========================================================================


def _get_learnings_resource() -> Any:
    """Return the trw://learnings/summary resource function."""
    from fastmcp import FastMCP
    from trw_mcp.resources.config import register_config_resources

    srv = FastMCP("test")
    register_config_resources(srv)
    return srv._resource_manager._resources["trw://learnings/summary"].fn


class TestLearningsSummaryErrorHandling:
    """Lines 99-100 — bad YAML in entries directory is silently skipped."""

    def test_skips_unreadable_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        # Write a valid high-impact entry
        _write_learning(
            entries_dir,
            "good-entry.yaml",
            {"id": "L-001", "summary": "Good one", "detail": "d", "impact": 0.9},
        )
        # Write a file that will raise an error on read
        bad_file = entries_dir / "bad-entry.yaml"
        bad_file.write_text("!!python/object:os.system [rm -rf /]", encoding="utf-8")

        fn = _get_learnings_resource()
        result = fn()
        # Should still return a summary and include the good entry
        assert "TRW Learnings Summary" in result
        # Good entry must still appear; bad entry is silently skipped
        assert "Good one" in result

    def test_entry_below_impact_threshold_excluded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        _write_learning(
            entries_dir,
            "low-impact.yaml",
            {"id": "L-002", "summary": "Low impact", "detail": "d", "impact": 0.5},
        )

        fn = _get_learnings_resource()
        result = fn()
        assert "Low impact" not in result


class TestLearningsSummaryPatternsSection:
    """Lines 112-122 — patterns_dir exists branch."""

    def test_patterns_included_in_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)

        # Create patterns directory with a pattern file
        patterns_dir = trw_dir / "patterns"
        patterns_dir.mkdir()
        _writer.write_yaml(
            patterns_dir / "wave-audit.yaml",
            {"name": "Wave Audit Pattern", "description": "Run 3-wave audit"},
        )

        fn = _get_learnings_resource()
        result = fn()
        assert "Discovered Patterns" in result
        assert "Wave Audit Pattern" in result
        assert "Run 3-wave audit" in result

    def test_patterns_index_yaml_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)

        patterns_dir = trw_dir / "patterns"
        patterns_dir.mkdir()
        # index.yaml must be skipped
        _writer.write_yaml(
            patterns_dir / "index.yaml",
            {"name": "index", "description": "should not appear"},
        )
        _writer.write_yaml(
            patterns_dir / "real-pattern.yaml",
            {"name": "Real Pattern", "description": "Should appear"},
        )

        fn = _get_learnings_resource()
        result = fn()
        assert "Real Pattern" in result
        # index.yaml content must not appear as a pattern entry
        assert "should not appear" not in result

    def test_bad_pattern_file_silently_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)

        patterns_dir = trw_dir / "patterns"
        patterns_dir.mkdir()
        # Write an unreadable pattern file
        bad = patterns_dir / "corrupt.yaml"
        bad.write_text("!!python/object:os.system [ls]", encoding="utf-8")
        _writer.write_yaml(
            patterns_dir / "good.yaml",
            {"name": "Good Pattern", "description": "Fine"},
        )

        fn = _get_learnings_resource()
        result = fn()
        assert "Good Pattern" in result


class TestLearningsSummaryAnalyticsSection:
    """Lines 127-131 — analytics.yaml exists branch."""

    def test_analytics_section_included(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        _writer.write_yaml(
            context_dir / "analytics.yaml",
            {
                "sessions_tracked": 42,
                "total_learnings": 100,
                "avg_learnings_per_session": 2.38,
            },
        )

        fn = _get_learnings_resource()
        result = fn()
        assert "Analytics" in result
        assert "42" in result
        assert "100" in result


# ===========================================================================
# export.py coverage gaps
# ===========================================================================


def _setup_project(tmp_path: Path) -> Path:
    """Create minimal .trw structure for export tests."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _make_entry(
    entries_dir: Path,
    *,
    summary: str = "Test learning",
    impact: float = 0.8,
    tags: list[str] | None = None,
    created: str = "2026-02-21T00:00:00Z",
) -> None:
    import uuid

    entry_id = f"L-{uuid.uuid4().hex[:8]}"
    slug = summary.lower().replace(" ", "-")[:40]
    _writer.write_yaml(
        entries_dir / f"2026-02-21-{slug}.yaml",
        {
            "id": entry_id,
            "summary": summary,
            "detail": f"Detail for: {summary}",
            "impact": impact,
            "status": "active",
            "tags": tags or ["test"],
            "source_type": "agent",
            "created": created,
            "updated": "2026-02-21T00:00:00Z",
            "q_value": 0.5,
            "access_count": 1,
        },
    )


class TestLoadProjectConfig:
    """Line 35-36 — _load_project_config reads existing config.yaml."""

    def test_loads_existing_config(self, tmp_path: Path) -> None:
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        trw_dir = project / ".trw"
        # Write a config.yaml with a custom framework_version
        _writer.write_yaml(
            trw_dir / "config.yaml",
            {"framework_version": "v99.0_CUSTOM"},
        )
        entries_dir = trw_dir / "learnings" / "entries"
        _make_entry(entries_dir, summary="Entry with custom config")

        result = export_data(project, "learnings")
        assert result["status"] == "ok"
        meta = result.get("metadata")
        assert isinstance(meta, dict)
        assert meta.get("trw_version") == "v99.0_CUSTOM"


class TestCollectLearningsEdgeCases:
    """Lines 50, 55, 58-59, 66-68 — _collect_learnings edge cases."""

    def test_entries_not_a_directory_returns_empty(self, tmp_path: Path) -> None:
        """Line 50 — entries_dir is not a directory."""
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        # Remove the entries directory and replace with a file
        entries_path = project / ".trw" / "learnings" / "entries"
        import shutil
        shutil.rmtree(entries_path)
        entries_path.write_text("not a directory", encoding="utf-8")

        result = export_data(project, "learnings")
        assert result["status"] == "ok"
        learnings = result.get("learnings")
        assert isinstance(learnings, list)
        assert len(learnings) == 0

    def test_index_yaml_skipped(self, tmp_path: Path) -> None:
        """Line 55 — index.yaml is skipped during collection."""
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        # Write index.yaml — should be excluded from results
        _writer.write_yaml(
            entries_dir / "index.yaml",
            {"id": "INDEX", "summary": "Index entry", "impact": 0.9},
        )
        _make_entry(entries_dir, summary="Real entry")

        result = export_data(project, "learnings")
        learnings = result.get("learnings")
        assert isinstance(learnings, list)
        assert len(learnings) == 1
        assert learnings[0]["summary"] == "Real entry"

    def test_unreadable_entry_silently_skipped(self, tmp_path: Path) -> None:
        """Lines 58-59 — exception in read_yaml is caught and skipped."""
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Good entry")
        # Write a file that will cause a parse error
        bad_file = entries_dir / "2026-02-21-bad.yaml"
        bad_file.write_text("!!python/object:os.system [rm -rf /]", encoding="utf-8")

        result = export_data(project, "learnings")
        learnings = result.get("learnings")
        assert isinstance(learnings, list)
        assert len(learnings) == 1
        assert learnings[0]["summary"] == "Good entry"

    def test_since_filter_excludes_older_entries(self, tmp_path: Path) -> None:
        """Lines 66-68 — since parameter filters by created date."""
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Old entry", created="2026-01-01T00:00:00Z")
        _make_entry(entries_dir, summary="New entry", created="2026-02-15T00:00:00Z")

        result = export_data(project, "learnings", since="2026-02-01")
        learnings = result.get("learnings")
        assert isinstance(learnings, list)
        assert len(learnings) == 1
        assert learnings[0]["summary"] == "New entry"

    def test_since_filter_includes_entries_on_boundary(self, tmp_path: Path) -> None:
        """Since filter uses string comparison — entry on boundary included."""
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(
            entries_dir, summary="Boundary entry", created="2026-02-01T00:00:00Z"
        )

        result = export_data(project, "learnings", since="2026-02-01")
        learnings = result.get("learnings")
        assert isinstance(learnings, list)
        assert len(learnings) == 1


class TestCollectRunsEnvRestore:
    """Line 111 — TRW_PROJECT_ROOT env var restored when it was previously set."""

    def test_existing_env_var_restored_after_collect_runs(
        self, tmp_path: Path
    ) -> None:
        import os
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)

        original_val = "original_project_root"
        os.environ["TRW_PROJECT_ROOT"] = original_val
        try:
            with patch("trw_mcp.export.scan_all_runs", return_value={"runs": []}):
                result = export_data(project, "runs")
            assert result["status"] == "ok"
            # The original env var must be restored
            assert os.environ.get("TRW_PROJECT_ROOT") == original_val
        finally:
            if os.environ.get("TRW_PROJECT_ROOT") == original_val:
                del os.environ["TRW_PROJECT_ROOT"]


class TestCollectAnalyticsEdgeCases:
    """Lines 128-131, 139-140, 143, 151-155 — _collect_analytics branches."""

    def test_analytics_yaml_exists_and_included(self, tmp_path: Path) -> None:
        """Lines 128-131 — analytics.yaml loaded into result."""
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        trw_dir = project / ".trw"
        _writer.write_yaml(
            trw_dir / "context" / "analytics.yaml",
            {"sessions_tracked": 7, "total_learnings": 42},
        )

        with patch("trw_mcp.export.compute_reflection_quality", return_value=0.75):
            result = export_data(project, "analytics")

        assert result["status"] == "ok"
        analytics = result.get("analytics")
        assert isinstance(analytics, dict)
        session_analytics = analytics.get("session_analytics")
        assert isinstance(session_analytics, dict)
        assert session_analytics.get("sessions_tracked") == 7

    def test_analytics_yaml_read_error_silently_ignored(
        self, tmp_path: Path
    ) -> None:
        """Lines 128-131 exception branch — read error does not raise."""
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        trw_dir = project / ".trw"
        context_dir = trw_dir / "context"
        analytics_file = context_dir / "analytics.yaml"
        analytics_file.write_text(
            "!!python/object:os.system [rm -rf /]", encoding="utf-8"
        )

        with patch("trw_mcp.export.compute_reflection_quality", return_value=0.5):
            result = export_data(project, "analytics")

        # Should succeed even if analytics.yaml is unreadable
        assert result["status"] == "ok"

    def test_reflection_quality_exception_silently_ignored(
        self, tmp_path: Path
    ) -> None:
        """Lines 139-140 — compute_reflection_quality exception is caught."""
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)

        with patch(
            "trw_mcp.export.compute_reflection_quality",
            side_effect=RuntimeError("boom"),
        ):
            result = export_data(project, "analytics")

        assert result["status"] == "ok"
        # reflection_quality key absent (exception was caught)
        analytics = result.get("analytics", {})
        assert isinstance(analytics, dict)
        # No reflection_quality if exception occurred
        assert "reflection_quality" not in analytics

    def test_analytics_env_restored_when_previously_set(
        self, tmp_path: Path
    ) -> None:
        """Line 143 — TRW_PROJECT_ROOT restored in analytics env block."""
        import os
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)

        original_val = "pre_analytics_root"
        os.environ["TRW_PROJECT_ROOT"] = original_val
        try:
            with patch(
                "trw_mcp.export.compute_reflection_quality", return_value=0.5
            ):
                result = export_data(project, "analytics")
            assert result["status"] == "ok"
            assert os.environ.get("TRW_PROJECT_ROOT") == original_val
        finally:
            if os.environ.get("TRW_PROJECT_ROOT") == original_val:
                del os.environ["TRW_PROJECT_ROOT"]

    def test_cached_report_ceremony_aggregates_included(
        self, tmp_path: Path
    ) -> None:
        """Lines 151-155 — analytics-report.yaml loaded for ceremony_aggregates."""
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        trw_dir = project / ".trw"
        _writer.write_yaml(
            trw_dir / "context" / "analytics-report.yaml",
            {"aggregate": {"avg_ceremony_score": 72.5, "runs_analyzed": 10}},
        )

        with patch("trw_mcp.export.compute_reflection_quality", return_value=0.6):
            result = export_data(project, "analytics")

        analytics = result.get("analytics", {})
        assert isinstance(analytics, dict)
        ceremony = analytics.get("ceremony_aggregates")
        assert isinstance(ceremony, dict)
        assert ceremony.get("avg_ceremony_score") == 72.5

    def test_cached_report_read_error_silently_ignored(
        self, tmp_path: Path
    ) -> None:
        """Lines 151-155 exception branch — bad analytics-report.yaml is skipped."""
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        trw_dir = project / ".trw"
        bad = trw_dir / "context" / "analytics-report.yaml"
        bad.write_text("!!python/object:os.system [ls]", encoding="utf-8")

        with patch("trw_mcp.export.compute_reflection_quality", return_value=0.6):
            result = export_data(project, "analytics")

        assert result["status"] == "ok"
        analytics = result.get("analytics", {})
        assert isinstance(analytics, dict)
        assert "ceremony_aggregates" not in analytics


class TestImportSourceValidation:
    """Lines 258, 261 — invalid source file shape."""

    def test_source_is_invalid_dict_no_learnings_key(
        self, tmp_path: Path
    ) -> None:
        """Line 258 — dict without 'learnings' key returns error."""
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps({"not_learnings": "value"}), encoding="utf-8")

        result = import_learnings(source_file, target)
        assert result["status"] == "failed"
        assert "learnings" in str(result.get("error", "")).lower()

    def test_source_learnings_key_is_not_a_list(self, tmp_path: Path) -> None:
        """Line 261 — learnings value is not a list."""
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        source_file = tmp_path / "export.json"
        source_file.write_text(
            json.dumps({"learnings": "should be a list"}), encoding="utf-8"
        )

        result = import_learnings(source_file, target)
        assert result["status"] == "failed"
        assert "list" in str(result.get("error", "")).lower()


class TestImportDeduplicationEdgeCases:
    """Lines 269, 273-274 — dedup loop edge cases."""

    def test_index_yaml_skipped_during_dedup_load(self, tmp_path: Path) -> None:
        """Line 269 — index.yaml in entries_dir is skipped during dedup load."""
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        entries_dir = target / ".trw" / "learnings" / "entries"
        # Write index.yaml — should be skipped when loading existing summaries
        _writer.write_yaml(
            entries_dir / "index.yaml",
            {"id": "INDEX", "summary": "Index file content"},
        )

        source_data = [
            {
                "summary": "New unique learning",
                "detail": "Some detail",
                "impact": 0.8,
            }
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target)
        assert result["status"] == "ok"
        assert result["imported"] == 1

    def test_unreadable_existing_entry_skipped_during_dedup(
        self, tmp_path: Path
    ) -> None:
        """Lines 273-274 — bad existing entry during dedup load is skipped."""
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        entries_dir = target / ".trw" / "learnings" / "entries"
        # Write a corrupted existing entry
        bad = entries_dir / "2026-02-21-corrupt.yaml"
        bad.write_text("!!python/object:os.system [ls]", encoding="utf-8")

        source_data = [
            {
                "summary": "Learning that should import fine",
                "detail": "Detail",
                "impact": 0.8,
            }
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target)
        assert result["status"] == "ok"
        assert result["imported"] == 1


class TestImportTagFilterNonList:
    """Lines 283, 295 — tag filter when entry_tags is not a list."""

    def test_non_list_tags_in_source_entry_treated_as_no_tags(
        self, tmp_path: Path
    ) -> None:
        """Line 283 / 295 — when entry's tags is not a list, treated as empty."""
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        source_data = [
            {
                "summary": "Entry with non-list tags",
                "detail": "Detail",
                "impact": 0.8,
                # tags is a string instead of a list
                "tags": "not-a-list",
            },
            {
                "summary": "Entry with matching tag",
                "detail": "Detail",
                "impact": 0.8,
                "tags": ["pydantic"],
            },
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        # Only import entries with "pydantic" tag
        result = import_learnings(source_file, target, tags=["pydantic"])
        assert result["status"] == "ok"
        # Entry with non-list tags should be skipped (no intersection)
        assert result["skipped_filter"] == 1
        assert result["imported"] == 1

    def test_none_tags_in_source_entry_treated_as_no_tags(
        self, tmp_path: Path
    ) -> None:
        """None tags value is treated as empty list — gets filtered out."""
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        source_data = [
            {
                "summary": "Entry with null tags",
                "detail": "Detail",
                "impact": 0.8,
                "tags": None,
            },
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target, tags=["pydantic"])
        assert result["status"] == "ok"
        assert result["skipped_filter"] == 1
        assert result["imported"] == 0


class TestImportResyncEnvRestore:
    """Line 347 — TRW_PROJECT_ROOT restored after resync when previously set."""

    def test_env_restored_after_resync_when_previously_set(
        self, tmp_path: Path
    ) -> None:
        import os
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        source_data = [
            {
                "summary": "Entry to trigger resync",
                "detail": "Detail",
                "impact": 0.8,
            }
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        original_val = "pre_import_root"
        os.environ["TRW_PROJECT_ROOT"] = original_val
        try:
            with patch("trw_mcp.export.resync_learning_index"):
                result = import_learnings(source_file, target)
            assert result["status"] == "ok"
            assert result["imported"] == 1
            # Env var must be restored to original value
            assert os.environ.get("TRW_PROJECT_ROOT") == original_val
        finally:
            if os.environ.get("TRW_PROJECT_ROOT") == original_val:
                del os.environ["TRW_PROJECT_ROOT"]


class TestImportNonDictEntry:
    """Line 283 — non-dict entries in source list are silently skipped."""

    def test_non_dict_entry_skipped(self, tmp_path: Path) -> None:
        from trw_mcp.export import import_learnings

        target = _setup_project(tmp_path / "target")
        # Source list contains a mix of valid dicts and non-dicts
        source_data = [
            "this is a string not a dict",
            42,
            None,
            {
                "summary": "Valid entry",
                "detail": "Good",
                "impact": 0.8,
            },
        ]
        source_file = tmp_path / "export.json"
        source_file.write_text(json.dumps(source_data), encoding="utf-8")

        result = import_learnings(source_file, target)
        assert result["status"] == "ok"
        # Only the dict entry should be imported
        assert result["imported"] == 1
        assert result["total_source"] == 4


class TestExportAllScope:
    """Test the 'all' scope covers learnings + runs + analytics together."""

    def test_export_all_includes_all_sections(self, tmp_path: Path) -> None:
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="Combined test")

        with (
            patch("trw_mcp.export.scan_all_runs", return_value={"runs": []}),
            patch("trw_mcp.export.compute_reflection_quality", return_value=0.5),
        ):
            result = export_data(project, "all")

        assert result["status"] == "ok"
        assert "learnings" in result
        assert "runs" in result
        assert "analytics" in result

    def test_export_csv_for_all_scope_gives_json_not_csv(
        self, tmp_path: Path
    ) -> None:
        """CSV format only applies when scope='learnings'. scope='all' uses JSON."""
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"
        _make_entry(entries_dir, summary="CSV scope test")

        with (
            patch("trw_mcp.export.scan_all_runs", return_value={"runs": []}),
            patch("trw_mcp.export.compute_reflection_quality", return_value=0.5),
        ):
            result = export_data(project, "all", fmt="csv")

        # scope='all' with fmt='csv' -> result["learnings"] is a list, not CSV string
        assert "learnings" in result
        assert isinstance(result["learnings"], list)
        assert "learnings_csv" not in result


# ===========================================================================
# telemetry/sender.py — _http_post coverage gaps (lines 127-143)
# ===========================================================================


def _write_events(path: Path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")


def _make_sender(
    tmp_path: Path,
    *,
    platform_url: str = "https://api.example.com",
    batch_size: int = 100,
    max_retries: int = 1,
    backoff_base: float = 0.0,
) -> tuple[Any, Path]:
    from trw_mcp.telemetry.sender import BatchSender

    input_path = tmp_path / "logs" / "tool-telemetry.jsonl"
    urls = [platform_url] if platform_url else []
    sender = BatchSender(
        platform_urls=urls,
        input_path=input_path,
        batch_size=batch_size,
        max_retries=max_retries,
        backoff_base=backoff_base,
    )
    return sender, input_path


class TestHttpPost:
    """Lines 127-143 — _http_post real urllib branches."""

    def test_http_post_returns_true_on_2xx(self, tmp_path: Path) -> None:
        """Line 141 — 2xx status returns True."""
        sender, _ = _make_sender(tmp_path)

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"k": "v"}],
            )

        assert result is True

    def test_http_post_returns_false_on_3xx(self, tmp_path: Path) -> None:
        """Line 141 — non-2xx status (e.g. 301) returns False."""
        sender, _ = _make_sender(tmp_path)

        mock_response = MagicMock()
        mock_response.status = 301
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"k": "v"}],
            )

        assert result is False

    def test_http_post_returns_false_on_url_error(self, tmp_path: Path) -> None:
        """Line 142 — urllib.error.URLError is caught and returns False."""
        sender, _ = _make_sender(tmp_path)

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            result = sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"k": "v"}],
            )

        assert result is False

    def test_http_post_returns_false_on_http_error(self, tmp_path: Path) -> None:
        """Line 142 — urllib.error.HTTPError is caught and returns False."""
        sender, _ = _make_sender(tmp_path)

        http_err = urllib.error.HTTPError(
            url="https://api.example.com/v1/telemetry",
            code=500,
            msg="Internal Server Error",
            hdrs=MagicMock(),  # type: ignore[arg-type]
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            result = sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"k": "v"}],
            )

        assert result is False

    def test_http_post_returns_false_on_os_error(self, tmp_path: Path) -> None:
        """Line 142 — OSError is caught and returns False."""
        sender, _ = _make_sender(tmp_path)

        with patch(
            "urllib.request.urlopen",
            side_effect=OSError("network unreachable"),
        ):
            result = sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"k": "v"}],
            )

        assert result is False

    def test_http_post_sends_json_body(self, tmp_path: Path) -> None:
        """Lines 131-137 — request body is JSON-encoded with events key."""
        sender, _ = _make_sender(tmp_path)

        captured_data: list[bytes] = []

        def fake_urlopen(req: Any, timeout: int) -> Any:
            captured_data.append(req.data)
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            return mock_response

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"tool": "trw_learn", "duration_ms": 42}],
            )

        assert len(captured_data) == 1
        body = json.loads(captured_data[0].decode("utf-8"))
        assert "events" in body
        assert body["events"][0]["tool"] == "trw_learn"

    def test_http_post_integrated_with_send(self, tmp_path: Path) -> None:
        """Full integration: send() -> _send_batch() -> _http_post() via real urllib mock."""
        sender, input_path = _make_sender(tmp_path)
        _write_events(input_path, [{"event_type": "tool_invocation"}])

        mock_response = MagicMock()
        mock_response.status = 201
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = sender.send()

        assert result["sent"] == 1
        assert result["failed"] == 0

    def test_http_post_url_construction(self, tmp_path: Path) -> None:
        """Lines 131-133 — urllib.request.Request is constructed correctly."""
        sender, _ = _make_sender(tmp_path)

        captured_req: list[Any] = []

        def fake_urlopen(req: Any, timeout: int) -> Any:
            captured_req.append(req)
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            return mock_response

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            sender._http_post(
                "https://api.example.com/v1/telemetry",
                [{"k": "v"}],
            )

        req = captured_req[0]
        assert req.get_full_url() == "https://api.example.com/v1/telemetry"
        assert req.get_header("Content-type") == "application/json"
        assert req.get_method() == "POST"
