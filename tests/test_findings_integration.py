"""PRD-QUAL-008: Integration tests for findings.py — full tool pipelines.

Covers edge cases and error paths not exercised by test_findings.py:
- Query dedup between per-run and global sources
- Register with tags and component filtering
- Corrupted/malformed entry file handling
- Global registry upsert (replace existing) path
- Error handling in query tool when entries are malformed
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.finding import FindingEntry, FindingSeverity, FindingStatus
from trw_mcp.state.persistence import FileStateReader, FileStateWriter, model_to_dict


def _make_tool_project(tmp_path: Path) -> tuple[Path, Path]:
    """Create a project with a run directory for tool testing."""
    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    run_dir = project / "docs" / "test-task" / "runs" / "20260209T000000Z-test"
    meta_dir = run_dir / "meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "run.yaml").write_text(
        "run_id: test-run\ntask: test-task\n", encoding="utf-8",
    )
    prds_dir = project / "docs" / "requirements-aare-f" / "prds"
    prds_dir.mkdir(parents=True)
    return project, run_dir


def _get_findings_tools() -> dict[str, object]:
    """Create a fresh MCP server and return tool map for findings."""
    from fastmcp import FastMCP
    from trw_mcp.tools.findings import register_findings_tools

    srv = FastMCP("test-findings-integration")
    register_findings_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


# ---------------------------------------------------------------------------
# Register tool — edge cases
# ---------------------------------------------------------------------------


class TestRegisterEdgeCases:
    """Edge-case tests for trw_finding_register."""

    def test_register_with_tags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Tags are stored and queryable."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        result = tools["trw_finding_register"].fn(
            summary="Tagged finding",
            detail="This has tags.",
            severity="medium",
            tags=["auth", "security"],
            run_path=str(run_dir),
        )
        entry_path = Path(str(result["path"]))
        data = FileStateReader().read_yaml(entry_path)
        assert data["tags"] == ["auth", "security"]

    def test_register_with_component(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Component field is persisted."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        result = tools["trw_finding_register"].fn(
            summary="Component finding",
            detail="detail",
            severity="low",
            component="state/validation.py",
            run_path=str(run_dir),
        )
        entry_path = Path(str(result["path"]))
        data = FileStateReader().read_yaml(entry_path)
        assert data["component"] == "state/validation.py"

    def test_register_dedup_detection_via_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Registering a near-duplicate finding returns dedup_match."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        # Register original
        tools["trw_finding_register"].fn(
            summary="Authentication token refresh fails with 401 error",
            detail="Users report persistent 401 errors during token refresh flow.",
            severity="high",
            run_path=str(run_dir),
        )
        # Register near-duplicate
        result = tools["trw_finding_register"].fn(
            summary="Authentication token refresh fails with 401 errors",
            detail="Users report persistent 401 errors during the token refresh flow.",
            severity="high",
            run_path=str(run_dir),
        )
        assert result["dedup_match"] is not None

    def test_register_different_waves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Findings from different waves get independent IDs."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        r1 = tools["trw_finding_register"].fn(
            summary="Wave 1 finding",
            detail="detail",
            wave=1, shard=1,
            run_path=str(run_dir),
        )
        r2 = tools["trw_finding_register"].fn(
            summary="Wave 2 finding",
            detail="detail",
            wave=2, shard=1,
            run_path=str(run_dir),
        )
        assert r1["finding_id"] == "F-W1-S1-001"
        assert r2["finding_id"] == "F-W2-S1-001"

    def test_register_info_severity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Info severity is accepted and is not a PRD candidate."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        result = tools["trw_finding_register"].fn(
            summary="Informational note",
            detail="detail",
            severity="info",
            run_path=str(run_dir),
        )
        assert result["severity"] == "info"
        assert result["prd_candidate"] is False


# ---------------------------------------------------------------------------
# Query tool — filtering and edge cases
# ---------------------------------------------------------------------------


class TestQueryFiltering:
    """Query tool filter integration tests."""

    def test_query_filter_by_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Filter by status returns only matching findings."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        tools["trw_finding_register"].fn(
            summary="Open finding", detail="d", severity="medium",
            run_path=str(run_dir),
        )
        # All findings start as "open", query for non-existent status
        result = tools["trw_finding_query"].fn(
            status="resolved",
            run_path=str(run_dir),
            include_global=False,
        )
        assert result["total"] == 0

    def test_query_filter_by_tags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Filter by tags returns findings matching any tag."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        tools["trw_finding_register"].fn(
            summary="Security finding", detail="d", severity="high",
            tags=["security", "auth"],
            run_path=str(run_dir),
        )
        tools["trw_finding_register"].fn(
            summary="Performance finding", detail="d", severity="low",
            tags=["perf"],
            run_path=str(run_dir),
        )
        result = tools["trw_finding_query"].fn(
            tags=["security"],
            run_path=str(run_dir),
            include_global=False,
        )
        assert result["total"] == 1
        assert result["findings"][0]["tags"] == ["security", "auth"]

    def test_query_filter_by_component(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Filter by component uses substring matching."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        tools["trw_finding_register"].fn(
            summary="Validation finding", detail="d",
            component="state/validation.py",
            run_path=str(run_dir),
        )
        tools["trw_finding_register"].fn(
            summary="Learning finding", detail="d",
            component="tools/learning.py",
            run_path=str(run_dir),
        )
        result = tools["trw_finding_query"].fn(
            component="validation",
            run_path=str(run_dir),
            include_global=False,
        )
        assert result["total"] == 1

    def test_query_sorting_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Results are sorted by severity (critical first) then by ID."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        tools["trw_finding_register"].fn(
            summary="Low finding", detail="d", severity="low",
            run_path=str(run_dir),
        )
        tools["trw_finding_register"].fn(
            summary="Critical finding", detail="d", severity="critical",
            run_path=str(run_dir),
        )
        tools["trw_finding_register"].fn(
            summary="High finding", detail="d", severity="high",
            run_path=str(run_dir),
        )
        result = tools["trw_finding_query"].fn(
            run_path=str(run_dir),
            include_global=False,
        )
        severities = [str(f.get("severity")) for f in result["findings"]]
        assert severities[0] == "critical"
        assert severities[1] == "high"
        assert severities[2] == "low"

    def test_query_dedup_between_run_and_global(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Query deduplicates findings appearing in both per-run and global."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        # Register creates both per-run and global entries
        tools["trw_finding_register"].fn(
            summary="Dedup test", detail="d", severity="medium",
            run_path=str(run_dir),
        )
        result = tools["trw_finding_query"].fn(
            run_path=str(run_dir),
            include_global=True,
        )
        # Same finding should appear only once (per-run takes precedence)
        ids = [str(f.get("id")) for f in result["findings"]]
        assert len(ids) == len(set(ids)), "Duplicate findings should be deduplicated"

    def test_query_malformed_entry_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Malformed entry files are skipped without crashing the query."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        # Register a valid finding
        tools["trw_finding_register"].fn(
            summary="Valid finding", detail="d", severity="medium",
            run_path=str(run_dir),
        )
        # Write a malformed YAML file in the entries dir
        config = TRWConfig()
        entries_dir = run_dir / config.findings_dir / config.findings_entries_dir
        (entries_dir / "F-W99-S99-999.yaml").write_text(
            "this is not: [valid: yaml: {{", encoding="utf-8",
        )
        # Query should still return the valid finding
        result = tools["trw_finding_query"].fn(
            run_path=str(run_dir),
            include_global=False,
        )
        assert result["total"] >= 1

    def test_query_no_run_path_per_run_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Query gracefully handles unavailable per-run findings dir."""
        import trw_mcp.tools.findings as find_mod

        project = tmp_path / "empty_project"
        (project / ".trw").mkdir(parents=True)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        # No run dir exists, so per-run query should fail gracefully
        result = tools["trw_finding_query"].fn(
            run_path=str(tmp_path / "nonexistent"),
            include_global=False,
        )
        # Should return empty without error (StateError caught internally)
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# Global registry — upsert and update paths
# ---------------------------------------------------------------------------


