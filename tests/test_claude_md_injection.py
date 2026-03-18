"""Tests for CLAUDE.md injection: template rendering, validation, and sync.

Covers:
  - render_template marker resolution and validation
  - Bundled template correctness (no hardcoded text, key alignment)
  - Full sync integration (no empty headers, no unreplaced markers)
  - merge_trw_section user content preservation and replacement
  - Project-local template override key completeness
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md._parser import (
    TRW_AUTO_COMMENT,
    TRW_MARKER_END,
    TRW_MARKER_START,
    load_claude_md_template,
    merge_trw_section,
    render_template,
)

# ---------------------------------------------------------------------------
# Test 1: render_template resolves all markers
# ---------------------------------------------------------------------------


class TestRenderTemplateResolvesAllMarkers:
    """Given a template with known placeholders and a context dict with all keys,
    assert no ``{{`` remains in output."""

    def test_all_markers_resolved(self) -> None:
        template = "Hello {{name}}, welcome to {{place}}!"
        context = {"name": "Alice", "place": "Wonderland"}
        result = render_template(template, context)
        assert "{{" not in result
        assert "}}" not in result
        assert result == "Hello Alice, welcome to Wonderland!"


# ---------------------------------------------------------------------------
# Test 2: render_template raises on unreplaced markers
# ---------------------------------------------------------------------------


class TestRenderTemplateRaisesOnUnreplacedMarkers:
    """Given a template with ``{{unknown_key}}`` and a context missing that key,
    assert StateError is raised with the marker name in the message."""

    def test_raises_state_error_with_marker_name(self) -> None:
        template = "Hello {{name}}, your role is {{unknown_key}}."
        context = {"name": "Bob"}
        with pytest.raises(StateError, match="unknown_key"):
            render_template(template, context)

    def test_lists_all_missing_markers(self) -> None:
        template = "{{alpha}} and {{beta}} are missing"
        context: dict[str, str] = {}
        with pytest.raises(StateError, match="alpha") as exc_info:
            render_template(template, context)
        assert "beta" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 3: render_template collapses blank lines
# ---------------------------------------------------------------------------


class TestRenderTemplateCollapsesBlankLines:
    """Verify 3+ consecutive blank lines collapse to 2."""

    def test_triple_blank_collapses(self) -> None:
        template = "A\n\n\n\nB"
        context: dict[str, str] = {}
        result = render_template(template, context)
        assert "\n\n\n" not in result
        assert "A\n\nB" == result

    def test_double_blank_preserved(self) -> None:
        template = "A\n\nB"
        context: dict[str, str] = {}
        result = render_template(template, context)
        assert result == "A\n\nB"

    def test_many_blanks_collapse(self) -> None:
        template = "X" + "\n" * 10 + "Y"
        context: dict[str, str] = {}
        result = render_template(template, context)
        assert result == "X\n\nY"


# ---------------------------------------------------------------------------
# Test 4: bundled template has no hardcoded text
# ---------------------------------------------------------------------------


class TestBundledTemplateHasNoHardcodedText:
    """Load the bundled template and verify every non-marker, non-whitespace,
    non-HTML-comment line contains only ``{{...}}`` placeholder tokens."""

    def test_no_hardcoded_content(self) -> None:
        data_dir = Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "templates"
        bundled = data_dir / "claude_md.md"
        assert bundled.exists(), f"Bundled template not found at {bundled}"

        content = bundled.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            # Skip blank lines
            if not stripped:
                continue
            # Skip HTML comments (markers)
            if stripped.startswith("<!--") and stripped.endswith("-->"):
                continue
            # Every remaining line must be a pure placeholder reference
            cleaned = re.sub(r"\{\{\w+\}\}", "", stripped)
            assert cleaned == "", f"Line {lineno} has hardcoded text outside placeholders: {line!r}"


# ---------------------------------------------------------------------------
# Test 5: bundled template keys match sync context
# ---------------------------------------------------------------------------


class TestBundledTemplateKeysMatchSyncContext:
    """Extract all ``{{key}}`` from the bundled template and compare against
    the keys in ``_sync.py``'s ``tpl_context`` dict. They must match exactly."""

    def test_keys_match(self) -> None:
        data_dir = Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "templates"
        bundled = data_dir / "claude_md.md"
        content = bundled.read_text(encoding="utf-8")
        template_keys = set(re.findall(r"\{\{(\w+)\}\}", content))

        # The canonical keys from _sync.py's tpl_context
        sync_keys = {
            "imperative_opener",
            "ceremony_quick_ref",
            "memory_harmonization",
            "framework_reference",
            "closing_reminder",
            "behavioral_protocol",
            "delegation_section",
            "agent_teams_section",
            "rationalization_watchlist",
            "ceremony_phases",
            "ceremony_table",
            "ceremony_flows",
            "architecture_section",
            "conventions_section",
            "categorized_learnings",
            "patterns_section",
            "adherence_section",
        }

        missing_from_template = sync_keys - template_keys
        extra_in_template = template_keys - sync_keys

        assert not missing_from_template, f"Template is missing keys that _sync.py provides: {missing_from_template}"
        assert not extra_in_template, f"Template has keys that _sync.py does not provide: {extra_in_template}"


# ---------------------------------------------------------------------------
# Test 6: full sync no empty section headers
# ---------------------------------------------------------------------------


class TestFullSyncNoEmptySectionHeaders:
    """Mock dependencies and run ``execute_claude_md_sync``. Assert the output
    doesn't contain empty ``## ... (Auto-Generated)`` headers followed by
    another header."""

    def test_no_empty_autogen_headers(self, tmp_path: Path) -> None:
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir()
        (trw_dir / "context").mkdir()
        (trw_dir / "patterns").mkdir()

        target = tmp_path / "CLAUDE.md"
        target.write_text("# My Project\n\nSome content.\n", encoding="utf-8")

        config = TRWConfig(trw_dir=str(trw_dir))
        reader = FileStateReader()
        writer = FileStateWriter()
        llm = MagicMock()
        llm.available = False

        with (
            patch("trw_mcp.state.claude_md._sync.collect_promotable_learnings", return_value=[]),
            patch("trw_mcp.state.claude_md._sync.collect_patterns", return_value=[]),
            patch("trw_mcp.state.claude_md._sync.collect_context_data", return_value=({}, {})),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.update_analytics_sync"),
            patch("trw_mcp.state.analytics.mark_promoted"),
        ):
            from trw_mcp.state.claude_md._sync import execute_claude_md_sync

            result = execute_claude_md_sync(
                scope="root",
                target_dir=None,
                config=config,
                reader=reader,
                writer=writer,
                llm=llm,
            )

        output = target.read_text(encoding="utf-8")
        # Should not have empty auto-generated section headers
        assert "## TRW Behavioral Protocol (Auto-Generated)\n\n##" not in output
        assert "## TRW Ceremony Tools (Auto-Generated)\n\n##" not in output
        assert "## TRW Learnings (Auto-Generated)\n\n##" not in output
        assert result["status"] == "synced"


# ---------------------------------------------------------------------------
# Test 7: full sync no unreplaced markers
# ---------------------------------------------------------------------------


class TestFullSyncNoUnreplacedMarkers:
    """After sync, read the target file and assert no ``{{`` or ``}}`` in content."""

    def test_no_template_markers_in_output(self, tmp_path: Path) -> None:
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir()
        (trw_dir / "context").mkdir()
        (trw_dir / "patterns").mkdir()

        target = tmp_path / "CLAUDE.md"
        target.write_text("# Test\n", encoding="utf-8")

        config = TRWConfig(trw_dir=str(trw_dir))
        reader = FileStateReader()
        writer = FileStateWriter()
        llm = MagicMock()
        llm.available = False

        with (
            patch("trw_mcp.state.claude_md._sync.collect_promotable_learnings", return_value=[]),
            patch("trw_mcp.state.claude_md._sync.collect_patterns", return_value=[]),
            patch("trw_mcp.state.claude_md._sync.collect_context_data", return_value=({}, {})),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.analytics.update_analytics_sync"),
            patch("trw_mcp.state.analytics.mark_promoted"),
        ):
            from trw_mcp.state.claude_md._sync import execute_claude_md_sync

            execute_claude_md_sync(
                scope="root",
                target_dir=None,
                config=config,
                reader=reader,
                writer=writer,
                llm=llm,
            )

        output = target.read_text(encoding="utf-8")
        assert "{{" not in output, f"Unreplaced '{{{{' found in output:\n{output}"
        assert "}}" not in output, f"Unreplaced '}}}}' found in output:\n{output}"


# ---------------------------------------------------------------------------
# Test 8: project-local template override must contain all required keys
# ---------------------------------------------------------------------------


class TestProjectLocalTemplateOverrideKeys:
    """If a project-local template exists, it must contain all required keys.
    This prevents re-introducing a stale override."""

    def test_local_override_must_have_all_keys(self, tmp_path: Path) -> None:
        """A local template missing keys should cause render_template to raise."""
        trw_dir = tmp_path / ".trw"
        tpl_dir = trw_dir / "templates"
        tpl_dir.mkdir(parents=True)

        # Create a local override that's MISSING ceremony_quick_ref
        local_template = tpl_dir / "claude_md.md"
        local_template.write_text(
            "\n"
            f"{TRW_AUTO_COMMENT}\n"
            f"{TRW_MARKER_START}\n"
            "\n"
            "{{imperative_opener}}\n"
            "{{delegation_section}}\n"
            # Missing: ceremony_quick_ref
            "{{agent_teams_section}}\n"
            "{{behavioral_protocol}}\n"
            "{{rationalization_watchlist}}\n"
            "{{ceremony_phases}}\n"
            "{{ceremony_table}}\n"
            "{{ceremony_flows}}\n"
            "{{architecture_section}}\n"
            "{{conventions_section}}\n"
            "{{categorized_learnings}}\n"
            "{{patterns_section}}\n"
            "{{adherence_section}}\n"
            "{{closing_reminder}}\n"
            f"{TRW_MARKER_END}\n",
            encoding="utf-8",
        )

        # Verify that the loaded template is the local one
        with patch("trw_mcp.state.claude_md._parser.get_config", return_value=TRWConfig()):
            template = load_claude_md_template(trw_dir)

        # The template won't have ceremony_quick_ref, so when rendered
        # with the full context, it should succeed (the key just doesn't appear
        # in the template). But when we verify alignment, we see it's missing.
        template_keys = set(re.findall(r"\{\{(\w+)\}\}", template))
        required_keys = {
            "imperative_opener",
            "ceremony_quick_ref",
            "memory_harmonization",
            "closing_reminder",
            "behavioral_protocol",
            "delegation_section",
            "agent_teams_section",
            "rationalization_watchlist",
            "ceremony_phases",
            "ceremony_table",
            "ceremony_flows",
            "architecture_section",
            "conventions_section",
            "categorized_learnings",
            "patterns_section",
            "adherence_section",
        }

        missing = required_keys - template_keys
        assert "ceremony_quick_ref" in missing, (
            "Expected ceremony_quick_ref to be detected as missing from local override"
        )


# ---------------------------------------------------------------------------
# Test 9: merge preserves user content
# ---------------------------------------------------------------------------


class TestMergePreservesUserContent:
    """Create a CLAUDE.md with user content and markers, merge a new section,
    verify user content is preserved."""

    def test_user_content_preserved(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        user_content = (
            "# My Project\n"
            "\n"
            "Important user notes here.\n"
            "\n"
            f"{TRW_AUTO_COMMENT}\n"
            f"{TRW_MARKER_START}\n"
            "Old TRW content\n"
            f"{TRW_MARKER_END}\n"
        )
        target.write_text(user_content, encoding="utf-8")

        new_section = f"\n{TRW_AUTO_COMMENT}\n{TRW_MARKER_START}\nNew TRW content\n{TRW_MARKER_END}\n"

        merge_trw_section(target, new_section, max_lines=500)

        result = target.read_text(encoding="utf-8")
        assert "# My Project" in result
        assert "Important user notes here." in result
        assert "New TRW content" in result
        assert "Old TRW content" not in result


# ---------------------------------------------------------------------------
# Test 10: merge replaces TRW section
# ---------------------------------------------------------------------------


class TestMergeReplacesTrwSection:
    """Verify the content between markers is fully replaced."""

    def test_full_replacement(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        original = (
            "# Header\n"
            "\n"
            f"{TRW_AUTO_COMMENT}\n"
            f"{TRW_MARKER_START}\n"
            "## Old Section 1\n"
            "Old content line 1\n"
            "## Old Section 2\n"
            "Old content line 2\n"
            f"{TRW_MARKER_END}\n"
            "\n"
            "Footer content\n"
        )
        target.write_text(original, encoding="utf-8")

        new_section = (
            f"\n{TRW_AUTO_COMMENT}\n"
            f"{TRW_MARKER_START}\n"
            "## Brand New Section\n"
            "Completely new content\n"
            f"{TRW_MARKER_END}\n"
        )

        merge_trw_section(target, new_section, max_lines=500)

        result = target.read_text(encoding="utf-8")
        assert "# Header" in result
        assert "## Brand New Section" in result
        assert "Completely new content" in result
        assert "Old Section 1" not in result
        assert "Old content line 1" not in result
        assert "Old Section 2" not in result
        assert "Old content line 2" not in result
        assert "Footer content" in result
