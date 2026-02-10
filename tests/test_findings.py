"""Tests for PRD-CORE-010: Structured Findings Pipeline.

Sub-Phase 1: Models, auto-ID, dedup, register tool.
Sub-Phase 2: Finding-to-PRD conversion, traceability.
Sub-Phase 3: Query tool, global registry.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.finding import (
    FindingEntry,
    FindingRef,
    FindingSeverity,
    FindingStatus,
    FindingsIndex,
    FindingsRegistry,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter, model_to_dict
from trw_mcp.tools.findings import (
    _check_dedup,
    _generate_finding_id,
    _jaccard_similarity,
    _matches_filters,
)


# ---------------------------------------------------------------------------
# Sub-Phase 1: Model Tests (T01-T10)
# ---------------------------------------------------------------------------


class TestFindingSeverityEnum:
    """Test FindingSeverity enum serialization."""

    def test_severity_values_lowercase(self) -> None:
        """T01: Enum values serialize to lowercase strings."""
        assert FindingSeverity.CRITICAL.value == "critical"
        assert FindingSeverity.HIGH.value == "high"
        assert FindingSeverity.MEDIUM.value == "medium"
        assert FindingSeverity.LOW.value == "low"
        assert FindingSeverity.INFO.value == "info"

    def test_severity_from_string(self) -> None:
        """Enum can be constructed from lowercase string."""
        assert FindingSeverity("critical") == FindingSeverity.CRITICAL


class TestFindingStatusEnum:
    """Test FindingStatus enum serialization."""

    def test_status_values_lowercase(self) -> None:
        """T02: Enum values serialize to lowercase strings."""
        assert FindingStatus.OPEN.value == "open"
        assert FindingStatus.ACKNOWLEDGED.value == "acknowledged"
        assert FindingStatus.IN_PROGRESS.value == "in-progress"
        assert FindingStatus.RESOLVED.value == "resolved"
        assert FindingStatus.WONT_FIX.value == "wont-fix"


class TestFindingEntry:
    """Test FindingEntry model validation and serialization."""

    def test_validates_required_fields(self) -> None:
        """T03: All required fields are validated."""
        entry = FindingEntry(
            id="F-W1-S1-001",
            summary="Test finding",
            detail="Detailed description",
        )
        assert entry.id == "F-W1-S1-001"
        assert entry.summary == "Test finding"
        assert entry.detail == "Detailed description"

    def test_rejects_invalid_severity(self) -> None:
        """T04: Invalid severity raises validation error."""
        with pytest.raises(Exception):
            FindingEntry(
                id="F-W1-S1-001",
                summary="Test",
                detail="Detail",
                severity="not_valid",  # type: ignore[arg-type]
            )

    def test_rejects_invalid_status(self) -> None:
        """T05: Invalid status raises validation error."""
        with pytest.raises(Exception):
            FindingEntry(
                id="F-W1-S1-001",
                summary="Test",
                detail="Detail",
                status="invalid_status",  # type: ignore[arg-type]
            )

    def test_round_trip_yaml(self, tmp_path: Path) -> None:
        """T06: YAML serialization round-trip preserves all fields."""
        writer = FileStateWriter()
        reader = FileStateReader()

        entry = FindingEntry(
            id="F-W1-S1-001",
            summary="Test finding",
            detail="Detailed description",
            severity=FindingSeverity.HIGH,
            status=FindingStatus.OPEN,
            component="validation",
            tags=["testing", "quality"],
            source_shard="S1",
            source_wave=1,
            run_id="test-run-123",
            target_prd=None,
            prd_candidate=True,
        )

        path = tmp_path / "entry.yaml"
        writer.write_yaml(path, model_to_dict(entry))
        loaded = reader.read_yaml(path)

        assert loaded["id"] == "F-W1-S1-001"
        assert loaded["summary"] == "Test finding"
        assert loaded["severity"] == "high"
        assert loaded["status"] == "open"
        assert loaded["tags"] == ["testing", "quality"]
        assert loaded["prd_candidate"] is True
        assert loaded["target_prd"] is None

    def test_default_values(self) -> None:
        """T10: Default values are correct."""
        entry = FindingEntry(
            id="F-W1-S1-001",
            summary="Test",
            detail="Detail",
        )
        assert entry.status == "open"
        assert entry.prd_candidate is False
        assert entry.target_prd is None
        assert entry.severity == "medium"
        assert entry.component == ""
        assert entry.tags == []


class TestFindingRef:
    """Test FindingRef lightweight reference model."""

    def test_ref_has_subset_fields(self) -> None:
        """T07: FindingRef contains only lightweight fields."""
        ref = FindingRef(
            id="F-W1-S1-001",
            summary="Test finding",
            severity=FindingSeverity.HIGH,
            status=FindingStatus.OPEN,
            run_id="test-run",
        )
        assert ref.id == "F-W1-S1-001"
        assert ref.summary == "Test finding"
        assert ref.target_prd is None


class TestFindingsIndex:
    """Test FindingsIndex model."""

    def test_validates_entries_list(self) -> None:
        """T08: FindingsIndex validates entries and total_count."""
        index = FindingsIndex(
            entries=[],
            total_count=0,
        )
        assert index.entries == []
        assert index.total_count == 0

    def test_with_entries(self) -> None:
        """FindingsIndex with entries."""
        entry = FindingEntry(
            id="F-W1-S1-001",
            summary="Test",
            detail="Detail",
        )
        index = FindingsIndex(
            entries=[entry],
            total_count=1,
        )
        assert len(index.entries) == 1


class TestFindingsRegistry:
    """Test FindingsRegistry model."""

    def test_validates_structure(self) -> None:
        """T09: FindingsRegistry validates entries and runs_indexed."""
        registry = FindingsRegistry(
            entries=[],
            total_count=0,
            runs_indexed=["run-1", "run-2"],
        )
        assert registry.total_count == 0
        assert len(registry.runs_indexed) == 2


# ---------------------------------------------------------------------------
# Sub-Phase 1: Deduplication Tests (T11-T14)
# ---------------------------------------------------------------------------


class TestJaccardSimilarity:
    """Test token-based Jaccard similarity computation."""

    def test_identical_texts(self) -> None:
        """T11: Identical texts return 1.0."""
        assert _jaccard_similarity("hello world", "hello world") == 1.0

    def test_completely_disjoint(self) -> None:
        """T12: Completely different texts return 0.0."""
        assert _jaccard_similarity("alpha beta", "gamma delta") == 0.0

    def test_partial_overlap(self) -> None:
        """Partial overlap returns intermediate value."""
        score = _jaccard_similarity("the quick brown fox", "the lazy brown dog")
        assert 0.0 < score < 1.0

    def test_empty_texts_return_one(self) -> None:
        """Two empty texts are 'identical'."""
        assert _jaccard_similarity("", "") == 1.0

    def test_one_empty_returns_zero(self) -> None:
        """One empty text returns 0.0."""
        assert _jaccard_similarity("hello", "") == 0.0


class TestDedupDetection:
    """Test dedup detection with threshold."""

    def test_above_threshold_matches(self, tmp_path: Path) -> None:
        """T13: Jaccard >= 0.6 triggers dedup match."""
        writer = FileStateWriter()
        findings_dir = tmp_path / "findings"
        entries_dir = findings_dir / "entries"
        entries_dir.mkdir(parents=True)

        # Write an existing finding
        existing = {
            "id": "F-W1-S1-001",
            "summary": "The validation engine needs better scoring",
            "detail": "Current scoring is too simple and binary",
        }
        writer.write_yaml(entries_dir / "F-W1-S1-001.yaml", existing)

        # Check dedup with similar text
        match = _check_dedup(
            "The validation engine needs improved scoring",
            "Current scoring is too simple and binary",
            findings_dir,
        )
        assert match == "F-W1-S1-001"

    def test_below_threshold_no_match(self, tmp_path: Path) -> None:
        """T14: Jaccard < 0.6 does not trigger dedup."""
        writer = FileStateWriter()
        findings_dir = tmp_path / "findings"
        entries_dir = findings_dir / "entries"
        entries_dir.mkdir(parents=True)

        existing = {
            "id": "F-W1-S1-001",
            "summary": "Authentication needs OAuth2 support",
            "detail": "Currently only basic auth is supported",
        }
        writer.write_yaml(entries_dir / "F-W1-S1-001.yaml", existing)

        # Check dedup with completely different text
        match = _check_dedup(
            "Database migrations should use Alembic",
            "The ORM needs proper migration support for schema changes",
            findings_dir,
        )
        assert match is None


# ---------------------------------------------------------------------------
# Sub-Phase 1: Auto-ID Generation Tests (T15-T17)
# ---------------------------------------------------------------------------


class TestAutoIdGeneration:
    """Test finding ID auto-generation."""

    def test_first_finding(self, tmp_path: Path) -> None:
        """T15: First finding for W1-S1 generates F-W1-S1-001."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        fid = _generate_finding_id(1, 1, entries_dir)
        assert fid == "F-W1-S1-001"

    def test_second_finding(self, tmp_path: Path) -> None:
        """T16: Second finding auto-increments to 002."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        (entries_dir / "F-W1-S1-001.yaml").write_text("id: F-W1-S1-001", encoding="utf-8")
        fid = _generate_finding_id(1, 1, entries_dir)
        assert fid == "F-W1-S1-002"

    def test_different_shard(self, tmp_path: Path) -> None:
        """T17: Different shard gets independent sequence."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        (entries_dir / "F-W1-S1-001.yaml").write_text("id: F-W1-S1-001", encoding="utf-8")
        fid = _generate_finding_id(2, 3, entries_dir)
        assert fid == "F-W2-S3-001"