class TestGlobalRegistryIntegration:
    """Tests for global registry upsert and update paths."""

    def test_upsert_replaces_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Re-registering same finding ID updates existing registry entry."""
        from trw_mcp.tools.findings import _upsert_global_registry

        project = tmp_path / "project"
        (project / ".trw").mkdir(parents=True)
        monkeypatch.setattr(
            "trw_mcp.tools.findings.resolve_project_root", lambda: project,
        )

        entry1 = FindingEntry(
            id="F-W1-S1-001", summary="Version 1",
            detail="Detail v1", severity=FindingSeverity.HIGH,
        )
        _upsert_global_registry(entry1, "run-1")

        entry2 = FindingEntry(
            id="F-W1-S1-001", summary="Version 2",
            detail="Detail v2", severity=FindingSeverity.CRITICAL,
        )
        _upsert_global_registry(entry2, "run-1")

        registry_path = project / ".trw" / "findings" / "registry.yaml"
        data = FileStateReader().read_yaml(registry_path)
        assert data["total_count"] == 1  # Not 2
        assert data["entries"][0]["summary"] == "Version 2"
        assert data["entries"][0]["severity"] == "critical"

    def test_registry_tracks_multiple_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Global registry tracks all run IDs in runs_indexed."""
        from trw_mcp.tools.findings import _upsert_global_registry

        project = tmp_path / "project"
        (project / ".trw").mkdir(parents=True)
        monkeypatch.setattr(
            "trw_mcp.tools.findings.resolve_project_root", lambda: project,
        )

        entry1 = FindingEntry(
            id="F-W1-S1-001", summary="Run 1",
            detail="Detail", severity=FindingSeverity.MEDIUM,
        )
        _upsert_global_registry(entry1, "run-alpha")

        entry2 = FindingEntry(
            id="F-W1-S2-001", summary="Run 2",
            detail="Detail", severity=FindingSeverity.LOW,
        )
        _upsert_global_registry(entry2, "run-beta")

        registry_path = project / ".trw" / "findings" / "registry.yaml"
        data = FileStateReader().read_yaml(registry_path)
        assert "run-alpha" in data["runs_indexed"]
        assert "run-beta" in data["runs_indexed"]

    def test_upsert_corrupt_existing_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Corrupt existing registry is gracefully replaced."""
        from trw_mcp.tools.findings import _upsert_global_registry

        project = tmp_path / "project"
        (project / ".trw" / "findings").mkdir(parents=True)
        monkeypatch.setattr(
            "trw_mcp.tools.findings.resolve_project_root", lambda: project,
        )

        # Write corrupt registry
        registry_path = project / ".trw" / "findings" / "registry.yaml"
        registry_path.write_text("{{bad yaml!", encoding="utf-8")

        entry = FindingEntry(
            id="F-W1-S1-001", summary="After corrupt",
            detail="Detail", severity=FindingSeverity.MEDIUM,
        )
        _upsert_global_registry(entry, "run-1")

        data = FileStateReader().read_yaml(registry_path)
        assert data["total_count"] == 1

    def test_update_registry_ref_corrupt_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_update_registry_ref handles corrupt registry gracefully."""
        from trw_mcp.tools.findings import _update_registry_ref

        project = tmp_path / "project"
        (project / ".trw" / "findings").mkdir(parents=True)
        monkeypatch.setattr(
            "trw_mcp.tools.findings.resolve_project_root", lambda: project,
        )

        registry_path = project / ".trw" / "findings" / "registry.yaml"
        registry_path.write_text("{{bad yaml!", encoding="utf-8")

        # Should not raise
        _update_registry_ref("F-W1-S1-001", "PRD-CORE-001", "acknowledged")


