"""Tests for tag inference behavior and its learning-tool integration."""

from __future__ import annotations

from pathlib import Path

from tests._tools_learning_shared import _entries_dir, _get_tools
from trw_mcp.state.persistence import FileStateReader


class TestInferTopicTags:
    """QUAL-018 FR03: Tag inference from summary keywords."""

    def test_infers_testing_tag(self) -> None:
        """Keywords like 'pytest' and 'fixture' map to 'testing'."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("pytest fixture fails on Windows", [])
        assert "testing" in tags

    def test_infers_multiple_tags(self) -> None:
        """Multiple distinct topic keywords produce multiple tags (up to 3)."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("API endpoint security auth token", [])
        assert len(tags) <= 3
        assert "api" in tags
        assert "security" in tags

    def test_no_duplicates_with_existing(self) -> None:
        """Tags already present in existing_tags are not re-inferred."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("pytest coverage report", ["testing"])
        assert "testing" not in tags

    def test_case_insensitive_dedup(self) -> None:
        """Dedup is case-insensitive: existing 'Testing' suppresses 'testing'."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("Test coverage", ["Testing"])
        # 'test' maps to 'testing', which matches existing 'Testing' (case-insensitive)
        assert "testing" not in tags

    def test_max_three_tags(self) -> None:
        """At most 3 tags are inferred regardless of how many keywords match."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("test api security deploy config debug database", [])
        assert len(tags) <= 3

    def test_empty_summary(self) -> None:
        """Empty summary produces no tags."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("", [])
        assert tags == []

    def test_no_matching_keywords(self) -> None:
        """Summary with no recognized keywords produces no tags."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("xyzzy foobar quux", [])
        assert tags == []

    def test_graceful_on_none_existing(self) -> None:
        """None for existing_tags is handled gracefully."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("test something", None)
        assert isinstance(tags, list)
        assert "testing" in tags

    def test_database_keywords(self) -> None:
        """Database-related keywords map to 'database' tag."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("sqlite migration query performance", [])
        assert "database" in tags
        assert "performance" in tags

    def test_hyphenated_and_slashed_tokens(self) -> None:
        """Tokens separated by hyphens, underscores, and slashes are split."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("api-endpoint/security_auth", [])
        assert "api" in tags
        assert "security" in tags

    def test_no_duplicate_same_tag_from_multiple_keywords(self) -> None:
        """Multiple keywords mapping to same tag produce only one instance (FR05)."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("test tests pytest coverage", [])
        assert tags.count("testing") == 1

    def test_documentation_keywords(self) -> None:
        """Documentation keywords are inferred correctly."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("update prd readme docs", [])
        assert "documentation" in tags

    def test_pricing_keywords(self) -> None:
        """Cost/pricing keywords map to 'pricing' tag (PRD acceptance example)."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("cost_tracker renamed", ["gotcha"])
        assert "pricing" in tags

    def test_rate_limiting_keywords(self) -> None:
        """Rate/limit keywords map to 'rate-limiting' tag (PRD acceptance example)."""
        from trw_mcp.state.analytics import infer_topic_tags

        tags = infer_topic_tags("api rate_limit exceeded", [])
        assert "api" in tags
        assert "rate-limiting" in tags

    def test_exception_safety(self) -> None:
        """Non-string or pathological input returns empty list, never raises."""
        from trw_mcp.state.analytics import infer_topic_tags

        # type: ignore intentional — testing exception safety
        assert infer_topic_tags(None, []) == []  # type: ignore[arg-type]
        assert infer_topic_tags(123, []) == []  # type: ignore[arg-type]


class TestTagInferenceIntegration:
    """QUAL-018 FR03: Tag inference is wired into learning save paths."""

    def test_trw_learn_infers_tags(self, tmp_path: Path, reader: FileStateReader) -> None:
        """trw_learn auto-infers tags from summary when storing."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="pytest fixture fails on Windows",
            detail="Windows path separator causes fixture to break",
            tags=["gotcha"],
            impact=0.7,
        )
        assert result["status"] == "recorded"

        # Verify tags were enriched in the YAML backup
        entries_dir = _entries_dir(tmp_path)
        yaml_files = list(entries_dir.glob("*.yaml"))
        assert len(yaml_files) == 1
        data = reader.read_yaml(yaml_files[0])
        tags = data.get("tags", [])
        # Original tag should be present
        assert "gotcha" in tags
        # Inferred tag 'testing' should be present from 'pytest' + 'fixture'
        assert "testing" in tags

    def test_trw_learn_no_duplicate_tags(self, tmp_path: Path, reader: FileStateReader) -> None:
        """trw_learn does not add inferred tags that already exist (FR05)."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="pytest fixture fails",
            detail="Details here",
            tags=["testing"],  # Already has 'testing'
            impact=0.5,
        )
        assert result["status"] == "recorded"

        entries_dir = _entries_dir(tmp_path)
        yaml_files = list(entries_dir.glob("*.yaml"))
        assert len(yaml_files) == 1
        data = reader.read_yaml(yaml_files[0])
        tags = data.get("tags", [])
        # 'testing' should appear exactly once (not duplicated)
        assert tags.count("testing") == 1