# ---------------------------------------------------------------------------
# Sub-Phase 1: Register Tool Integration Tests (T18-T22)
# ---------------------------------------------------------------------------


@pytest.fixture()
def findings_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a project structure for findings tool tests."""
    project_root = tmp_path / "project"
    trw_dir = project_root / ".trw"
    trw_dir.mkdir(parents=True)

    # Create run directory
    run_path = tmp_path / "run"
    meta = run_path / "meta"
    meta.mkdir(parents=True)
    reports = run_path / "reports"
    reports.mkdir(parents=True)

    from ruamel.yaml import YAML

    yaml = YAML()
    state = {"run_id": "test-run-123", "task": "test"}
    yaml.dump(state, meta / "run.yaml")

    # Monkeypatch project root — patch both source module AND the
    # already-imported reference in findings.py (module-level import).
    monkeypatch.setattr(
        "trw_mcp.state._paths.resolve_project_root",
        lambda: project_root,
    )
    monkeypatch.setattr(
        "trw_mcp.tools.findings.resolve_project_root",
        lambda: project_root,
    )

    return run_path


class TestRegisterTool:
    """Integration tests for trw_finding_register."""

    def test_register_creates_entry_file(self, findings_project: Path) -> None:
        """T18: Register creates FindingEntry YAML in findings/entries/."""
        # Manually call the internal functions
        from trw_mcp.tools.findings import (
            _config,
            _generate_finding_id,
            _writer,
        )

        findings_dir = findings_project / _config.findings_dir
        entries_dir = findings_dir / _config.findings_entries_dir
        _writer.ensure_dir(entries_dir)

        fid = _generate_finding_id(1, 1, entries_dir)
        entry = FindingEntry(
            id=fid,
            summary="Test finding",
            detail="Test detail",
            severity=FindingSeverity.MEDIUM,
        )
        entry_path = entries_dir / f"{fid}.yaml"
        _writer.write_yaml(entry_path, model_to_dict(entry))

        assert entry_path.exists()
        reader = FileStateReader()
        loaded = reader.read_yaml(entry_path)
        assert loaded["id"] == "F-W1-S1-001"

    def test_register_updates_index(self, findings_project: Path) -> None:
        """T19: Register updates per-run FindingsIndex."""
        from trw_mcp.tools.findings import (
            _config,
            _update_run_index,
            _writer,
        )

        findings_dir = findings_project / _config.findings_dir
        entries_dir = findings_dir / _config.findings_entries_dir
        _writer.ensure_dir(entries_dir)

        entry = FindingEntry(
            id="F-W1-S1-001",
            summary="Test finding",
            detail="Test detail",
        )
        _update_run_index(findings_dir, entry)

        index_path = findings_dir / "index.yaml"
        assert index_path.exists()

        reader = FileStateReader()
        index_data = reader.read_yaml(index_path)
        assert index_data["total_count"] == 1

    def test_register_upserts_global_registry(self, findings_project: Path) -> None:
        """T20: Register upserts FindingRef into global registry."""
        from trw_mcp.tools.findings import (
            _config,
            _upsert_global_registry,
        )

        entry = FindingEntry(
            id="F-W1-S1-001",
            summary="Test finding",
            detail="Test detail",
            severity=FindingSeverity.HIGH,
        )
        _upsert_global_registry(entry, "test-run-123")

        registry_path = (
            findings_project.parent / "project" / ".trw" / "findings" / "registry.yaml"
        )
        assert registry_path.exists()

        reader = FileStateReader()
        reg_data = reader.read_yaml(registry_path)
        assert reg_data["total_count"] == 1
        entries = reg_data.get("entries", [])
        assert len(entries) == 1
        assert entries[0]["id"] == "F-W1-S1-001"

    def test_critical_severity_flags_prd_candidate(self) -> None:
        """T21: Critical severity sets prd_candidate=True."""
        sev = FindingSeverity.CRITICAL
        prd_candidate = sev in (FindingSeverity.CRITICAL, FindingSeverity.HIGH)
        assert prd_candidate is True

    def test_medium_severity_not_prd_candidate(self) -> None:
        """Medium severity does not flag prd_candidate."""
        sev = FindingSeverity.MEDIUM
        prd_candidate = sev in (FindingSeverity.CRITICAL, FindingSeverity.HIGH)
        assert prd_candidate is False


# ---------------------------------------------------------------------------
# Sub-Phase 1: Config Tests
# ---------------------------------------------------------------------------


class TestFindingsConfig:
    """Test new TRWConfig fields for findings pipeline."""

    def test_dedup_threshold_default(self) -> None:
        """Default dedup threshold is 0.6."""
        config = TRWConfig()
        assert config.finding_dedup_threshold == 0.6

    def test_findings_dir_default(self) -> None:
        """Default findings dir."""
        config = TRWConfig()
        assert config.findings_dir == "findings"

    def test_findings_entries_dir_default(self) -> None:
        """Default findings entries dir."""
        config = TRWConfig()
        assert config.findings_entries_dir == "entries"

    def test_registry_file_default(self) -> None:
        """Default registry file."""
        config = TRWConfig()
        assert config.findings_registry_file == "registry.yaml"

    def test_dedup_threshold_override(self) -> None:
        """Custom dedup threshold."""
        config = TRWConfig(finding_dedup_threshold=0.8)
        assert config.finding_dedup_threshold == 0.8


# ---------------------------------------------------------------------------
# Sub-Phase 1: Filter Tests
# ---------------------------------------------------------------------------


class TestMatchesFilters:
    """Test finding filter matching."""

    def test_severity_filter(self) -> None:
        """Severity filter matches exact."""
        data: dict[str, object] = {"severity": "critical", "status": "open"}
        assert _matches_filters(data, severity="critical", status=None, tags=None, component=None)
        assert not _matches_filters(data, severity="low", status=None, tags=None, component=None)

    def test_status_filter(self) -> None:
        """Status filter matches exact."""
        data: dict[str, object] = {"severity": "medium", "status": "resolved"}
        assert _matches_filters(data, severity=None, status="resolved", tags=None, component=None)
        assert not _matches_filters(data, severity=None, status="open", tags=None, component=None)

    def test_tags_filter_any_match(self) -> None:
        """Tags filter matches any tag."""
        data: dict[str, object] = {"tags": ["testing", "quality"]}
        assert _matches_filters(data, severity=None, status=None, tags=["testing"], component=None)
        assert _matches_filters(data, severity=None, status=None, tags=["quality", "other"], component=None)
        assert not _matches_filters(data, severity=None, status=None, tags=["unrelated"], component=None)

    def test_component_filter_substring(self) -> None:
        """Component filter matches substring."""
        data: dict[str, object] = {"component": "state/validation.py"}
        assert _matches_filters(data, severity=None, status=None, tags=None, component="validation")
        assert not _matches_filters(data, severity=None, status=None, tags=None, component="learning")

    def test_no_filters_matches_all(self) -> None:
        """No filters matches everything."""
        data: dict[str, object] = {"severity": "low", "status": "open"}
        assert _matches_filters(data, severity=None, status=None, tags=None, component=None)


# ---------------------------------------------------------------------------
# Sub-Phase 2: Private Helper Tests
# ---------------------------------------------------------------------------


class TestReadRunId:
    """Test _read_run_id helper."""

    def test_reads_run_id(self, tmp_path: Path) -> None:
        """Reads run_id from run.yaml."""
        from trw_mcp.tools.findings import _read_run_id

        meta = tmp_path / "meta"
        meta.mkdir()
        from ruamel.yaml import YAML

        YAML().dump({"run_id": "abc-123"}, meta / "run.yaml")
        assert _read_run_id(tmp_path) == "abc-123"

    def test_returns_empty_when_missing(self, tmp_path: Path) -> None:
        """Returns empty string when run.yaml is missing."""
        from trw_mcp.tools.findings import _read_run_id

        assert _read_run_id(tmp_path) == ""


class TestUpdateRegistryRef:
    """Test _update_registry_ref helper."""

    def test_updates_existing_ref(self, findings_project: Path) -> None:
        """Updates target_prd and status on existing registry entry."""
        from trw_mcp.tools.findings import (
            _update_registry_ref,
            _upsert_global_registry,
        )

        entry = FindingEntry(
            id="F-W1-S1-001", summary="Test", detail="Detail",
            severity=FindingSeverity.MEDIUM,
        )
        _upsert_global_registry(entry, "run-1")

        _update_registry_ref("F-W1-S1-001", "PRD-CORE-099", "acknowledged")

        registry_path = (
            findings_project.parent / "project" / ".trw" / "findings" / "registry.yaml"
        )
        data = FileStateReader().read_yaml(registry_path)
        ref = data["entries"][0]
        assert ref["target_prd"] == "PRD-CORE-099"
        assert ref["status"] == "acknowledged"

    def test_no_registry_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Does nothing when no registry file exists."""
        from trw_mcp.tools.findings import _update_registry_ref

        monkeypatch.setattr(
            "trw_mcp.tools.findings.resolve_project_root", lambda: tmp_path,
        )
        # Should not raise
        _update_registry_ref("F-W1-S1-001", "PRD-CORE-001", "open")