# ---------------------------------------------------------------------------
# Private helper edge cases — error handling paths
# ---------------------------------------------------------------------------


class TestPrivateHelperEdgeCases:
    """Tests for private helper error handling that improves coverage."""

    def test_generate_id_skips_non_numeric_suffix(self, tmp_path: Path) -> None:
        """Files with non-numeric suffixes after the prefix are skipped."""
        from trw_mcp.tools.findings import _generate_finding_id

        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        (entries_dir / "F-W1-S1-001.yaml").write_text("id: F-W1-S1-001")
        (entries_dir / "F-W1-S1-abc.yaml").write_text("id: F-W1-S1-abc")

        fid = _generate_finding_id(1, 1, entries_dir)
        assert fid == "F-W1-S1-002"

    def test_check_dedup_entries_dir_missing(self, tmp_path: Path) -> None:
        """_check_dedup returns None when entries dir does not exist."""
        from trw_mcp.tools.findings import _check_dedup

        findings_dir = tmp_path / "findings"
        findings_dir.mkdir()
        # entries subdir does NOT exist
        result = _check_dedup("summary", "detail", findings_dir)
        assert result is None

    def test_check_dedup_corrupt_yaml_skipped(self, tmp_path: Path) -> None:
        """_check_dedup skips corrupt YAML files."""
        from trw_mcp.tools.findings import _check_dedup

        findings_dir = tmp_path / "findings"
        entries_dir = findings_dir / "entries"
        entries_dir.mkdir(parents=True)
        (entries_dir / "F-W1-S1-001.yaml").write_text("{{bad yaml!", encoding="utf-8")

        result = _check_dedup("test", "detail", findings_dir)
        assert result is None

    def test_read_run_id_corrupt_yaml(self, tmp_path: Path) -> None:
        """_read_run_id returns empty string on corrupt run.yaml."""
        from trw_mcp.tools.findings import _read_run_id

        meta = tmp_path / "meta"
        meta.mkdir()
        (meta / "run.yaml").write_text("{{bad yaml", encoding="utf-8")
        assert _read_run_id(tmp_path) == ""

    def test_update_run_index_corrupt_existing(self, tmp_path: Path) -> None:
        """_update_run_index overwrites corrupt existing index."""
        from trw_mcp.tools.findings import _update_run_index

        findings_dir = tmp_path / "findings"
        findings_dir.mkdir()
        (findings_dir / "index.yaml").write_text("{{bad yaml!", encoding="utf-8")

        entry = FindingEntry(
            id="F-W1-S1-001", summary="Test", detail="Detail",
        )
        _update_run_index(findings_dir, entry)

        data = FileStateReader().read_yaml(findings_dir / "index.yaml")
        assert data["total_count"] == 1

    def test_query_corrupt_global_registry_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Query logs a warning for corrupt global registry but doesn't crash."""
        import trw_mcp.tools.findings as find_mod

        project = tmp_path / "project"
        (project / ".trw" / "findings").mkdir(parents=True)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        # Corrupt registry
        registry = project / ".trw" / "findings" / "registry.yaml"
        registry.write_text("{{bad yaml!", encoding="utf-8")

        tools = _get_findings_tools()
        result = tools["trw_finding_query"].fn(include_global=True)
        assert result["total"] == 0
