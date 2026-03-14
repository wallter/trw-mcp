"""PRD-FIX-055: Language-Agnostic test_refs Regex — Dedicated Test Suite.

This file is the canonical acceptance test for PRD-FIX-055.  It covers every
FR and every language convention listed in the PRD traceability matrix.

Kept separate from test_validation_v2.py so it can be run in isolation and so
the fix is traceable to a single test file per the PRD requirement.

Run in isolation (fast):
    cd trw-mcp
    .venv/bin/python -m pytest tests/test_fix055_traceability_lang.py -v
"""

from __future__ import annotations

import re

import pytest

from trw_mcp.state.validation.prd_quality import (
    _KNOWN_TEST_PATTERNS,
    _TEST_REF_RE,
    score_traceability_v2,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _match(snippet: str) -> list[str]:
    """Return all _TEST_REF_RE matches in *snippet*."""
    return _TEST_REF_RE.findall(snippet)


def _has_match(snippet: str) -> bool:
    return bool(_TEST_REF_RE.search(snippet))


# ---------------------------------------------------------------------------
# FR01 — Python (prefix convention) — backward compat (FR02)
# ---------------------------------------------------------------------------


class TestPythonConvention:
    """FR01/FR02: Python test_* prefix naming convention."""

    def test_basic_python_file(self) -> None:
        assert "`test_module.py`" in _match("`test_module.py`")

    def test_python_with_underscore_words(self) -> None:
        assert "`test_validation_v2.py`" in _match("`test_validation_v2.py`")

    def test_python_with_pytest_node_id(self) -> None:
        """pytest node IDs (module::function) must match."""
        assert "`test_api.py::test_create`" in _match("`test_api.py::test_create`")

    def test_python_longer_prefix_still_matches(self) -> None:
        assert "`test_tools_learning.py`" in _match("`test_tools_learning.py`")

    def test_python_non_test_file_no_match(self) -> None:
        """FR03: prd_quality.py must NOT match."""
        assert not _has_match("`prd_quality.py`")

    def test_python_config_no_match(self) -> None:
        assert not _has_match("`config.py`")

    def test_python_main_no_match(self) -> None:
        assert not _has_match("`main.py`")


# ---------------------------------------------------------------------------
# FR01 — TypeScript / JavaScript (suffix conventions)
# ---------------------------------------------------------------------------


class TestTypeScriptConvention:
    """FR01: TypeScript .test.ts / .test.tsx / .spec.ts / .spec.tsx naming."""

    def test_ts_test_suffix(self) -> None:
        assert "`Component.test.ts`" in _match("`Component.test.ts`")

    def test_tsx_test_suffix(self) -> None:
        assert "`Dashboard.test.tsx`" in _match("`Dashboard.test.tsx`")

    def test_ts_spec_suffix(self) -> None:
        assert "`api.spec.ts`" in _match("`api.spec.ts`")

    def test_tsx_spec_suffix(self) -> None:
        assert "`Form.spec.tsx`" in _match("`Form.spec.tsx`")

    def test_js_test_suffix(self) -> None:
        assert "`utils.test.js`" in _match("`utils.test.js`")

    def test_js_spec_suffix(self) -> None:
        assert "`service.spec.js`" in _match("`service.spec.js`")

    def test_jsx_test_suffix(self) -> None:
        assert "`Button.test.jsx`" in _match("`Button.test.jsx`")

    def test_plain_ts_no_match(self) -> None:
        """FR03: router.ts is not a test file."""
        assert not _has_match("`router.ts`")

    def test_plain_tsx_no_match(self) -> None:
        assert not _has_match("`SiteHeader.tsx`")

    def test_plain_js_no_match(self) -> None:
        assert not _has_match("`server.js`")


# ---------------------------------------------------------------------------
# FR01 — Go (suffix convention)
# ---------------------------------------------------------------------------


class TestGoConvention:
    """FR01: Go *_test.go suffix naming."""

    def test_go_basic_test_suffix(self) -> None:
        assert "`handler_test.go`" in _match("`handler_test.go`")

    def test_go_service_test_suffix(self) -> None:
        assert "`service_test.go`" in _match("`service_test.go`")

    def test_go_with_path(self) -> None:
        assert "`internal/handler_test.go`" in _match("`internal/handler_test.go`")

    def test_go_plain_file_no_match(self) -> None:
        """FR03: main.go must NOT match."""
        assert not _has_match("`main.go`")

    def test_go_model_file_no_match(self) -> None:
        assert not _has_match("`user.go`")


# ---------------------------------------------------------------------------
# FR01 — Java (suffix conventions)
# ---------------------------------------------------------------------------


class TestJavaConvention:
    """FR01: Java *Test.java and *Tests.java JUnit suffix naming."""

    def test_java_test_suffix(self) -> None:
        assert "`UserServiceTest.java`" in _match("`UserServiceTest.java`")

    def test_java_tests_plural_suffix(self) -> None:
        assert "`UserServiceTests.java`" in _match("`UserServiceTests.java`")

    def test_java_handler_test(self) -> None:
        assert "`HttpHandlerTest.java`" in _match("`HttpHandlerTest.java`")

    def test_java_plain_service_no_match(self) -> None:
        """FR03: UserService.java is not a test file."""
        assert not _has_match("`UserService.java`")

    def test_java_plain_controller_no_match(self) -> None:
        assert not _has_match("`UserController.java`")


# ---------------------------------------------------------------------------
# FR01 — Ruby (spec suffix convention)
# ---------------------------------------------------------------------------


class TestRubyConvention:
    """FR01: Ruby *_spec.rb RSpec naming."""

    def test_ruby_basic_spec(self) -> None:
        assert "`user_spec.rb`" in _match("`user_spec.rb`")

    def test_ruby_controller_spec(self) -> None:
        assert "`users_controller_spec.rb`" in _match("`users_controller_spec.rb`")

    def test_ruby_plain_model_no_match(self) -> None:
        assert not _has_match("`user.rb`")

    def test_ruby_plain_controller_no_match(self) -> None:
        assert not _has_match("`users_controller.rb`")


# ---------------------------------------------------------------------------
# FR01 — Rust / Generic tests/ directory convention
# ---------------------------------------------------------------------------


class TestTestsDirectoryConvention:
    """FR01: tests/ and test/ directory prefix convention (Rust, etc.)."""

    def test_tests_dir_rust_integration(self) -> None:
        assert "`tests/integration.rs`" in _match("`tests/integration.rs`")

    def test_tests_dir_nested(self) -> None:
        assert "`tests/auth/login.rs`" in _match("`tests/auth/login.rs`")

    def test_test_singular_dir(self) -> None:
        """test/ (singular) directory also counts."""
        assert "`test/helpers.py`" in _match("`test/helpers.py`")


# ---------------------------------------------------------------------------
# FR03 — No false positives on common implementation files
# ---------------------------------------------------------------------------


class TestNoFalsePositives:
    """FR03: Non-test files must never match _TEST_REF_RE."""

    @pytest.mark.parametrize(
        "snippet",
        [
            "`prd_quality.py`",
            "`config.py`",
            "`router.ts`",
            "`SiteHeader.tsx`",
            "`main.go`",
            "`UserService.java`",
            "`server.js`",
            "`README.md`",
            "`setup.cfg`",
            "`pyproject.toml`",
        ],
    )
    def test_non_test_file_no_match(self, snippet: str) -> None:
        assert not _has_match(snippet), f"False positive: {snippet} matched _TEST_REF_RE"


# ---------------------------------------------------------------------------
# FR01 + FR02 — Mixed-language matrix integration
# ---------------------------------------------------------------------------


class TestMixedLanguageMatrix:
    """Integration: mixed-language traceability matrices count all test refs."""

    def test_python_ts_go_all_counted(self) -> None:
        """US-003: mixed matrix with Python, TypeScript, and Go test refs."""
        matrix = (
            "| FR01 | US-001 | `src/validation.py` | `test_api.py::test_create` | Pending |\n"
            "| FR02 | US-001 | `Dashboard.tsx` | `Dashboard.test.tsx` | Pending |\n"
            "| FR03 | US-002 | `handler.go` | `handler_test.go` | Pending |\n"
        )
        matches = _match(matrix)
        assert len(matches) == 3
        assert "`test_api.py::test_create`" in matches
        assert "`Dashboard.test.tsx`" in matches
        assert "`handler_test.go`" in matches

    def test_all_seven_languages_counted(self) -> None:
        """All 7 language conventions represented simultaneously."""
        matrix = (
            "| FR01 | | `src/mod.py` | `test_module.py` | Pending |\n"
            "| FR02 | | `Component.tsx` | `Component.test.tsx` | Pending |\n"
            "| FR03 | | `api.ts` | `api.spec.ts` | Pending |\n"
            "| FR04 | | `handler.go` | `handler_test.go` | Pending |\n"
            "| FR05 | | `UserSvc.java` | `UserServiceTest.java` | Pending |\n"
            "| FR06 | | `user.rb` | `user_spec.rb` | Pending |\n"
            "| FR07 | | `lib.rs` | `tests/integration.rs` | Pending |\n"
        )
        matches = _match(matrix)
        assert len(matches) == 7, f"Expected 7, got {len(matches)}: {matches}"

    def test_impl_refs_not_counted_as_test_refs(self) -> None:
        """FR03: impl_refs like `src/validation.py` must not appear in test_refs."""
        matrix = "| FR01 | `src/validation.py` | `test_api.py` | Pending |\n"
        matches = _match(matrix)
        assert "`src/validation.py`" not in matches
        assert "`test_api.py`" in matches


# ---------------------------------------------------------------------------
# FR01 — End-to-end: score_traceability_v2 returns matrix_score > 0
# ---------------------------------------------------------------------------

_MINIMAL_FRONTMATTER = """\
---
prd:
  id: PRD-FIX-055-TEST
  title: Test PRD
  version: '1.0'
  status: draft
  priority: P1
  category: FIX
  risk_level: low
  complexity: low
  aaref_components: []
  evidence:
    level: weak
    sources: []
  confidence:
    implementation_feasibility: 0.8
    requirement_clarity: 0.8
    estimate_confidence: 0.8
    test_coverage_target: 0.8
  traceability:
    implements: []
    depends_on: []
    enables: []
  metrics:
    success_criteria: []
    measurement_method: []
  quality_gates:
    ambiguity_rate_max: 0.05
    completeness_min: 0.85
    traceability_coverage_min: 0.9
    consistency_validation_min: 0.95
  dates:
    created: '2026-03-13'
    updated: '2026-03-13'
    target_completion: '2026-03-14'
  template_version: '2.1'
  slos: []
---

"""


def _make_prd_with_matrix(matrix_rows: str) -> str:
    """Minimal PRD body with a Traceability Matrix section."""
    return (
        _MINIMAL_FRONTMATTER
        + "## 12. Traceability Matrix\n\n"
        + "| Requirement | Implementation | Test | Status |\n"
        + "|-------------|----------------|------|--------|\n"
        + matrix_rows
    )


class TestScoreTraceabilityV2Integration:
    """score_traceability_v2 must yield matrix_score > 0 for non-Python test refs."""

    _FRONTMATTER_WITH_FIELDS: dict[str, object] = {
        "traceability": {
            "implements": ["REQ-001"],
            "depends_on": [],
            "enables": [],
        }
    }

    def test_typescript_matrix_score_positive(self) -> None:
        """US-001: TypeScript .test.tsx refs must produce matrix_score > 0."""
        prd = _make_prd_with_matrix(
            "| FR01 | `src/Form.tsx` | `Form.test.tsx` | Pending |\n"
        )
        result = score_traceability_v2(self._FRONTMATTER_WITH_FIELDS, prd)
        assert result.details["matrix_score"] > 0.0, (
            "TypeScript test refs not counted — matrix_score was 0"
        )

    def test_go_matrix_score_positive(self) -> None:
        """US-002: Go _test.go refs must produce matrix_score > 0."""
        prd = _make_prd_with_matrix(
            "| FR01 | `handler.go` | `handler_test.go` | Pending |\n"
            "| FR02 | `service.go` | `service_test.go` | Pending |\n"
        )
        result = score_traceability_v2(self._FRONTMATTER_WITH_FIELDS, prd)
        assert result.details["matrix_score"] > 0.0

    def test_java_matrix_score_positive(self) -> None:
        """Java *Test.java refs must produce matrix_score > 0."""
        prd = _make_prd_with_matrix(
            "| FR01 | `UserService.java` | `UserServiceTest.java` | Pending |\n"
        )
        result = score_traceability_v2(self._FRONTMATTER_WITH_FIELDS, prd)
        assert result.details["matrix_score"] > 0.0

    def test_ruby_matrix_score_positive(self) -> None:
        """Ruby *_spec.rb refs must produce matrix_score > 0."""
        prd = _make_prd_with_matrix(
            "| FR01 | `user.rb` | `user_spec.rb` | Pending |\n"
        )
        result = score_traceability_v2(self._FRONTMATTER_WITH_FIELDS, prd)
        assert result.details["matrix_score"] > 0.0

    def test_rust_tests_dir_matrix_score_positive(self) -> None:
        """Rust tests/ dir refs must produce matrix_score > 0."""
        prd = _make_prd_with_matrix(
            "| FR01 | `src/lib.rs` | `tests/integration.rs` | Pending |\n"
        )
        result = score_traceability_v2(self._FRONTMATTER_WITH_FIELDS, prd)
        assert result.details["matrix_score"] > 0.0

    def test_mixed_language_matrix_highest_score(self) -> None:
        """US-003: all 7 language conventions in one matrix yield max matrix_score."""
        prd = _make_prd_with_matrix(
            "| FR01 | `mod.py` | `test_module.py` | Pending |\n"
            "| FR02 | `Component.tsx` | `Component.test.tsx` | Pending |\n"
            "| FR03 | `api.ts` | `api.spec.ts` | Pending |\n"
            "| FR04 | `handler.go` | `handler_test.go` | Pending |\n"
            "| FR05 | `UserSvc.java` | `UserServiceTest.java` | Pending |\n"
            "| FR06 | `user.rb` | `user_spec.rb` | Pending |\n"
            "| FR07 | `lib.rs` | `tests/integration.rs` | Pending |\n"
        )
        result = score_traceability_v2(self._FRONTMATTER_WITH_FIELDS, prd)
        assert result.details["matrix_score"] == 1.0, (
            f"Expected max matrix_score=1.0, got {result.details['matrix_score']}"
        )


# ---------------------------------------------------------------------------
# _KNOWN_TEST_PATTERNS constant completeness
# ---------------------------------------------------------------------------


class TestKnownTestPatternsConstant:
    """The _KNOWN_TEST_PATTERNS dict must document all supported languages."""

    REQUIRED_KEYS = {"python", "typescript", "javascript", "go", "rust", "java", "ruby"}

    def test_all_required_languages_present(self) -> None:
        missing = self.REQUIRED_KEYS - _KNOWN_TEST_PATTERNS.keys()
        assert not missing, f"Missing language entries in _KNOWN_TEST_PATTERNS: {missing}"

    def test_all_values_are_non_empty_strings(self) -> None:
        for lang, description in _KNOWN_TEST_PATTERNS.items():
            assert isinstance(description, str) and description.strip(), (
                f"_KNOWN_TEST_PATTERNS['{lang}'] is empty or not a string"
            )

    def test_regex_covers_all_documented_languages(self) -> None:
        """Each language in _KNOWN_TEST_PATTERNS must have at least one match example."""
        examples: dict[str, str] = {
            "python": "`test_example.py`",
            "typescript": "`example.test.ts`",
            "javascript": "`example.test.js`",
            "go": "`example_test.go`",
            "rust": "`tests/example.rs`",
            "java": "`ExampleTest.java`",
            "ruby": "`example_spec.rb`",
            "generic_spec": "`example.spec.tsx`",
        }
        for lang in _KNOWN_TEST_PATTERNS:
            if lang in examples:
                assert _has_match(examples[lang]), (
                    f"_TEST_REF_RE does not match example for language '{lang}': {examples[lang]}"
                )


# ---------------------------------------------------------------------------
# NFR01 — Performance: regex must not be catastrophically slow
# ---------------------------------------------------------------------------


class TestRegexPerformance:
    """NFR01: The regex must complete in a reasonable time on typical input."""

    def test_large_matrix_section_completes_quickly(self) -> None:
        """50 rows of mixed-language refs must complete without timeout."""
        row = "| FRxx | `src/module.py` | `test_module.py` | Pending |\n"
        section = row * 50
        # If this hangs, there's a ReDoS issue.
        matches = _TEST_REF_RE.findall(section)
        assert len(matches) == 50

    def test_regex_is_precompiled(self) -> None:
        """_TEST_REF_RE must be a compiled Pattern (not a string)."""
        assert isinstance(_TEST_REF_RE, re.Pattern), (
            "_TEST_REF_RE should be a compiled re.Pattern for performance"
        )
