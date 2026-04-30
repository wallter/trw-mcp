"""Tests for Gemini instructions generation and smart-merge behavior."""

from __future__ import annotations

import pytest

from tests._gemini_test_support import fake_git_repo
from trw_mcp.bootstrap._gemini import (
    _GEMINI_MD_PATH,
    _GEMINI_TRW_END_MARKER,
    _GEMINI_TRW_START_MARKER,
    _gemini_instructions_content,
    _smart_merge_instructions,
    generate_gemini_instructions,
)


@pytest.mark.unit
class TestGeminiInstructions:
    """Test generate_gemini_instructions and smart-merge logic."""

    def test_instructions_created(self, fake_git_repo) -> None:
        result = generate_gemini_instructions(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _GEMINI_MD_PATH).is_file()
        assert _GEMINI_MD_PATH in result["created"]

    def test_instructions_contains_trw_markers(self, fake_git_repo) -> None:
        generate_gemini_instructions(fake_git_repo)
        content = (fake_git_repo / _GEMINI_MD_PATH).read_text()
        assert _GEMINI_TRW_START_MARKER in content
        assert _GEMINI_TRW_END_MARKER in content

    def test_instructions_contains_ceremony_protocol(self, fake_git_repo) -> None:
        generate_gemini_instructions(fake_git_repo)
        content = (fake_git_repo / _GEMINI_MD_PATH).read_text()
        assert "TRW Framework Integration" in content
        assert "Session Protocol" in content
        assert "trw_session_start" in content
        assert "trw_learn" in content
        assert "trw_checkpoint" in content
        assert "trw_deliver" in content

    def test_instructions_mentions_gemini_cli(self, fake_git_repo) -> None:
        """Content should reference Gemini CLI, not Claude Code."""
        generate_gemini_instructions(fake_git_repo)
        content = (fake_git_repo / _GEMINI_MD_PATH).read_text()
        assert "Gemini CLI" in content
        assert "mcp_trw_" in content

    def test_instructions_smart_merge_preserves_user_content(self, fake_git_repo) -> None:
        """Existing file with user content + TRW markers -> user content preserved."""
        instructions_path = fake_git_repo / _GEMINI_MD_PATH

        user_before = "# My Custom Project\n\nDo NOT delete this.\n\n"
        user_after = "\n\n## My Other Section\n\nKeep this too.\n"
        original_trw = f"{_GEMINI_TRW_START_MARKER}\nold content here\n{_GEMINI_TRW_END_MARKER}"
        instructions_path.write_text(user_before + original_trw + user_after)

        result = generate_gemini_instructions(fake_git_repo)
        assert not result["errors"]

        content = instructions_path.read_text()
        assert "My Custom Project" in content
        assert "Do NOT delete this." in content
        assert "My Other Section" in content
        assert "Keep this too." in content
        assert "TRW Framework Integration" in content
        assert "old content here" not in content

    def test_instructions_fresh_file_when_no_markers(self, fake_git_repo) -> None:
        """Existing file without markers gets TRW section appended."""
        instructions_path = fake_git_repo / _GEMINI_MD_PATH
        instructions_path.write_text("# User instructions only\n\nNo markers here.\n")

        result = generate_gemini_instructions(fake_git_repo)
        assert not result["errors"]

        content = instructions_path.read_text()
        assert "User instructions only" in content
        assert "No markers here." in content
        assert _GEMINI_TRW_START_MARKER in content
        assert _GEMINI_TRW_END_MARKER in content

    def test_instructions_force_overwrites(self, fake_git_repo) -> None:
        """force=True completely replaces the file with TRW content."""
        instructions_path = fake_git_repo / _GEMINI_MD_PATH
        instructions_path.write_text("# I will be overwritten\nUser content here.\n")

        result = generate_gemini_instructions(fake_git_repo, force=True)
        assert not result["errors"]

        content = instructions_path.read_text()
        assert "I will be overwritten" not in content
        assert _GEMINI_TRW_START_MARKER in content
        assert "TRW Framework Integration" in content

    def test_instructions_updated_when_existing(self, fake_git_repo) -> None:
        """Re-running on existing file marks it as updated, not created."""
        instructions_path = fake_git_repo / _GEMINI_MD_PATH
        instructions_path.write_text("# Existing\n")

        result = generate_gemini_instructions(fake_git_repo)
        assert _GEMINI_MD_PATH in result["updated"]
        assert _GEMINI_MD_PATH not in result["created"]


@pytest.mark.unit
class TestGeminiSmartMerge:
    """Unit tests for the _smart_merge_instructions helper."""

    def test_merge_replaces_trw_section(self) -> None:
        existing = f"before\n{_GEMINI_TRW_START_MARKER}\nold\n{_GEMINI_TRW_END_MARKER}\nafter"
        new_content = f"{_GEMINI_TRW_START_MARKER}\nnew\n{_GEMINI_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert "old" not in merged
        assert "new" in merged
        assert "before" in merged
        assert "after" in merged

    def test_merge_appends_when_no_markers(self) -> None:
        existing = "user content only"
        new_content = f"{_GEMINI_TRW_START_MARKER}\nnew section\n{_GEMINI_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert "user content only" in merged
        assert "new section" in merged

    def test_merge_empty_existing(self) -> None:
        new_content = f"{_GEMINI_TRW_START_MARKER}\nstuff\n{_GEMINI_TRW_END_MARKER}"
        merged = _smart_merge_instructions("", new_content)
        assert "stuff" in merged

    def test_gemini_instructions_content_has_markers(self) -> None:
        content = _gemini_instructions_content()
        assert content.startswith(_GEMINI_TRW_START_MARKER)
        assert _GEMINI_TRW_END_MARKER in content

    def test_merge_end_before_start_appends(self) -> None:
        """End marker before start marker is treated as corrupted — append instead."""
        existing = f"user\n{_GEMINI_TRW_END_MARKER}\nmiddle\n{_GEMINI_TRW_START_MARKER}"
        new_content = f"{_GEMINI_TRW_START_MARKER}\nnew\n{_GEMINI_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert merged.endswith(new_content + "\n")
        assert "user" in merged

    def test_merge_single_start_marker_appends(self) -> None:
        """Only start marker present — treated as no valid pair, append."""
        existing = f"user\n{_GEMINI_TRW_START_MARKER}\npartial"
        new_content = f"{_GEMINI_TRW_START_MARKER}\nnew\n{_GEMINI_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert merged.endswith(new_content + "\n")

    def test_merge_single_end_marker_appends(self) -> None:
        """Only end marker present — treated as no valid pair, append."""
        existing = f"user\n{_GEMINI_TRW_END_MARKER}\nstuff"
        new_content = f"{_GEMINI_TRW_START_MARKER}\nnew\n{_GEMINI_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert merged.endswith(new_content + "\n")

    def test_merge_idempotent(self, fake_git_repo) -> None:
        """Running generate_gemini_instructions twice marks second as preserved."""
        result1 = generate_gemini_instructions(fake_git_repo)
        assert result1.get("created") or result1.get("updated")
        result2 = generate_gemini_instructions(fake_git_repo)
        assert result2.get("preserved")
