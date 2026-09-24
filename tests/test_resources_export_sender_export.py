from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._memory_store_fake import FakeMemoryStore
from tests._resources_export_sender_support import _setup_project, _writer
from tests._test_export_support import _store_entry


@pytest.fixture(autouse=True)
def _route_memory(fake_memory_store: FakeMemoryStore) -> FakeMemoryStore:
    """Export reads ``selected_store``; the fake is this checkout's store (PRD-CORE-280 e1)."""
    return fake_memory_store


class TestLoadProjectConfig:
    """Line 35-36 — _load_project_config reads existing config.yaml."""

    def test_loads_existing_config(self, tmp_path: Path) -> None:
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        trw_dir = project / ".trw"
        _writer.write_yaml(
            trw_dir / "config.yaml",
            {"framework_version": "v99.0_CUSTOM"},
        )
        _store_entry(trw_dir, summary="Entry with custom config")

        result = export_data(project, "learnings")
        assert result["status"] == "ok"
        meta = result.get("metadata")
        assert isinstance(meta, dict)
        assert meta.get("trw_version") == "v99.0_CUSTOM"


class TestCollectLearningsEdgeCases:
    """Lines 50, 55, 58-59, 66-68 — _collect_learnings edge cases (PRD-CORE-280 FR05: reads the store, not YAML).

    ``test_entries_not_a_directory_returns_empty`` and ``test_unreadable_entry_silently_skipped``
    pinned the retired YAML reader's directory/parse-error handling; ``_collect_learnings`` no
    longer touches ``learnings/entries/`` at all, so those behaviours are gone. The store-empty
    case has a direct equivalent in ``test_export_internals.py::TestCollectLearnings::test_returns_empty_when_store_is_empty``.
    """

    def test_since_filter_excludes_older_entries(self, tmp_path: Path) -> None:
        from trw_mcp.export import export_data
        from trw_mcp.state._store_selection import selected_store

        project = _setup_project(tmp_path)
        trw_dir = project / ".trw"
        _store_entry(trw_dir, summary="Old entry", entry_id="L-old")
        _store_entry(trw_dir, summary="New entry", entry_id="L-new")
        store, namespace = selected_store(trw_dir)
        old_entry = store.get("L-old")
        assert old_entry is not None
        store.apply_synced(
            namespace, old_entry.model_copy(update={"created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)})
        )

        result = export_data(project, "learnings", since="2026-02-01")
        learnings = result.get("learnings")
        assert isinstance(learnings, list)
        assert len(learnings) == 1
        assert learnings[0]["summary"] == "New entry"

    def test_since_filter_includes_entries_on_boundary(self, tmp_path: Path) -> None:
        from trw_mcp.export import export_data
        from trw_mcp.state._store_selection import selected_store

        project = _setup_project(tmp_path)
        trw_dir = project / ".trw"
        _store_entry(trw_dir, summary="Boundary entry", entry_id="L-boundary")
        store, namespace = selected_store(trw_dir)
        entry = store.get("L-boundary")
        assert entry is not None
        store.apply_synced(
            namespace, entry.model_copy(update={"created_at": datetime(2026, 2, 1, tzinfo=timezone.utc)})
        )

        result = export_data(project, "learnings", since="2026-02-01")
        learnings = result.get("learnings")
        assert isinstance(learnings, list)
        assert len(learnings) == 1


class TestCollectRunsEnvRestore:
    """Line 111 — TRW_PROJECT_ROOT env var restored when it was previously set."""

    def test_existing_env_var_restored_after_collect_runs(self, tmp_path: Path) -> None:
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)

        original_val = "original_project_root"
        os.environ["TRW_PROJECT_ROOT"] = original_val
        try:
            with patch("trw_mcp.export.scan_all_runs", return_value={"runs": []}):
                result = export_data(project, "runs")
            assert result["status"] == "ok"
            assert os.environ.get("TRW_PROJECT_ROOT") == original_val
        finally:
            if os.environ.get("TRW_PROJECT_ROOT") == original_val:
                del os.environ["TRW_PROJECT_ROOT"]


class TestCollectAnalyticsEdgeCases:
    """Lines 128-131, 139-140, 143, 151-155 — _collect_analytics branches."""

    def test_analytics_yaml_exists_and_included(self, tmp_path: Path) -> None:
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

    def test_analytics_yaml_read_error_silently_ignored(self, tmp_path: Path) -> None:
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        trw_dir = project / ".trw"
        analytics_file = trw_dir / "context" / "analytics.yaml"
        analytics_file.write_text("!!python/object:os.system [rm -rf /]", encoding="utf-8")

        with patch("trw_mcp.export.compute_reflection_quality", return_value=0.5):
            result = export_data(project, "analytics")

        assert result["status"] == "ok"

    def test_reflection_quality_exception_silently_ignored(self, tmp_path: Path) -> None:
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)

        with patch(
            "trw_mcp.export.compute_reflection_quality",
            side_effect=RuntimeError("boom"),
        ):
            result = export_data(project, "analytics")

        assert result["status"] == "ok"
        analytics = result.get("analytics", {})
        assert isinstance(analytics, dict)
        assert "reflection_quality" not in analytics

    def test_analytics_env_restored_when_previously_set(self, tmp_path: Path) -> None:
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)

        original_val = "pre_analytics_root"
        os.environ["TRW_PROJECT_ROOT"] = original_val
        try:
            with patch("trw_mcp.export.compute_reflection_quality", return_value=0.5):
                result = export_data(project, "analytics")
            assert result["status"] == "ok"
            assert os.environ.get("TRW_PROJECT_ROOT") == original_val
        finally:
            if os.environ.get("TRW_PROJECT_ROOT") == original_val:
                del os.environ["TRW_PROJECT_ROOT"]

    def test_cached_report_ceremony_aggregates_included(self, tmp_path: Path) -> None:
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

    def test_cached_report_read_error_silently_ignored(self, tmp_path: Path) -> None:
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


class TestExportAllScope:
    """Test the 'all' scope covers learnings + runs + analytics together."""

    def test_export_all_includes_all_sections(self, tmp_path: Path) -> None:
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        _store_entry(project / ".trw", summary="Combined test")

        with (
            patch("trw_mcp.export.scan_all_runs", return_value={"runs": []}),
            patch("trw_mcp.export.compute_reflection_quality", return_value=0.5),
        ):
            result = export_data(project, "all")

        assert result["status"] == "ok"
        assert "learnings" in result
        assert "runs" in result
        assert "analytics" in result

    def test_export_csv_for_all_scope_gives_json_not_csv(self, tmp_path: Path) -> None:
        from trw_mcp.export import export_data

        project = _setup_project(tmp_path)
        _store_entry(project / ".trw", summary="CSV scope test")

        with (
            patch("trw_mcp.export.scan_all_runs", return_value={"runs": []}),
            patch("trw_mcp.export.compute_reflection_quality", return_value=0.5),
        ):
            result = export_data(project, "all", fmt="csv")

        assert "learnings" in result
        assert isinstance(result["learnings"], list)
        assert "learnings_csv" not in result
