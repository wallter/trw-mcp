"""Tests for validation v2 traceability language support."""

from __future__ import annotations

from trw_mcp.state.validation import score_traceability_v2
from trw_mcp.state.validation.prd_quality import _KNOWN_TEST_PATTERNS, _TEST_REF_RE

from ._validation_v2_support import _FILLED_PRD, _MINIMAL_FRONTMATTER


class TestTestRefsRegex:
    """Unit tests for the _TEST_REF_RE pattern."""

    def test_python_prefix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`test_module.py`") == ["`test_module.py`"]

    def test_python_prefix_with_pytest_node_matches(self) -> None:
        assert _TEST_REF_RE.findall("`test_api.py::test_create`") == ["`test_api.py::test_create`"]

    def test_python_prefix_underscore_matches(self) -> None:
        assert _TEST_REF_RE.findall("`test_validation_v2.py`") == ["`test_validation_v2.py`"]

    def test_typescript_test_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`Component.test.tsx`") == ["`Component.test.tsx`"]

    def test_typescript_spec_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`api.spec.ts`") == ["`api.spec.ts`"]

    def test_javascript_test_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`utils.test.js`") == ["`utils.test.js`"]

    def test_javascript_spec_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`service.spec.js`") == ["`service.spec.js`"]

    def test_go_test_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`handler_test.go`") == ["`handler_test.go`"]

    def test_go_test_suffix_with_path_matches(self) -> None:
        assert _TEST_REF_RE.findall("`internal/handler_test.go`") == ["`internal/handler_test.go`"]

    def test_java_test_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`UserServiceTest.java`") == ["`UserServiceTest.java`"]

    def test_java_tests_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`UserServiceTests.java`") == ["`UserServiceTests.java`"]

    def test_ruby_spec_suffix_matches(self) -> None:
        assert _TEST_REF_RE.findall("`user_spec.rb`") == ["`user_spec.rb`"]

    def test_tests_dir_matches(self) -> None:
        assert _TEST_REF_RE.findall("`tests/integration.rs`") == ["`tests/integration.rs`"]

    def test_test_dir_singular_matches(self) -> None:
        assert _TEST_REF_RE.findall("`test/helpers.py`") == ["`test/helpers.py`"]

    def test_plain_python_file_no_match(self) -> None:
        assert _TEST_REF_RE.findall("`prd_quality.py`") == []

    def test_plain_typescript_file_no_match(self) -> None:
        assert _TEST_REF_RE.findall("`router.ts`") == []

    def test_plain_go_file_no_match(self) -> None:
        assert _TEST_REF_RE.findall("`main.go`") == []

    def test_plain_java_file_no_match(self) -> None:
        assert _TEST_REF_RE.findall("`UserService.java`") == []

    def test_config_file_no_match(self) -> None:
        assert _TEST_REF_RE.findall("`config.py`") == []

    def test_server_ts_no_match(self) -> None:
        assert _TEST_REF_RE.findall("`server.ts`") == []

    def test_mixed_language_matrix_all_counted(self) -> None:
        matrix_section = (
            "| FR01 | US-001 | `src/validation.py` | `test_api.py::test_create` | Pending |\n"
            "| FR02 | US-001 | `Dashboard.tsx` | `Dashboard.test.tsx` | Pending |\n"
            "| FR03 | US-002 | `handler.go` | `handler_test.go` | Pending |\n"
        )
        matches = _TEST_REF_RE.findall(matrix_section)
        assert len(matches) == 3
        assert "`test_api.py::test_create`" in matches
        assert "`Dashboard.test.tsx`" in matches
        assert "`handler_test.go`" in matches

    def test_non_test_files_not_counted_in_mixed_matrix(self) -> None:
        matrix_section = "| FR01 | `src/validation.py` | `test_api.py` | Pending |\n"
        matches = _TEST_REF_RE.findall(matrix_section)
        assert "`src/validation.py`" not in matches
        assert "`test_api.py`" in matches


class TestKnownTestPatterns:
    """Verify _KNOWN_TEST_PATTERNS constant is populated."""

    def test_constant_has_expected_languages(self) -> None:
        assert "python" in _KNOWN_TEST_PATTERNS
        assert "typescript" in _KNOWN_TEST_PATTERNS
        assert "go" in _KNOWN_TEST_PATTERNS
        assert "java" in _KNOWN_TEST_PATTERNS
        assert "ruby" in _KNOWN_TEST_PATTERNS
        assert "rust" in _KNOWN_TEST_PATTERNS

    def test_constant_values_are_strings(self) -> None:
        for lang, description in _KNOWN_TEST_PATTERNS.items():
            assert isinstance(description, str) and description, (
                f"Language '{lang}' has empty or non-string description"
            )


class TestTraceabilityV2LanguageAgnostic:
    """Integration tests: score_traceability_v2 with non-Python test refs."""

    _TS_PRD = (
        _MINIMAL_FRONTMATTER
        + """\
# PRD-TS-001: TypeScript PRD

## 1. Problem Statement
TypeScript frontend needs validation.

## 2. Goals & Non-Goals
### Goals
- Add form validation

## 3. User Stories
### US-001
**As a** user **I want** validation **So that** errors are clear.

## 4. Functional Requirements
### PRD-TS-001-FR01: Validate Form
**Priority**: Must Have
**Description**: Validate all form fields on submit.

## 5. Non-Functional Requirements
- Response under 100ms

## 6. Technical Approach
Uses React Hook Form.

## 7. Test Strategy
- Component.test.tsx

## 8. Rollout Plan
Phase 1: Implement.

## 9. Success Metrics
| Metric | Target |
|--------|--------|
| Error rate | <1% |

## 10. Dependencies & Risks
No blockers.

## 11. Open Questions
None.

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | US-001 | `src/Form.tsx` | `Component.test.tsx` | Pending |
"""
    )

    def test_typescript_prd_matrix_score_positive(self) -> None:
        frontmatter = {
            "traceability": {
                "implements": ["REQ-001"],
                "depends_on": [],
                "enables": [],
            }
        }
        result = score_traceability_v2(frontmatter, self._TS_PRD)
        assert result.details["matrix_score"] > 0.0, "TypeScript test refs not counted — matrix_score was 0"

    def test_python_prd_backward_compat_score_unchanged(self) -> None:
        frontmatter = {
            "traceability": {
                "implements": ["REQ-001"],
                "depends_on": ["PRD-CORE-007"],
                "enables": ["PRD-CORE-009"],
            }
        }
        result = score_traceability_v2(frontmatter, _FILLED_PRD)
        assert result.score >= 13.0
        assert result.name == "traceability"

    def test_go_prd_test_refs_counted(self) -> None:
        frontmatter: dict[str, object] = {
            "traceability": {
                "implements": [],
                "depends_on": [],
                "enables": [],
            }
        }
        go_content = """\
## 12. Traceability Matrix

| Requirement | Implementation | Test | Status |
|-------------|----------------|------|--------|
| FR01 | `handler.go` | `handler_test.go` | Pending |
| FR02 | `service.go` | `service_test.go` | Pending |
"""
        result = score_traceability_v2(frontmatter, go_content)
        assert result.details["matrix_score"] > 0.0