class TestCreatePrdFromFinding:
    """Test _create_prd_from_finding helper."""

    def test_creates_prd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Creates a PRD file from finding text."""
        from trw_mcp.tools.findings import _create_prd_from_finding

        project = tmp_path / "project"
        prds_dir = project / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (project / ".trw").mkdir(parents=True)

        monkeypatch.setattr(
            "trw_mcp.tools.findings.resolve_project_root", lambda: project,
        )

        result = _create_prd_from_finding(
            "Authentication fails on token refresh\n\nUsers report 401 errors.",
            "FIX", "P1",
        )

        assert "prd_id" in result
        assert str(result["prd_id"]).startswith("PRD-FIX-")
        assert result.get("output_path")
        prd_path = Path(str(result["output_path"]))
        assert prd_path.exists()
        content = prd_path.read_text()
        assert "Authentication" in content

    def test_invalid_priority_defaults_p1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Invalid priority falls back to P1."""
        from trw_mcp.tools.findings import _create_prd_from_finding

        project = tmp_path / "project"
        prds_dir = project / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (project / ".trw").mkdir(parents=True)

        monkeypatch.setattr(
            "trw_mcp.tools.findings.resolve_project_root", lambda: project,
        )

        result = _create_prd_from_finding("Test finding", "CORE", "INVALID")
        assert "prd_id" in result


