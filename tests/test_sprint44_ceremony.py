"""Tests for Sprint 44 ceremony changes — compliance copy and integration review gate.

Coverage:
- check_delivery_gates: integration-review.yaml verdict=block adds integration_review_block
- check_delivery_gates: integration-review.yaml verdict=warn adds integration_review_warning
- check_delivery_gates: no integration-review.yaml is fine (single-shard)
- copy_compliance_artifacts: copies review files to compliance dir
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._ceremony_helpers import check_delivery_gates, copy_compliance_artifacts


def _write_review_yaml(run_path: Path, verdict: str, critical_count: int = 0) -> None:
    writer = FileStateWriter()
    meta = run_path / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    writer.write_yaml(meta / "review.yaml", {
        "review_id": "rev-test",
        "verdict": verdict,
        "critical_count": critical_count,
        "findings": [],
    })


def _write_integration_review_yaml(run_path: Path, verdict: str, findings: list[dict] | None = None) -> None:
    writer = FileStateWriter()
    meta = run_path / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    writer.write_yaml(meta / "integration-review.yaml", {
        "review_id": "int-rev-test",
        "verdict": verdict,
        "findings": findings or [],
    })


class TestCheckDeliveryGatesIntegrationReview:
    """Tests for integration review gate in check_delivery_gates."""

    def test_integration_review_block_sets_block_key(self, tmp_path: Path) -> None:
        _write_review_yaml(tmp_path, "pass")
        _write_integration_review_yaml(
            tmp_path,
            verdict="block",
            findings=[{"severity": "critical", "description": "API mismatch"}],
        )

        reader = FileStateReader()
        result = check_delivery_gates(tmp_path, reader)

        assert "integration_review_block" in result
        assert "critical finding" in result["integration_review_block"]  # type: ignore[operator]

    def test_integration_review_warn_sets_warning_key(self, tmp_path: Path) -> None:
        _write_review_yaml(tmp_path, "pass")
        _write_integration_review_yaml(tmp_path, verdict="warn")

        reader = FileStateReader()
        result = check_delivery_gates(tmp_path, reader)

        assert "integration_review_warning" in result
        assert "integration_review_block" not in result

    def test_no_integration_review_file_no_error(self, tmp_path: Path) -> None:
        _write_review_yaml(tmp_path, "pass")
        # No integration-review.yaml

        reader = FileStateReader()
        result = check_delivery_gates(tmp_path, reader)

        assert "integration_review_block" not in result
        assert "integration_review_warning" not in result

    def test_integration_review_pass_no_keys(self, tmp_path: Path) -> None:
        _write_review_yaml(tmp_path, "pass")
        _write_integration_review_yaml(tmp_path, verdict="pass")

        reader = FileStateReader()
        result = check_delivery_gates(tmp_path, reader)

        assert "integration_review_block" not in result
        assert "integration_review_warning" not in result

    def test_integration_review_block_counts_criticals(self, tmp_path: Path) -> None:
        _write_review_yaml(tmp_path, "pass")
        _write_integration_review_yaml(
            tmp_path,
            verdict="block",
            findings=[
                {"severity": "critical", "description": "issue 1"},
                {"severity": "critical", "description": "issue 2"},
                {"severity": "warning", "description": "warning 1"},
            ],
        )

        reader = FileStateReader()
        result = check_delivery_gates(tmp_path, reader)

        block_msg = result.get("integration_review_block", "")
        assert "2 critical" in str(block_msg)

    def test_none_run_path_returns_empty(self) -> None:
        reader = FileStateReader()
        result = check_delivery_gates(None, reader)
        assert result == {}


class TestCopyComplianceArtifacts:
    """Tests for copy_compliance_artifacts function."""

    def test_copies_review_yaml(self, tmp_path: Path) -> None:
        run_path = tmp_path / "run-test123"
        meta = run_path / "meta"
        meta.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(meta / "review.yaml", {"verdict": "pass", "review_id": "r1"})

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        config = TRWConfig()
        reader = FileStateReader()

        result = copy_compliance_artifacts(run_path, trw_dir, config, reader, writer)

        assert "compliance_artifacts_copied" in result
        assert "review.yaml" in result["compliance_artifacts_copied"]  # type: ignore[operator]

    def test_copies_integration_review_yaml(self, tmp_path: Path) -> None:
        run_path = tmp_path / "run-test456"
        meta = run_path / "meta"
        meta.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(meta / "review.yaml", {"verdict": "pass", "review_id": "r2"})
        writer.write_yaml(meta / "integration-review.yaml", {"verdict": "warn", "findings": []})

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        config = TRWConfig()
        reader = FileStateReader()

        result = copy_compliance_artifacts(run_path, trw_dir, config, reader, writer)

        copied = result.get("compliance_artifacts_copied", [])
        assert isinstance(copied, list)
        assert "review.yaml" in copied
        assert "integration-review.yaml" in copied

    def test_no_artifacts_returns_empty(self, tmp_path: Path) -> None:
        run_path = tmp_path / "run-empty"
        run_path.mkdir(parents=True)
        (run_path / "meta").mkdir()

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        config = TRWConfig()
        reader = FileStateReader()
        writer = FileStateWriter()

        result = copy_compliance_artifacts(run_path, trw_dir, config, reader, writer)

        # No files to copy, result should be empty
        assert result == {}

    def test_none_run_path_returns_empty(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        config = TRWConfig()
        reader = FileStateReader()
        writer = FileStateWriter()

        result = copy_compliance_artifacts(None, trw_dir, config, reader, writer)

        assert result == {}

    def test_compliance_dir_path_contains_run_id(self, tmp_path: Path) -> None:
        run_path = tmp_path / "20260303T050000Z-abc12345"
        meta = run_path / "meta"
        meta.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(meta / "review.yaml", {"verdict": "pass"})

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        config = TRWConfig()
        reader = FileStateReader()

        result = copy_compliance_artifacts(run_path, trw_dir, config, reader, writer)

        compliance_dir = result.get("compliance_dir", "")
        assert "20260303T050000Z-abc12345" in str(compliance_dir)

    def test_files_are_actually_written(self, tmp_path: Path) -> None:
        run_path = tmp_path / "run-verify"
        meta = run_path / "meta"
        meta.mkdir(parents=True)

        writer = FileStateWriter()
        original_data: dict[str, object] = {"verdict": "pass", "review_id": "verify-01"}
        writer.write_yaml(meta / "review.yaml", original_data)

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        config = TRWConfig()
        reader = FileStateReader()

        result = copy_compliance_artifacts(run_path, trw_dir, config, reader, writer)

        compliance_dir = Path(str(result["compliance_dir"]))
        written = reader.read_yaml(compliance_dir / "review.yaml")
        assert written.get("verdict") == "pass"
        assert written.get("review_id") == "verify-01"
