"""Split bootstrap branch coverage for CLAUDE.md and manifest helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.bootstrap import (
    _minimal_claude_md_trw_block,
    _read_manifest,
    _update_claude_md_trw_section,
    _write_manifest,
)


@pytest.mark.unit
class TestUpdateClaudeMdTrwSection:
    """Cover error branches in _update_claude_md_trw_section."""

    def test_write_error_with_existing_markers(self, tmp_path: Path) -> None:
        """OSError writing updated CLAUDE.md with existing markers → error."""
        claude_md = tmp_path / "CLAUDE.md"
        content = (
            "# User content\n\n"
            "<!-- TRW AUTO-GENERATED — do not edit between markers -->\n"
            "<!-- trw:start -->\nOld TRW block\n<!-- trw:end -->\n"
        )
        claude_md.write_text(content, encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}

        with patch.object(Path, "write_text", side_effect=OSError("read-only fs")):
            _update_claude_md_trw_section(claude_md, result)

        assert any("Failed to update" in e for e in result["errors"])

    def test_malformed_markers_start_without_end(self, tmp_path: Path) -> None:
        """CLAUDE.md with trw:start but no trw:end → error about malformed markers."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("<!-- trw:start -->\nno end marker here\n", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _update_claude_md_trw_section(claude_md, result)

        assert any("malformed" in e for e in result["errors"])

    def test_append_when_no_trw_section(self, tmp_path: Path) -> None:
        """CLAUDE.md with no TRW section → append the block."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project\n", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _update_claude_md_trw_section(claude_md, result)

        assert not result["errors"]
        assert any(str(claude_md) in u for u in result["updated"])
        content = claude_md.read_text(encoding="utf-8")
        assert "<!-- trw:start -->" in content

    def test_append_write_error(self, tmp_path: Path) -> None:
        """OSError when appending TRW block → error."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project\n", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}

        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            _update_claude_md_trw_section(claude_md, result)

        assert any("Failed to update" in e for e in result["errors"])

    def test_content_without_trailing_newline(self, tmp_path: Path) -> None:
        """Content without trailing newline gets one added before TRW block."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _update_claude_md_trw_section(claude_md, result)

        content = claude_md.read_text(encoding="utf-8")
        assert "<!-- trw:start -->" in content
        assert "# My Project\n" in content

    def test_inline_prose_mention_not_treated_as_section(self, tmp_path: Path) -> None:
        """An inline mention of ``<!-- trw:start -->`` must not open the section.

        Marker matching is line-anchored: a marker referenced mid-paragraph (e.g.
        inside backticks) is not a whole-line delimiter, so a doc with no real
        markers is treated as having no TRW section and the block is appended —
        the user's prose (including the inline mention) is preserved verbatim.
        The old substring match would have mis-detected a start-without-end and
        errored (or spliced from the mention, truncating prose).
        """
        claude_md = tmp_path / "CLAUDE.md"
        prose = "# Notes\n\nWe open it with `<!-- trw:start -->` inline in prose.\n"
        claude_md.write_text(prose, encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _update_claude_md_trw_section(claude_md, result)

        content = claude_md.read_text(encoding="utf-8")
        assert not result["errors"]
        assert "We open it with `<!-- trw:start -->` inline in prose." in content
        assert "\n<!-- trw:start -->\n" in content
        assert "<!-- trw:end -->" in content

    def test_inline_mention_above_real_markers_replaces_only_whole_line_section(self, tmp_path: Path) -> None:
        """Whole-line markers are replaced even when an inline mention precedes them.

        The line-anchored search must bind to the genuine whole-line start marker
        (skipping the earlier backticked mention), so only the real section is
        swapped and the surrounding prose survives.
        """
        claude_md = tmp_path / "CLAUDE.md"
        content = (
            "# Doc\n\n"
            "Prose mentioning `<!-- trw:start -->` inline should be ignored.\n\n"
            "<!-- trw:start -->\nOLD BLOCK\n<!-- trw:end -->\n"
        )
        claude_md.write_text(content, encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _update_claude_md_trw_section(claude_md, result)

        updated = claude_md.read_text(encoding="utf-8")
        assert not result["errors"]
        assert "Prose mentioning `<!-- trw:start -->` inline should be ignored." in updated
        assert "OLD BLOCK" not in updated
        assert any(str(claude_md) in u for u in result["updated"])


@pytest.mark.unit
class TestMinimalClaudeMdTrwBlock:
    """Cover _minimal_claude_md_trw_block including fallback."""

    def test_returns_trw_block(self) -> None:
        """Returns a non-empty string with TRW markers."""
        block = _minimal_claude_md_trw_block()
        assert "<!-- trw:start -->" in block
        assert "<!-- trw:end -->" in block

    def test_fallback_when_header_marker_missing(self) -> None:
        """Returns trw:start..end block when header marker not in template."""
        fake_md = "<!-- trw:start -->\nSome content\n<!-- trw:end -->\n"
        with patch("trw_mcp.bootstrap._minimal_claude_md", return_value=fake_md):
            block = _minimal_claude_md_trw_block()
        assert "<!-- trw:start -->" in block
        assert "<!-- trw:end -->" in block

    def test_returns_empty_when_no_markers(self) -> None:
        """Returns empty string when no TRW markers found."""
        with patch("trw_mcp.bootstrap._minimal_claude_md", return_value="no markers"):
            block = _minimal_claude_md_trw_block()
        assert block == ""


@pytest.mark.unit
class TestReadManifest:
    """Cover _read_manifest edge cases."""

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        """Returns None when manifest file doesn't exist."""
        result = _read_manifest(tmp_path)
        assert result is None

    def test_returns_none_when_not_dict(self, tmp_path: Path) -> None:
        """Returns None when read_yaml returns a non-dict (e.g. a list)."""
        manifest_path = tmp_path / ".trw" / "managed-artifacts.yaml"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text("- not\n- a\n- dict\n", encoding="utf-8")

        mock_reader = MagicMock()
        mock_reader.read_yaml.return_value = ["not", "a", "dict"]
        with patch("trw_mcp.state.persistence.FileStateReader", return_value=mock_reader):
            result = _read_manifest(tmp_path)
        assert result is None

    def test_returns_none_on_oserror(self, tmp_path: Path) -> None:
        """Returns None when OSError reading manifest."""
        manifest_path = tmp_path / ".trw" / "managed-artifacts.yaml"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text("version: 1\nskills: []\n", encoding="utf-8")

        with patch("trw_mcp.state.persistence.FileStateReader.read_yaml", side_effect=OSError("io error")):
            result = _read_manifest(tmp_path)
        assert result is None

    def test_returns_dict_with_lists(self, tmp_path: Path) -> None:
        """Returns dict with skills/agents/hooks lists."""
        manifest_path = tmp_path / ".trw" / "managed-artifacts.yaml"
        manifest_path.parent.mkdir(parents=True)
        from trw_mcp.state.persistence import FileStateWriter

        FileStateWriter().write_yaml(
            manifest_path,
            {
                "version": 1,
                "skills": ["deliver", "learn"],
                "agents": ["trw-tester.md"],
                "hooks": ["session-start.sh"],
            },
        )

        result = _read_manifest(tmp_path)
        assert result is not None
        assert "deliver" in result["skills"]
        assert "trw-tester.md" in result["agents"]


@pytest.mark.unit
class TestWriteManifest:
    """Cover _write_manifest error path."""

    def test_write_manifest_oserror(self, tmp_path: Path) -> None:
        """OSError writing manifest adds to errors."""
        result: dict[str, list[str]] = {"created": [], "errors": []}

        with patch("trw_mcp.state.persistence.FileStateWriter.write_yaml", side_effect=OSError("disk full")):
            _write_manifest(tmp_path, result)

        assert any("Failed to write manifest" in e for e in result["errors"])

    def test_write_manifest_uses_updated_key_when_present(self, tmp_path: Path) -> None:
        """When 'updated' key exists in result, manifest is appended there."""
        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        (tmp_path / ".trw").mkdir(parents=True)

        _write_manifest(tmp_path, result)

        assert any("managed-artifacts" in u for u in result["updated"])
        assert not result["errors"]