# ---------------------------------------------------------------------------
# Sub-Phase 2: Finding-to-PRD Integration Tests (T23-T26)
# ---------------------------------------------------------------------------


class TestFindingToPrd:
    """Integration tests for finding-to-PRD conversion via internal helpers."""

    def test_finding_to_prd_updates_entry(
        self, findings_project: Path,
    ) -> None:
        """T23: Converting finding updates entry status to acknowledged."""
        from trw_mcp.tools.findings import (
            _config,
            _create_prd_from_finding,
            _reader,
            _update_registry_ref,
            _upsert_global_registry,
            _writer,
        )

        project = findings_project.parent / "project"
        prds_dir = project / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)

        # Create a finding entry
        findings_dir = findings_project / _config.findings_dir
        entries_dir = findings_dir / _config.findings_entries_dir
        _writer.ensure_dir(entries_dir)

        entry = FindingEntry(
            id="F-W1-S1-001", summary="Test finding for PRD",
            detail="Detailed analysis of the issue.",
            severity=FindingSeverity.HIGH,
        )
        entry_data = model_to_dict(entry)
        _writer.write_yaml(entries_dir / "F-W1-S1-001.yaml", entry_data)

        # Upsert into global registry first (like the register tool would)
        _upsert_global_registry(entry, "test-run-123")

        # Simulate what trw_finding_to_prd does:
        # 1. Read finding
        read_data = _reader.read_yaml(entries_dir / "F-W1-S1-001.yaml")
        loaded = FindingEntry.model_validate(read_data)

        # 2. Create PRD
        input_text = f"{loaded.summary}\n\n{loaded.detail}"
        prd_result = _create_prd_from_finding(input_text, "FIX", "P1")
        prd_id = str(prd_result.get("prd_id", ""))
        assert prd_id.startswith("PRD-FIX-")

        # 3. Update finding entry
        read_data["target_prd"] = prd_id
        read_data["status"] = "acknowledged"
        _writer.write_yaml(entries_dir / "F-W1-S1-001.yaml", read_data)

        # 4. Update global registry
        _update_registry_ref("F-W1-S1-001", prd_id, "acknowledged")

        # Verify entry was updated
        updated_data = FileStateReader().read_yaml(entries_dir / "F-W1-S1-001.yaml")
        assert updated_data["status"] == "acknowledged"
        assert updated_data["target_prd"] == prd_id

    def test_finding_to_prd_missing_finding(
        self, findings_project: Path,
    ) -> None:
        """T24: Missing finding file should be detectable."""
        from trw_mcp.tools.findings import _config

        findings_dir = findings_project / _config.findings_dir
        entries_dir = findings_dir / _config.findings_entries_dir
        entry_path = entries_dir / "F-W99-S99-999.yaml"
        assert not entry_path.exists()


