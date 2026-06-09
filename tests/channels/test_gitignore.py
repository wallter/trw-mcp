"""Tests for _gitignore.py — managed .gitignore section (FR21, PRD-DIST-2400)."""

from __future__ import annotations

import pytest

from trw_mcp.channels._gitignore import (
    GITIGNORE_BEGIN,
    GITIGNORE_END,
    _get_managed_section,
    add_gitignore_entry,
    list_gitignore_entries,
    remove_gitignore_entry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path):
    """Minimal repository root directory."""
    return tmp_path


# ---------------------------------------------------------------------------
# _get_managed_section
# ---------------------------------------------------------------------------


class TestGetManagedSection:
    def test_returns_none_when_absent(self):
        content = "foo\nbar\n"
        assert _get_managed_section(content) is None

    def test_returns_none_when_only_begin(self):
        content = f"foo\n{GITIGNORE_BEGIN}\nbar\n"
        assert _get_managed_section(content) is None

    def test_returns_indices(self):
        content = f"header\n{GITIGNORE_BEGIN}\nentry1\n{GITIGNORE_END}\nfooter\n"
        result = _get_managed_section(content)
        assert result is not None
        begin_idx, end_idx = result
        lines = content.splitlines()
        assert lines[begin_idx] == GITIGNORE_BEGIN
        assert lines[end_idx] == GITIGNORE_END

    def test_end_before_begin_returns_none(self):
        content = f"{GITIGNORE_END}\n{GITIGNORE_BEGIN}\n"
        assert _get_managed_section(content) is None


# ---------------------------------------------------------------------------
# add_gitignore_entry
# ---------------------------------------------------------------------------


class TestAddGitignoreEntry:
    def test_creates_file_and_section_when_absent(self, repo):
        result = add_gitignore_entry(repo, ".trw/telemetry/channel-events.jsonl")
        assert result is True
        gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
        assert GITIGNORE_BEGIN in gitignore
        assert GITIGNORE_END in gitignore
        assert ".trw/telemetry/channel-events.jsonl" in gitignore

    def test_idempotent_add_returns_false(self, repo):
        add_gitignore_entry(repo, "entry.lock")
        result = add_gitignore_entry(repo, "entry.lock")
        assert result is False

    def test_entry_inside_section_after_add(self, repo):
        add_gitignore_entry(repo, "*.tmp")
        entries = list_gitignore_entries(repo)
        assert "*.tmp" in entries

    def test_content_outside_section_byte_identical(self, repo):
        gitignore = repo / ".gitignore"
        existing = "# user content\n*.pyc\n__pycache__/\n"
        gitignore.write_text(existing, encoding="utf-8")

        add_gitignore_entry(repo, "new-entry")

        after = gitignore.read_text(encoding="utf-8")
        # Everything before the managed section is unchanged
        assert after.startswith(existing.rstrip())

    def test_multiple_entries_preserved_in_order(self, repo):
        add_gitignore_entry(repo, "alpha")
        add_gitignore_entry(repo, "beta")
        add_gitignore_entry(repo, "gamma")
        entries = list_gitignore_entries(repo)
        assert entries == ["alpha", "beta", "gamma"]

    def test_adds_to_existing_section(self, repo):
        gitignore = repo / ".gitignore"
        gitignore.write_text(
            f"{GITIGNORE_BEGIN}\nexisting-entry\n{GITIGNORE_END}\n",
            encoding="utf-8",
        )
        add_gitignore_entry(repo, "new-entry")
        entries = list_gitignore_entries(repo)
        assert "existing-entry" in entries
        assert "new-entry" in entries

    def test_content_before_and_after_section_unchanged(self, repo):
        gitignore = repo / ".gitignore"
        original = f"before\n{GITIGNORE_BEGIN}\n{GITIGNORE_END}\nafter\n"
        gitignore.write_text(original, encoding="utf-8")
        add_gitignore_entry(repo, "inserted")
        after_text = gitignore.read_text(encoding="utf-8")
        lines = after_text.splitlines()
        assert lines[0] == "before"
        assert "after" in lines


# ---------------------------------------------------------------------------
# remove_gitignore_entry
# ---------------------------------------------------------------------------


class TestRemoveGitignoreEntry:
    def test_removes_existing_entry(self, repo):
        add_gitignore_entry(repo, "to-remove")
        result = remove_gitignore_entry(repo, "to-remove")
        assert result is True
        assert "to-remove" not in list_gitignore_entries(repo)

    def test_returns_false_when_entry_absent(self, repo):
        add_gitignore_entry(repo, "something")
        result = remove_gitignore_entry(repo, "non-existent")
        assert result is False

    def test_returns_false_when_no_section(self, repo):
        gitignore = repo / ".gitignore"
        gitignore.write_text("# plain\n*.log\n", encoding="utf-8")
        result = remove_gitignore_entry(repo, "*.log")
        assert result is False

    def test_section_preserved_when_empty_after_remove(self, repo):
        add_gitignore_entry(repo, "only-entry")
        remove_gitignore_entry(repo, "only-entry")
        content = (repo / ".gitignore").read_text(encoding="utf-8")
        assert GITIGNORE_BEGIN in content
        assert GITIGNORE_END in content

    def test_other_entries_preserved_after_remove(self, repo):
        add_gitignore_entry(repo, "keep1")
        add_gitignore_entry(repo, "remove-me")
        add_gitignore_entry(repo, "keep2")
        remove_gitignore_entry(repo, "remove-me")
        entries = list_gitignore_entries(repo)
        assert entries == ["keep1", "keep2"]

    def test_content_outside_section_byte_identical_after_remove(self, repo):
        gitignore = repo / ".gitignore"
        original_header = "# top of file\n*.pyc\n"
        gitignore.write_text(
            original_header + f"{GITIGNORE_BEGIN}\nentry\n{GITIGNORE_END}\n",
            encoding="utf-8",
        )
        remove_gitignore_entry(repo, "entry")
        after = gitignore.read_text(encoding="utf-8")
        assert after.startswith(original_header)


# ---------------------------------------------------------------------------
# list_gitignore_entries
# ---------------------------------------------------------------------------


class TestListGitignoreEntries:
    def test_empty_when_no_file(self, repo):
        assert list_gitignore_entries(repo) == []

    def test_empty_when_no_section(self, repo):
        (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
        assert list_gitignore_entries(repo) == []

    def test_empty_section_returns_empty_list(self, repo):
        (repo / ".gitignore").write_text(
            f"{GITIGNORE_BEGIN}\n{GITIGNORE_END}\n", encoding="utf-8"
        )
        assert list_gitignore_entries(repo) == []

    def test_returns_entries_in_order(self, repo):
        add_gitignore_entry(repo, "a")
        add_gitignore_entry(repo, "b")
        add_gitignore_entry(repo, "c")
        assert list_gitignore_entries(repo) == ["a", "b", "c"]
