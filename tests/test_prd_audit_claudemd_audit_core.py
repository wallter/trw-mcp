"""Audit core coverage tests split from test_prd_audit_claudemd."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from trw_mcp.models.config import TRWConfig, _reset_config

from ._prd_audit_claudemd_support import _setup_project, _writer


class TestLoadProjectConfig:
    """Cover lines 41-42: config.yaml loading path."""

    def test_loads_config_yaml_when_present(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _load_project_config

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        _writer.write_yaml(
            trw_dir / "config.yaml",
            {
                "learning_max_entries": 250,
            },
        )
        config = _load_project_config(trw_dir)
        assert config.learning_max_entries == 250

    def test_returns_default_config_when_missing(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _load_project_config

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = _load_project_config(trw_dir)
        assert isinstance(config, TRWConfig)


class TestIterEntries:
    """Cover lines 50, 53, 56-57: _iter_entries edge cases."""

    def test_returns_empty_for_nonexistent_dir(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _iter_entries

        result = _iter_entries(tmp_path / "nonexistent")
        assert result == []

    def test_skips_index_yaml(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _iter_entries

        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        _writer.write_yaml(entries_dir / "index.yaml", {"total_count": 0})
        _writer.write_yaml(entries_dir / "entry-001.yaml", {"id": "L-001", "summary": "A"})

        result = _iter_entries(entries_dir)
        assert len(result) == 1
        assert result[0]["id"] == "L-001"

    def test_skips_corrupt_yaml_files(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _iter_entries

        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        (entries_dir / "corrupt.yaml").write_text(": bad: [yaml\n", encoding="utf-8")
        _writer.write_yaml(entries_dir / "good.yaml", {"id": "L-002", "summary": "B"})

        result = _iter_entries(entries_dir)
        # Only the good entry should be returned; corrupt one is skipped
        assert len(result) == 1
        assert result[0]["id"] == "L-002"


class TestAuditRecallEffectivenessEdgeCases:
    """Cover lines 171, 174-175, 186-187: recall log parsing edge cases."""

    def test_empty_string_query_counts_as_wildcard(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _audit_recall_effectiveness

        project = _setup_project(tmp_path)
        receipts_dir = project / ".trw" / "learnings" / "receipts"
        receipts_dir.mkdir(parents=True)
        log_path = receipts_dir / "recall_log.jsonl"

        # Empty query string should be treated as wildcard
        lines = [
            json.dumps({"query": "", "matched_ids": []}),
            json.dumps({"query": "pydantic", "matched_ids": ["L-1"]}),
        ]
        log_path.write_text("\n".join(lines), encoding="utf-8")

        result = _audit_recall_effectiveness(project / ".trw", TRWConfig())
        assert result["wildcard_queries"] == 1
        assert result["named_queries"] == 1

    def test_zero_match_queries_tracked(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _audit_recall_effectiveness

        project = _setup_project(tmp_path)
        receipts_dir = project / ".trw" / "learnings" / "receipts"
        receipts_dir.mkdir(parents=True)
        log_path = receipts_dir / "recall_log.jsonl"

        lines = [
            json.dumps({"query": "query-a", "matched_ids": []}),
            json.dumps({"query": "query-b", "matched_ids": []}),
            json.dumps({"query": "query-c", "matched_ids": []}),
            json.dumps({"query": "query-d", "matched_ids": []}),
            json.dumps({"query": "query-e", "matched_ids": []}),
            json.dumps({"query": "query-f", "matched_ids": []}),  # beyond cap of 5
        ]
        log_path.write_text("\n".join(lines), encoding="utf-8")

        result = _audit_recall_effectiveness(project / ".trw", TRWConfig())
        # Only up to 5 zero-match queries stored
        assert len(result["top_zero_match_queries"]) <= 5

    def test_invalid_json_lines_skipped(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _audit_recall_effectiveness

        project = _setup_project(tmp_path)
        receipts_dir = project / ".trw" / "learnings" / "receipts"
        receipts_dir.mkdir(parents=True)
        log_path = receipts_dir / "recall_log.jsonl"

        content = (
            json.dumps({"query": "pydantic", "matched_ids": ["L-1"]})
            + "\n"
            + "not valid json\n"
            + json.dumps({"query": "testing", "matched_ids": []})
            + "\n"
        )
        log_path.write_text(content, encoding="utf-8")

        result = _audit_recall_effectiveness(project / ".trw", TRWConfig())
        assert result["total_queries"] == 2  # invalid JSON skipped
        assert result["verdict"] in ("PASS", "WARN")

    def test_exception_reading_log_returns_skip(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _audit_recall_effectiveness

        project = _setup_project(tmp_path)
        receipts_dir = project / ".trw" / "learnings" / "receipts"
        receipts_dir.mkdir(parents=True)
        log_path = receipts_dir / "recall_log.jsonl"
        log_path.write_text("placeholder", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=OSError("read failed")):
            result = _audit_recall_effectiveness(project / ".trw", TRWConfig())
        assert result["verdict"] == "SKIP"


class TestAuditCeremonyComplianceEnvRestore:
    """Cover lines 214, 221: TRW_PROJECT_ROOT env var restoration branches."""

    def test_env_var_restored_when_originally_set(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _audit_ceremony_compliance

        project = _setup_project(tmp_path)
        original = "original_project"
        os.environ["TRW_PROJECT_ROOT"] = original
        try:
            result = _audit_ceremony_compliance(project)
            assert isinstance(result, dict)
            assert "verdict" in result
            # Env var should be restored to original
            assert os.environ.get("TRW_PROJECT_ROOT") == original
        finally:
            os.environ.pop("TRW_PROJECT_ROOT", None)
            _reset_config()

    def test_env_var_removed_when_not_originally_set(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _audit_ceremony_compliance

        project = _setup_project(tmp_path)
        os.environ.pop("TRW_PROJECT_ROOT", None)
        try:
            result = _audit_ceremony_compliance(project)
            assert isinstance(result, dict)
            # Env var must NOT be left behind
            assert "TRW_PROJECT_ROOT" not in os.environ
        finally:
            os.environ.pop("TRW_PROJECT_ROOT", None)
            _reset_config()


class TestAuditRecallEffectivenessBlankLines:
    """Cover audit.py line 171: blank line continue in recall log."""

    def test_blank_lines_in_recall_log_are_skipped(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _audit_recall_effectiveness

        project = _setup_project(tmp_path)
        receipts_dir = project / ".trw" / "learnings" / "receipts"
        receipts_dir.mkdir(parents=True)
        log_path = receipts_dir / "recall_log.jsonl"

        # Include actual blank lines in the file
        content = (
            json.dumps({"query": "pydantic", "matched_ids": ["L-1"]})
            + "\n"
            + "\n"  # blank line — triggers line 171 continue
            + "\n"  # another blank line
            + json.dumps({"query": "testing", "matched_ids": []})
            + "\n"
        )
        log_path.write_text(content, encoding="utf-8")

        result = _audit_recall_effectiveness(project / ".trw", TRWConfig())
        assert result["total_queries"] == 2  # blank lines don't count


class TestAuditCeremonyComplianceNonDictAggregate:
    """Cover audit.py line 221: aggregate = {} when not a dict."""

    def test_non_dict_aggregate_handled_gracefully(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _audit_ceremony_compliance

        project = _setup_project(tmp_path)

        with patch("trw_mcp.audit.scan_all_runs") as mock_scan:
            mock_scan.return_value = {
                "runs_scanned": 5,
                "aggregate": "not a dict",  # triggers line 221: aggregate = {}
            }
            result = _audit_ceremony_compliance(project)

        assert result["runs_scanned"] == 5
        assert result["avg_ceremony_score"] == 0
        assert result["verdict"] == "WARN"