# ---------------------------------------------------------------------------
# Sub-Phase 3: Query Integration Tests (T27-T31)
# ---------------------------------------------------------------------------


class TestFindingQueryIntegration:
    """Integration tests for finding query logic."""

    def test_query_per_run_findings(
        self, findings_project: Path,
    ) -> None:
        """T27: Query reads and filters per-run findings."""
        from trw_mcp.tools.findings import _writer, _config, _reader

        findings_dir = findings_project / _config.findings_dir
        entries_dir = findings_dir / _config.findings_entries_dir
        _writer.ensure_dir(entries_dir)

        for i, sev in enumerate(["critical", "low"], start=1):
            entry = FindingEntry(
                id=f"F-W1-S1-{i:03d}", summary=f"Finding {i}",
                detail=f"Detail {i}", severity=FindingSeverity(sev),
                status=FindingStatus.OPEN, tags=["test"],
            )
            _writer.write_yaml(entries_dir / f"{entry.id}.yaml", model_to_dict(entry))

        # Read and filter (what trw_finding_query does internally)
        results: list[dict[str, object]] = []
        for entry_file in sorted(entries_dir.glob("*.yaml")):
            data = _reader.read_yaml(entry_file)
            if _matches_filters(data, severity=None, status=None, tags=None, component=None):
                results.append(data)

        assert len(results) == 2

    def test_query_severity_filter(
        self, findings_project: Path,
    ) -> None:
        """T28: Query filters by severity correctly."""
        from trw_mcp.tools.findings import _writer, _config, _reader

        findings_dir = findings_project / _config.findings_dir
        entries_dir = findings_dir / _config.findings_entries_dir
        _writer.ensure_dir(entries_dir)

        for sev in ["critical", "low"]:
            entry = FindingEntry(
                id=f"F-sev-{sev}", summary=f"{sev} finding",
                detail="Detail", severity=FindingSeverity(sev),
            )
            _writer.write_yaml(entries_dir / f"{entry.id}.yaml", model_to_dict(entry))

        results: list[dict[str, object]] = []
        for entry_file in sorted(entries_dir.glob("*.yaml")):
            data = _reader.read_yaml(entry_file)
            if _matches_filters(data, severity="critical", status=None, tags=None, component=None):
                results.append(data)

        assert len(results) == 1
        assert results[0]["severity"] == "critical"

    def test_query_global_registry(
        self, findings_project: Path,
    ) -> None:
        """T29: Query reads from global registry."""
        from trw_mcp.tools.findings import (
            _get_registry_path,
            _reader,
            _upsert_global_registry,
        )

        # Put a finding in global registry
        entry = FindingEntry(
            id="F-GLOBAL-001", summary="Global finding",
            detail="Detail", severity=FindingSeverity.HIGH,
        )
        _upsert_global_registry(entry, "global-run")

        # Read and verify
        registry_path = _get_registry_path()
        assert registry_path.exists()
        reg_data = _reader.read_yaml(registry_path)
        assert reg_data["total_count"] >= 1

    def test_query_sorting_critical_first(self) -> None:
        """T30: Results sorted by severity (critical first)."""
        severity_order = {
            "critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4,
        }
        results: list[dict[str, object]] = [
            {"id": "F-3", "severity": "low"},
            {"id": "F-1", "severity": "critical"},
            {"id": "F-2", "severity": "high"},
        ]
        results.sort(
            key=lambda r: (
                severity_order.get(str(r.get("severity", "medium")), 2),
                str(r.get("id", "")),
            ),
        )
        assert [str(r["id"]) for r in results] == ["F-1", "F-2", "F-3"]


