"""Copilot instruction generation and merge tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._copilot import (
    _COPILOT_INSTRUCTIONS_PATH,
    _COPILOT_TRW_END_MARKER,
    _COPILOT_TRW_START_MARKER,
    _copilot_instructions_content,
    _smart_merge_instructions,
    generate_copilot_instructions,
)

from ._copilot_test_support import fake_git_repo  # noqa: F401


@pytest.mark.unit
class TestCopilotInstructions:
    """Test generate_copilot_instructions and smart-merge logic."""

    def test_instructions_created(self, fake_git_repo: Path) -> None:
        result = generate_copilot_instructions(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _COPILOT_INSTRUCTIONS_PATH).is_file()
        assert _COPILOT_INSTRUCTIONS_PATH in result["created"]

    def test_instructions_contains_trw_markers(self, fake_git_repo: Path) -> None:
        generate_copilot_instructions(fake_git_repo)
        content = (fake_git_repo / _COPILOT_INSTRUCTIONS_PATH).read_text()
        assert _COPILOT_TRW_START_MARKER in content
        assert _COPILOT_TRW_END_MARKER in content

    def test_instructions_contains_ceremony_protocol(self, fake_git_repo: Path) -> None:
        generate_copilot_instructions(fake_git_repo)
        content = (fake_git_repo / _COPILOT_INSTRUCTIONS_PATH).read_text()
        assert "TRW Framework Integration" in content
        assert "Session Protocol" in content
        assert "trw_session_start" in content
        assert "trw_learn" in content
        assert "trw_checkpoint" in content
        assert "trw_deliver" in content

    def test_instructions_smart_merge_preserves_user_content(self, fake_git_repo: Path) -> None:
        """Existing file with user content + TRW markers → user content preserved."""
        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        (fake_git_repo / ".github").mkdir(parents=True, exist_ok=True)

        user_before = "# My Custom Instructions\n\nDo NOT delete this.\n\n"
        user_after = "\n\n## My Other Section\n\nKeep this too.\n"
        original_trw = f"{_COPILOT_TRW_START_MARKER}\nold content here\n{_COPILOT_TRW_END_MARKER}"
        instructions_path.write_text(user_before + original_trw + user_after)

        result = generate_copilot_instructions(fake_git_repo)
        assert not result["errors"]

        content = instructions_path.read_text()
        assert "My Custom Instructions" in content
        assert "Do NOT delete this." in content
        assert "My Other Section" in content
        assert "Keep this too." in content
        assert "TRW Framework Integration" in content
        assert "old content here" not in content

    def test_instructions_fresh_file_when_no_markers(self, fake_git_repo: Path) -> None:
        """Existing file without markers gets TRW section appended."""
        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        (fake_git_repo / ".github").mkdir(parents=True, exist_ok=True)
        instructions_path.write_text("# User instructions only\n\nNo markers here.\n")

        result = generate_copilot_instructions(fake_git_repo)
        assert not result["errors"]

        content = instructions_path.read_text()
        assert "User instructions only" in content
        assert "No markers here." in content
        assert _COPILOT_TRW_START_MARKER in content
        assert _COPILOT_TRW_END_MARKER in content

    def test_instructions_force_overwrites(self, fake_git_repo: Path) -> None:
        """force=True completely replaces the file with TRW content."""
        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        (fake_git_repo / ".github").mkdir(parents=True, exist_ok=True)
        instructions_path.write_text("# I will be overwritten\nUser content here.\n")

        result = generate_copilot_instructions(fake_git_repo, force=True)
        assert not result["errors"]

        content = instructions_path.read_text()
        assert "I will be overwritten" not in content
        assert _COPILOT_TRW_START_MARKER in content
        assert "TRW Framework Integration" in content

    def test_instructions_updated_when_existing(self, fake_git_repo: Path) -> None:
        """Re-running on existing file marks it as updated, not created."""
        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        (fake_git_repo / ".github").mkdir(parents=True, exist_ok=True)
        instructions_path.write_text("# Existing\n")

        result = generate_copilot_instructions(fake_git_repo)
        assert _COPILOT_INSTRUCTIONS_PATH in result["updated"]
        assert _COPILOT_INSTRUCTIONS_PATH not in result["created"]

    def test_instructions_creates_github_dir(self, fake_git_repo: Path) -> None:
        """The .github directory is created if it doesn't exist."""
        result = generate_copilot_instructions(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / ".github").is_dir()


@pytest.mark.unit
class TestSmartMergeInstructions:
    """Unit tests for the _smart_merge_instructions helper."""

    def test_merge_replaces_trw_section(self) -> None:
        existing = f"before\n{_COPILOT_TRW_START_MARKER}\nold\n{_COPILOT_TRW_END_MARKER}\nafter"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert "old" not in merged
        assert "new" in merged
        assert "before" in merged
        assert "after" in merged

    def test_merge_appends_when_no_markers(self) -> None:
        existing = "user content only"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew section\n{_COPILOT_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert "user content only" in merged
        assert "new section" in merged

    def test_merge_empty_existing(self) -> None:
        new_content = f"{_COPILOT_TRW_START_MARKER}\nstuff\n{_COPILOT_TRW_END_MARKER}"
        merged = _smart_merge_instructions("", new_content)
        assert "stuff" in merged

    def test_copilot_instructions_content_has_markers(self) -> None:
        content = _copilot_instructions_content()
        assert content.startswith(_COPILOT_TRW_START_MARKER)
        assert _COPILOT_TRW_END_MARKER in content

    def test_merge_end_before_start_appends(self) -> None:
        """End marker before start marker is treated as corrupted — append instead."""
        existing = f"user\n{_COPILOT_TRW_END_MARKER}\nmiddle\n{_COPILOT_TRW_START_MARKER}"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert merged.endswith(new_content + "\n")
        assert "user" in merged

    def test_merge_single_start_marker_appends(self) -> None:
        """Only start marker present (no end) — treated as no valid pair, append."""
        existing = f"user\n{_COPILOT_TRW_START_MARKER}\npartial"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert merged.endswith(new_content + "\n")

    def test_merge_single_end_marker_appends(self) -> None:
        """Only end marker present — treated as no valid pair, append."""
        existing = f"user\n{_COPILOT_TRW_END_MARKER}\nstuff"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert merged.endswith(new_content + "\n")

    def test_merge_idempotent(self, fake_git_repo: Path) -> None:
        """Running generate_copilot_instructions twice marks second as preserved."""
        result1 = generate_copilot_instructions(fake_git_repo)
        assert result1.get("created") or result1.get("updated")
        result2 = generate_copilot_instructions(fake_git_repo)
        assert result2.get("preserved")
