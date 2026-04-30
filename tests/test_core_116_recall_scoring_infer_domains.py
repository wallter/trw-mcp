"""Domain-inference coverage for CORE-116 recall scoring."""

from __future__ import annotations


class TestInferDomains:
    """Tests for infer_domains() — prefix mapping, security, fallback."""

    def test_infer_domains_returns_set(self) -> None:
        """Return type is set[str]."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(file_paths=["src/auth/middleware.py"])
        assert isinstance(result, set)

    def test_infer_domains_prefix_mapping(self) -> None:
        """Configurable prefix mapping resolves to explicit domain labels."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(
            file_paths=["backend/payments/x.py"],
            path_domain_map={"backend/payments": "payments"},
        )
        assert "payments" in result

    def test_infer_domains_fallback_no_mapping(self) -> None:
        """Without mapping, directory stems are used as fallback."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(file_paths=["backend/payments/x.py"])
        assert "backend" in result
        assert "payments" in result

    def test_infer_domains_security_traversal(self) -> None:
        """Path traversal '../../etc/passwd' does not produce '..' or 'etc'."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(file_paths=["../../etc/passwd"])
        assert ".." not in result
        for domain in result:
            assert ".." not in domain

    def test_infer_domains_absolute_path_stripped(self) -> None:
        """Absolute path '/backend/payments/x.py' is treated as relative."""
        from trw_mcp.scoring._recall import infer_domains

        result_abs = infer_domains(
            file_paths=["/backend/payments/x.py"],
            path_domain_map={"backend/payments": "payments"},
        )
        result_rel = infer_domains(
            file_paths=["backend/payments/x.py"],
            path_domain_map={"backend/payments": "payments"},
        )
        assert result_abs == result_rel

    def test_infer_domains_prefix_greedy_match(self) -> None:
        """Longer prefix wins over shorter prefix."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(
            file_paths=["backend/payments/stripe/handler.py"],
            path_domain_map={
                "backend": "backend-general",
                "backend/payments": "payments",
                "backend/payments/stripe": "stripe",
            },
        )
        assert "stripe" in result
        assert "backend-general" not in result
        assert "payments" not in result

    def test_infer_domains_empty_input(self) -> None:
        """infer_domains(file_paths=[]) returns empty set."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(file_paths=[])
        assert result == set()

    def test_infer_domains_none_input(self) -> None:
        """infer_domains() with no args returns empty set."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains()
        assert isinstance(result, set)
        assert len(result) == 0

    def test_infer_domains_deprecated_modified_files(self) -> None:
        """Deprecated modified_files param still works as alias for file_paths."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(modified_files=["backend/payments/x.py"])
        assert "payments" in result
        assert "backend" in result

    def test_infer_domains_prefix_map_traversal_dropped(self) -> None:
        """Prefix map entries with '..' are silently dropped."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(
            file_paths=["backend/payments/x.py"],
            path_domain_map={"../etc": "hacked", "backend/payments": "payments"},
        )
        assert "hacked" not in result
        assert "payments" in result

    def test_infer_domains_null_byte_rejected(self) -> None:
        """Paths containing null bytes are sanitized out."""
        from trw_mcp.scoring._recall import infer_domains

        result = infer_domains(file_paths=["backend/payments\x00evil/x.py"])
        assert "evil" not in result
