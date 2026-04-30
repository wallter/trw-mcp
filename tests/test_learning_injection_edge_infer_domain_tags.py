"""Edge-case tests for learning_injection domain-tag inference."""

from __future__ import annotations


class TestInferDomainTagsEdge:
    """Edge cases for path-to-tag inference."""

    def test_unrecognized_path_returns_empty(self) -> None:
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["some/random/unknown/file.py"])
        assert tags == set()

    def test_case_insensitive_matching(self) -> None:
        """Path components are lowered before lookup."""
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["Backend/Routers/Admin.py"])
        assert "backend" in tags
        assert "admin" in tags
        assert "api" in tags

    def test_extension_stripped_for_stem_match(self) -> None:
        """File 'auth.ts' should match 'auth' stem."""
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["src/auth.ts"])
        assert "auth" in tags
        assert "security" in tags

    def test_directory_without_extension_matches(self) -> None:
        """A path component without a dot is used as-is for matching."""
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["middleware/"])
        assert "middleware" in tags
        assert "api" in tags

    def test_deeply_nested_path_all_components_checked(self) -> None:
        """Every component in a deep path is checked against the map."""
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags = infer_domain_tags(["backend/models/database/migrations/alembic/env.py"])
        assert "backend" in tags
        assert "models" in tags
        assert "database" in tags
        assert "alembic" in tags
        assert "migration" in tags

    def test_trw_memory_underscore_variant(self) -> None:
        """Both trw-memory and trw_memory map to memory tags."""
        from trw_mcp.state.learning_injection import infer_domain_tags

        tags_hyphen = infer_domain_tags(["trw-memory/src/"])
        tags_underscore = infer_domain_tags(["trw_memory/src/"])
        assert "memory" in tags_hyphen
        assert "memory" in tags_underscore
        assert tags_hyphen == tags_underscore
