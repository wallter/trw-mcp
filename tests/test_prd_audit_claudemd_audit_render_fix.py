"""Audit rendering and fix-path coverage tests split from test_prd_audit_claudemd."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import _reset_config

from ._prd_audit_claudemd_support import _setup_project, _writer


class TestAuditHookVersions:
    """Cover lines 242, 257-280: _audit_hook_versions path."""

    def test_no_hooks_dir_returns_skip(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _audit_hook_versions

        result = _audit_hook_versions(tmp_path)
        assert result["verdict"] == "SKIP"
        assert result["total"] == 0

    def test_empty_hooks_dir_returns_pass(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _audit_hook_versions

        hooks_dir = tmp_path / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        result = _audit_hook_versions(tmp_path)
        assert result["verdict"] == "PASS"
        assert result["total"] == 0

    def test_hook_with_no_bundled_counterpart_not_outdated(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _audit_hook_versions

        hooks_dir = tmp_path / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        # Create a deployed hook with no bundled counterpart
        (hooks_dir / "custom-hook.sh").write_text("#!/bin/bash\necho custom\n")
        result = _audit_hook_versions(tmp_path)
        assert result["total"] == 1
        # No bundled equivalent → not in up_to_date, but also not in outdated
        assert result["up_to_date"] == 0
        assert len(result["outdated"]) == 0

    def test_outdated_hook_detected(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _audit_hook_versions

        hooks_dir = tmp_path / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)

        # Write a deployed hook with specific content
        deployed_hook = hooks_dir / "session-start.sh"
        deployed_hook.write_text("#!/bin/bash\necho old version\n")

        # Mock bundled data to return a different hash
        bundled_content = b"#!/bin/bash\necho new version\n"

        mock_bundled_file = MagicMock()
        mock_bundled_file.is_file.return_value = True
        mock_bundled_file.read_bytes.return_value = bundled_content

        mock_hooks_pkg = MagicMock()
        mock_hooks_pkg.__truediv__ = MagicMock(return_value=mock_bundled_file)

        mock_data_pkg = MagicMock()
        mock_data_pkg.__truediv__ = MagicMock(return_value=mock_hooks_pkg)

        # pkg_files is imported inside the function: patch at importlib.resources level
        with patch("importlib.resources.files", return_value=mock_data_pkg):
            result = _audit_hook_versions(tmp_path)

        assert result["total"] == 1
        assert "session-start.sh" in result["outdated"]
        assert result["verdict"] == "WARN"

    def test_up_to_date_hook_detected(self, tmp_path: Path) -> None:
        from trw_mcp.audit import _audit_hook_versions

        hooks_dir = tmp_path / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook_content = b"#!/bin/bash\necho same\n"

        deployed_hook = hooks_dir / "session-start.sh"
        deployed_hook.write_bytes(hook_content)

        mock_bundled_file = MagicMock()
        mock_bundled_file.is_file.return_value = True
        mock_bundled_file.read_bytes.return_value = hook_content  # same bytes

        mock_hooks_pkg = MagicMock()
        mock_hooks_pkg.__truediv__ = MagicMock(return_value=mock_bundled_file)

        mock_data_pkg = MagicMock()
        mock_data_pkg.__truediv__ = MagicMock(return_value=mock_hooks_pkg)

        with patch("importlib.resources.files", return_value=mock_data_pkg):
            result = _audit_hook_versions(tmp_path)

        assert result["up_to_date"] == 1
        assert result["verdict"] == "PASS"


class TestRunAuditFix:
    """Cover line 384: resync_learning_index call in fix path."""

    def test_fix_true_resyncs_index(self, tmp_path: Path) -> None:
        from trw_mcp.audit import run_audit

        project = _setup_project(tmp_path)
        entries_dir = project / ".trw" / "learnings" / "entries"

        # Add a normal entry and a bloat entry
        _writer.write_yaml(
            entries_dir / "good.yaml",
            {
                "id": "L-good",
                "summary": "Real learning",
                "status": "active",
                "impact": 0.8,
                "tags": ["testing"],
            },
        )
        _writer.write_yaml(
            entries_dir / "bloat.yaml",
            {
                "id": "L-bloat",
                "summary": "Repeated operation: checkpoint (5x)",
                "status": "active",
                "impact": 0.2,
            },
        )

        result = run_audit(project, fix=True)
        assert result["status"] == "ok"
        fix_actions = result.get("fix_actions")
        assert isinstance(fix_actions, dict)
        # index_resynced key should be present
        assert fix_actions.get("index_resynced") is True


class TestFormatMarkdownEdgeCases:
    """Cover format_markdown branches for duplicates, index, recall, hooks."""

    def test_duplicate_pairs_rendered(self) -> None:
        from trw_mcp.audit import format_markdown

        audit: dict[str, object] = {
            "project": "test",
            "generated_at": "2026-02-22T00:00:00Z",
            "duplicates": {
                "pairs": [{"older_id": "L-abc", "newer_id": "L-def", "similarity": 0.92}],
                "count": 1,
                "verdict": "WARN",
            },
        }
        md = format_markdown(audit)
        assert "L-abc" in md
        assert "L-def" in md
        assert "0.92" in md

    def test_index_consistency_warn_rendered(self) -> None:
        from trw_mcp.audit import format_markdown

        audit: dict[str, object] = {
            "project": "test",
            "generated_at": "2026-02-22T00:00:00Z",
            "index_consistency": {
                "analytics_total": 100,
                "actual_count": 95,
                "match": False,
                "verdict": "WARN",
            },
        }
        md = format_markdown(audit)
        assert "100" in md
        assert "95" in md

    def test_index_consistency_pass_rendered(self) -> None:
        from trw_mcp.audit import format_markdown

        audit: dict[str, object] = {
            "project": "test",
            "generated_at": "2026-02-22T00:00:00Z",
            "index_consistency": {
                "analytics_total": 50,
                "actual_count": 50,
                "match": True,
                "verdict": "PASS",
            },
        }
        md = format_markdown(audit)
        assert "Counts match" in md

    def test_recall_zero_match_queries_rendered(self) -> None:
        from trw_mcp.audit import format_markdown

        audit: dict[str, object] = {
            "project": "test",
            "generated_at": "2026-02-22T00:00:00Z",
            "recall_effectiveness": {
                "total_queries": 10,
                "wildcard_queries": 2,
                "named_queries": 8,
                "zero_match": 3,
                "miss_rate": 0.375,
                "top_zero_match_queries": ["concept-a", "concept-b"],
                "verdict": "WARN",
            },
        }
        md = format_markdown(audit)
        assert "concept-a" in md
        assert "concept-b" in md

    def test_outdated_hooks_rendered(self) -> None:
        from trw_mcp.audit import format_markdown

        audit: dict[str, object] = {
            "project": "test",
            "generated_at": "2026-02-22T00:00:00Z",
            "hook_versions": {
                "total": 3,
                "up_to_date": 2,
                "outdated": ["session-start.sh"],
                "verdict": "WARN",
            },
        }
        md = format_markdown(audit)
        assert "session-start.sh" in md

    def test_hook_versions_skip_not_rendered(self) -> None:
        from trw_mcp.audit import format_markdown

        audit: dict[str, object] = {
            "project": "test",
            "generated_at": "2026-02-22T00:00:00Z",
            "hook_versions": {
                "total": 0,
                "up_to_date": 0,
                "outdated": [],
                "verdict": "SKIP",
            },
        }
        md = format_markdown(audit)
        # Hook Versions section should not appear for SKIP
        assert "## Hook Versions" not in md


# =============================================================================
# state/claude_md.py — missing lines
# =============================================================================

class TestAuditRunFixEnvVarRestored:
    """Cover audit.py line 384: env var restoration in fix path."""

    def test_fix_restores_env_var_when_previously_set(self, tmp_path: Path) -> None:
        from trw_mcp.audit import run_audit

        project = _setup_project(tmp_path)
        original_root = str(tmp_path / "original")
        os.environ["TRW_PROJECT_ROOT"] = original_root

        try:
            result = run_audit(project, fix=True)
            # env var should be restored to original value after fix
            assert os.environ.get("TRW_PROJECT_ROOT") == original_root
            assert result["status"] == "ok"
        finally:
            os.environ.pop("TRW_PROJECT_ROOT", None)
            _reset_config()