# ---------------------------------------------------------------------------
# Sub-Phase 3: Register Full Pipeline Tests
# ---------------------------------------------------------------------------


class TestRegisterFullPipeline:
    """Full pipeline tests for finding registration."""

    def test_full_register_pipeline(
        self, findings_project: Path,
    ) -> None:
        """T31: Full register pipeline — validate, generate ID, write, index, registry."""
        from trw_mcp.tools.findings import (
            _config,
            _generate_finding_id,
            _check_dedup,
            _read_run_id,
            _update_run_index,
            _upsert_global_registry,
            _writer,
        )

        # Simulate the register tool flow
        severity = "high"
        sev = FindingSeverity(severity.lower())
        assert sev == FindingSeverity.HIGH

        findings_dir = findings_project / _config.findings_dir
        entries_dir = findings_dir / _config.findings_entries_dir
        _writer.ensure_dir(entries_dir)

        finding_id = _generate_finding_id(1, 1, entries_dir)
        assert finding_id == "F-W1-S1-001"

        dedup_match = _check_dedup("New finding", "Detail", findings_dir)
        assert dedup_match is None

        prd_candidate = sev in (FindingSeverity.CRITICAL, FindingSeverity.HIGH)
        assert prd_candidate is True

        run_id = _read_run_id(findings_project)
        assert run_id == "test-run-123"

        entry = FindingEntry(
            id=finding_id, summary="New finding", detail="Detail",
            severity=sev, run_id=run_id, prd_candidate=prd_candidate,
        )
        _writer.write_yaml(entries_dir / f"{finding_id}.yaml", model_to_dict(entry))
        _update_run_index(findings_dir, entry)
        _upsert_global_registry(entry, run_id)

        # Verify all artifacts
        assert (entries_dir / "F-W1-S1-001.yaml").exists()
        assert (findings_dir / "index.yaml").exists()

        registry_path = (
            findings_project.parent / "project" / ".trw" / "findings" / "registry.yaml"
        )
        assert registry_path.exists()

    def test_invalid_severity_detection(self) -> None:
        """T32: Invalid severity is detected before registration."""
        from trw_mcp.exceptions import ValidationError

        severity = "INVALID_SEV"
        with pytest.raises(ValueError):
            FindingSeverity(severity.lower())

    def test_dedup_detection_in_pipeline(
        self, findings_project: Path,
    ) -> None:
        """T33: Dedup detection works in full pipeline context."""
        from trw_mcp.tools.findings import _writer, _config

        findings_dir = findings_project / _config.findings_dir
        entries_dir = findings_dir / _config.findings_entries_dir
        _writer.ensure_dir(entries_dir)

        # Create existing finding
        existing = FindingEntry(
            id="F-W1-S1-001",
            summary="Authentication token refresh fails with 401",
            detail="Users report persistent 401 errors during token refresh flow.",
            severity=FindingSeverity.HIGH,
        )
        _writer.write_yaml(entries_dir / "F-W1-S1-001.yaml", model_to_dict(existing))

        # Check dedup with very similar text
        match = _check_dedup(
            "Authentication token refresh fails with 401 error",
            "Users report persistent 401 errors during the token refresh flow.",
            findings_dir,
        )
        assert match == "F-W1-S1-001"

    def test_update_run_index_appends(
        self, findings_project: Path,
    ) -> None:
        """Index grows as entries are added."""
        from trw_mcp.tools.findings import _config, _update_run_index, _writer, _reader

        findings_dir = findings_project / _config.findings_dir
        _writer.ensure_dir(findings_dir)

        for i in range(1, 4):
            entry = FindingEntry(
                id=f"F-W1-S1-{i:03d}", summary=f"Finding {i}",
                detail=f"Detail {i}", severity=FindingSeverity.MEDIUM,
            )
            _update_run_index(findings_dir, entry)

        index = _reader.read_yaml(findings_dir / "index.yaml")
        assert index["total_count"] == 3
        assert len(index["entries"]) == 3


