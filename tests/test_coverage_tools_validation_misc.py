from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.validation import _is_substantive_line, auto_progress_prds, check_integration, score_section_density


class TestValidationIsSubstantiveLine:
    """Lines 1315-1317: HTML comment inline branch."""

    def test_single_line_html_comment_not_substantive(self) -> None:
        assert _is_substantive_line("<!-- This is a comment -->") is False

    def test_multiline_html_comment_start_is_substantive(self) -> None:
        assert _is_substantive_line("<!-- start of block") is True

    def test_table_separator_not_substantive(self) -> None:
        assert _is_substantive_line("|---|---|") is False

    def test_horizontal_rule_not_substantive(self) -> None:
        assert _is_substantive_line("---") is False

    def test_real_content_is_substantive(self) -> None:
        assert _is_substantive_line("FR01: The system shall process requests.") is True

    def test_heading_not_substantive(self) -> None:
        assert _is_substantive_line("# Section heading") is False

    def test_placeholder_braces_not_substantive(self) -> None:
        assert _is_substantive_line("{Brief description here}") is False


class TestValidationScoreSectionDensityEmpty:
    """Lines 1340-1343: score_section_density behavior."""

    def test_score_section_density_empty_string_zero_density(self) -> None:
        result = score_section_density("Test Section", "")
        assert result.section_name == "Test Section"
        assert result.density == 0.0
        assert result.substantive_lines == 0

    def test_score_section_density_html_comment_counted_as_placeholder(self) -> None:
        result = score_section_density("Test", "<!-- comment -->\nReal content here\n")
        assert result.substantive_lines == 1
        assert result.placeholder_lines == 1

    def test_score_section_density_substantive_content(self) -> None:
        body = "FR01: System shall process requests.\nFR02: System shall respond quickly.\n"
        result = score_section_density("Functional Requirements", body)
        assert result.substantive_lines == 2
        assert result.density > 0.0


class TestValidationAutoProgressOSError:
    """Lines 1941-1945 and 1957-1958."""

    def test_auto_progress_prd_read_oserror_continues(self, tmp_path: Path) -> None:
        run_path = tmp_path / "run"
        (run_path / "meta").mkdir(parents=True)
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        (prds_dir / "PRD-CORE-001.md").write_text("---\nprd:\n  status: draft\n---\n")
        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        with (
            patch("trw_mcp.state.prd_utils.discover_governing_prds", return_value=["PRD-CORE-001"]),
            patch("trw_mcp.state.prd_utils.parse_frontmatter", side_effect=OSError("cannot read prd")),
        ):
            results = auto_progress_prds(run_path, "plan", prds_dir, config)

        assert results == []

    def test_auto_progress_index_sync_exception_swallowed(self, tmp_path: Path) -> None:
        import yaml as _yaml

        run_path = tmp_path / "run"
        meta_dir = run_path / "meta"
        meta_dir.mkdir(parents=True)
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        (prds_dir / "PRD-CORE-002.md").write_text("---\nprd:\n  status: draft\n---\n\nContent\n")
        (meta_dir / "run.yaml").write_text(_yaml.dump({"run_id": "test-run", "prd_scope": ["PRD-CORE-002"]}))
        config = TRWConfig(trw_dir=str(tmp_path / ".trw"))

        with (
            patch("trw_mcp.state.validation.prd_progression.update_frontmatter"),
            patch("trw_mcp.state.index_sync.sync_index_md", side_effect=RuntimeError("index sync failed")),
        ):
            results = auto_progress_prds(run_path, "plan", prds_dir, config)

        applied = [r for r in results if r.get("applied")]
        assert len(applied) == 1


class TestValidationCheckIntegrationServerOSError:
    """Lines 2021-2022 and 2029."""

    def test_check_integration_server_read_oserror(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "trw_mcp"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        server_path = src_dir / "server.py"
        (tools_dir / "mytool.py").write_text("def register_mytool_tools(server):\n    pass\n")
        server_path.write_text("from trw_mcp.tools.mytool import register_mytool_tools\n")
        original_read_text = Path.read_text

        def selective_read_text(self: Path, *args, **kwargs) -> str:
            if self == server_path:
                raise OSError("permission denied")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", selective_read_text):
            result = check_integration(src_dir)

        assert "unregistered" in result
        assert "mytool" in result["unregistered"]

    def test_check_integration_missing_tests_appended(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "trw_mcp"
        tools_dir = src_dir / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "newtool.py").write_text("def register_newtool_tools(server):\n    pass\n")
        (src_dir / "server.py").write_text(
            "from trw_mcp.tools.newtool import register_newtool_tools\nregister_newtool_tools(server)\n"
        )

        result = check_integration(src_dir)

        assert "missing_tests" in result
        assert isinstance(result["missing_tests"], list)
        assert "test_tools_newtool.py" in result["missing_tests"]
        assert "newtool" not in result["unregistered"]
