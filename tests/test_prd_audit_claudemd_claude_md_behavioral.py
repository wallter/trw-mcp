"""Claude MD behavioral rendering tests split from test_prd_audit_claudemd."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import _reset_config

from ._prd_audit_claudemd_support import _writer


class TestRenderBehavioralProtocol:
    """Cover lines 372-373, 376: behavioral_protocol loading paths."""

    def test_returns_empty_when_file_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.claude_md import render_behavioral_protocol

        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _reset_config()

        # No behavioral_protocol.yaml exists
        result = render_behavioral_protocol()
        assert result == ""

    def test_returns_directives_when_file_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.claude_md import render_behavioral_protocol

        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _reset_config()

        # Create behavioral_protocol.yaml
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        _writer.write_yaml(
            context_dir / "behavioral_protocol.yaml",
            {
                "directives": [
                    "Call trw_session_start at session start",
                    "Call trw_deliver at task completion",
                ]
            },
        )

        # Patch resolve_project_root in _static_sections to return tmp_path
        monkeypatch.setattr(
            "trw_mcp.state.claude_md._static_sections.resolve_project_root",
            lambda: tmp_path,
        )
        result = render_behavioral_protocol()

        assert "trw_session_start" in result
        assert "trw_deliver" in result

    def test_returns_empty_on_read_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.claude_md import render_behavioral_protocol

        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _reset_config()

        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        # Write a corrupt YAML
        (context_dir / "behavioral_protocol.yaml").write_text(": invalid: [yaml\n", encoding="utf-8")

        monkeypatch.setattr(
            "trw_mcp.state.claude_md._static_sections.resolve_project_root",
            lambda: tmp_path,
        )
        result = render_behavioral_protocol()
        assert result == ""


class TestMergeTrwSectionTruncationNoMarkers:
    """Cover line 588 (auto_idx branch) and no-marker fallback truncation (line 624-625)."""

    def test_auto_comment_before_marker_is_found(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import (
            TRW_AUTO_COMMENT,
            TRW_MARKER_END,
            TRW_MARKER_START,
            merge_trw_section,
        )

        target = tmp_path / "CLAUDE.md"
        # Existing file with auto comment before marker
        existing = f"# User content\n\n{TRW_AUTO_COMMENT}\n{TRW_MARKER_START}\nOld TRW content\n{TRW_MARKER_END}\n"
        target.write_text(existing, encoding="utf-8")

        new_section = f"\n{TRW_AUTO_COMMENT}\n{TRW_MARKER_START}\nNew content\n{TRW_MARKER_END}\n"
        merge_trw_section(target, new_section, max_lines=1000)

        result = target.read_text(encoding="utf-8")
        assert "New content" in result
        assert "Old TRW content" not in result

    def test_truncation_no_intact_markers(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import merge_trw_section

        target = tmp_path / "CLAUDE.md"
        # Large content without TRW markers — should use simple truncation
        big_content = "\n".join(f"Line {i}" for i in range(100))
        # Write a section without any TRW markers to trigger simple truncation
        short_section = "\nNo markers here at all\n"
        target.write_text(big_content, encoding="utf-8")

        merge_trw_section(target, short_section, max_lines=10)
        result = target.read_text(encoding="utf-8")
        assert "trw: truncated to line limit" in result

    def test_no_existing_file_creates_new(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import (
            TRW_MARKER_END,
            TRW_MARKER_START,
            merge_trw_section,
        )

        target = tmp_path / "CLAUDE.md"
        assert not target.exists()
        section = f"\n{TRW_MARKER_START}\nContent\n{TRW_MARKER_END}\n"
        merge_trw_section(target, section, max_lines=1000)
        assert target.exists()
        result = target.read_text(encoding="utf-8")
        assert "Content" in result

    def test_existing_file_without_markers_appended(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md import (
            TRW_MARKER_END,
            TRW_MARKER_START,
            merge_trw_section,
        )

        target = tmp_path / "CLAUDE.md"
        target.write_text("# Existing user content\n", encoding="utf-8")
        section = f"\n{TRW_MARKER_START}\nAppended\n{TRW_MARKER_END}\n"
        merge_trw_section(target, section, max_lines=1000)
        result = target.read_text(encoding="utf-8")
        assert "Existing user content" in result
        assert "Appended" in result

class TestRenderBehavioralProtocolEmptyDirectives:
    """Cover claude_md.py line 376: empty/non-list directives return empty string."""

    def test_empty_directives_returns_empty_string(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.claude_md import render_behavioral_protocol

        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _reset_config()

        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        _writer.write_yaml(
            context_dir / "behavioral_protocol.yaml",
            {
                "directives": []  # empty list → line 376: return ""
            },
        )

        monkeypatch.setattr(
            "trw_mcp.state.claude_md._static_sections.resolve_project_root",
            lambda: tmp_path,
        )
        result = render_behavioral_protocol()

        assert result == ""

    def test_non_list_directives_returns_empty_string(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state.claude_md import render_behavioral_protocol

        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _reset_config()

        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        _writer.write_yaml(
            context_dir / "behavioral_protocol.yaml",
            {
                "directives": "not a list"  # not isinstance list → line 376: return ""
            },
        )

        monkeypatch.setattr(
            "trw_mcp.state.claude_md._static_sections.resolve_project_root",
            lambda: tmp_path,
        )
        result = render_behavioral_protocol()

        assert result == ""