# ---------------------------------------------------------------------------
# FR09: Traceability Check — Finding Coverage Integration
# ---------------------------------------------------------------------------


class TestTraceabilityFindingCoverage:
    """Test FR09: trw_traceability_check finding coverage analysis."""

    def test_unlinked_critical_findings_flagged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR09: Critical findings without target_prd appear in unlinked list."""
        from trw_mcp.tools.findings import _upsert_global_registry

        project = tmp_path / "project"
        trw_dir = project / ".trw"
        trw_dir.mkdir(parents=True)

        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root", lambda: project,
        )
        monkeypatch.setattr(
            "trw_mcp.tools.findings.resolve_project_root", lambda: project,
        )
        monkeypatch.setattr(
            "trw_mcp.tools.requirements.resolve_project_root", lambda: project,
        )

        # Register a critical finding without target_prd
        entry = FindingEntry(
            id="F-W1-S1-001", summary="Critical bug",
            detail="Detail", severity=FindingSeverity.CRITICAL,
        )
        _upsert_global_registry(entry, "run-1")

        # Also register a high finding WITH target_prd
        entry2 = FindingEntry(
            id="F-W1-S2-001", summary="Linked finding",
            detail="Detail", severity=FindingSeverity.HIGH,
            target_prd="PRD-FIX-099",
        )
        _upsert_global_registry(entry2, "run-1")

        # Import the traceability check's return logic directly
        from trw_mcp.tools.findings import _get_registry_path, _reader

        registry_path = _get_registry_path()
        assert registry_path.exists()

        reg_data = _reader.read_yaml(registry_path)
        entries = reg_data.get("entries", [])
        unlinked: list[str] = []
        for ref in entries:
            sev = str(ref.get("severity", ""))
            has_prd = bool(ref.get("target_prd"))
            if sev in ("critical", "high") and not has_prd:
                unlinked.append(str(ref.get("id", "")))

        assert "F-W1-S1-001" in unlinked
        assert "F-W1-S2-001" not in unlinked

    def test_no_registry_no_unlinked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No registry file means zero unlinked findings."""
        project = tmp_path / "project"
        (project / ".trw").mkdir(parents=True)

        monkeypatch.setattr(
            "trw_mcp.tools.requirements.resolve_project_root", lambda: project,
        )

        # No registry file — should not error
        from trw_mcp.models.config import TRWConfig
        config = TRWConfig()
        registry = project / ".trw" / config.findings_dir / config.findings_registry_file
        assert not registry.exists()


# ---------------------------------------------------------------------------
# PRD-QUAL-008: End-to-end tool function tests
# ---------------------------------------------------------------------------


def _make_tool_project(tmp_path: Path) -> tuple[Path, Path]:
    """Create a project with a run directory for tool testing.

    Returns:
        (project_root, run_dir) paths.
    """
    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    run_dir = project / "docs" / "test-task" / "runs" / "20260209T000000Z-test"
    meta_dir = run_dir / "meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "run.yaml").write_text(
        "run_id: test-run\ntask: test-task\n", encoding="utf-8",
    )
    # Create PRDs dir for finding_to_prd
    prds_dir = project / "docs" / "requirements-aare-f" / "prds"
    prds_dir.mkdir(parents=True)
    return project, run_dir


def _get_findings_tools() -> dict[str, object]:
    """Create a fresh MCP server and return tool map for findings."""
    from fastmcp import FastMCP
    from trw_mcp.tools.findings import register_findings_tools

    srv = FastMCP("test-findings")
    register_findings_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


class TestFindingRegisterEndToEnd:
    """End-to-end tests for trw_finding_register tool function."""

    def test_register_returns_expected_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Tool returns dict with required keys."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        result = tools["trw_finding_register"].fn(
            summary="Test finding",
            detail="This is a test finding with enough detail.",
            severity="high",
            component="test-module",
            run_path=str(run_dir),
        )
        assert "finding_id" in result
        assert "path" in result
        assert "dedup_match" in result
        assert "prd_candidate" in result
        assert result["severity"] == "high"
        assert result["status"] == "open"
        assert result["prd_candidate"] is True  # high severity = prd candidate

    def test_register_creates_entry_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Tool creates a YAML entry file in the run findings directory."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        result = tools["trw_finding_register"].fn(
            summary="Entry file test",
            detail="Verify that file is created on disk.",
            severity="medium",
            run_path=str(run_dir),
        )
        entry_path = Path(str(result["path"]))
        assert entry_path.exists()

    def test_register_invalid_severity_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Invalid severity raises ValidationError."""
        from trw_mcp.exceptions import ValidationError as TRWValidationError
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        with pytest.raises(TRWValidationError, match="Invalid severity"):
            tools["trw_finding_register"].fn(
                summary="Bad severity",
                detail="detail",
                severity="EXTREME",
                run_path=str(run_dir),
            )

    def test_register_updates_global_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Tool creates/updates global registry in .trw/."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        tools["trw_finding_register"].fn(
            summary="Registry test",
            detail="Verify global registry update.",
            severity="critical",
            run_path=str(run_dir),
        )
        registry_path = project / ".trw" / "findings" / "registry.yaml"
        assert registry_path.exists()

    def test_register_medium_is_not_prd_candidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Medium severity findings are not PRD candidates."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        result = tools["trw_finding_register"].fn(
            summary="Medium finding",
            detail="detail text",
            severity="medium",
            run_path=str(run_dir),
        )
        assert result["prd_candidate"] is False


class TestFindingQueryEndToEnd:
    """End-to-end tests for trw_finding_query tool function."""

    def test_query_empty_returns_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Query on empty run returns zero findings."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        result = tools["trw_finding_query"].fn(
            run_path=str(run_dir),
            include_global=False,
        )
        assert result["total"] == 0
        assert result["findings"] == []

    def test_query_returns_registered_findings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Query returns findings that were registered."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        tools["trw_finding_register"].fn(
            summary="Query test finding",
            detail="detail",
            severity="high",
            run_path=str(run_dir),
        )
        result = tools["trw_finding_query"].fn(
            run_path=str(run_dir),
            include_global=False,
        )
        assert result["total"] >= 1

    def test_query_filter_by_severity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Query filters by severity correctly."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        tools["trw_finding_register"].fn(
            summary="High sev",
            detail="detail",
            severity="high",
            run_path=str(run_dir),
        )
        tools["trw_finding_register"].fn(
            summary="Low sev",
            detail="detail",
            severity="low",
            run_path=str(run_dir),
        )
        result = tools["trw_finding_query"].fn(
            severity="high",
            run_path=str(run_dir),
            include_global=False,
        )
        assert result["total"] >= 1
        for f in result["findings"]:
            assert f.get("severity") == "high"

    def test_query_includes_global(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Query with include_global=True includes global registry."""
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        tools["trw_finding_register"].fn(
            summary="Global test",
            detail="detail",
            severity="critical",
            run_path=str(run_dir),
        )
        result = tools["trw_finding_query"].fn(
            run_path=str(run_dir),
            include_global=True,
        )
        assert "global" in result["sources"] or "per-run" in result["sources"]
        assert result["total"] >= 1


class TestFindingToPrdEndToEnd:
    """End-to-end tests for trw_finding_to_prd tool function."""

    def test_to_prd_creates_prd_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Converting a finding creates a PRD file."""
        import trw_mcp.tools.findings as find_mod
        import trw_mcp.tools.requirements as req_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())
        monkeypatch.setattr(req_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(req_mod, "_config", TRWConfig())
        monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_BODY", None)
        monkeypatch.setattr(req_mod, "_CACHED_TEMPLATE_VERSION", None)

        tools = _get_findings_tools()
        # First register a finding
        reg_result = tools["trw_finding_register"].fn(
            summary="Finding to convert to PRD",
            detail="This finding should be converted into a proper PRD.",
            severity="high",
            run_path=str(run_dir),
        )

        # Convert to PRD
        prd_result = tools["trw_finding_to_prd"].fn(
            finding_id=str(reg_result["finding_id"]),
            run_path=str(run_dir),
            category="FIX",
            priority="P1",
        )
        assert "prd_id" in prd_result
        assert "prd_path" in prd_result
        assert prd_result["status"] == "acknowledged"

    def test_to_prd_not_found_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Converting a non-existent finding raises StateError."""
        from trw_mcp.exceptions import StateError as TRWStateError
        import trw_mcp.tools.findings as find_mod

        project, run_dir = _make_tool_project(tmp_path)
        monkeypatch.setattr(find_mod, "resolve_project_root", lambda: project)
        monkeypatch.setattr(find_mod, "_config", TRWConfig())

        tools = _get_findings_tools()
        with pytest.raises(TRWStateError, match="Finding not found"):
            tools["trw_finding_to_prd"].fn(
                finding_id="F-W99-S99-999",
                run_path=str(run_dir),
            )
