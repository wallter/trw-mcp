"""Tests for PRD-QUAL-093: behavioral (wiring) assertions as a first-class type.

Verifies (anti-pattern A1, learning L-bGOd):
- FR01: behavioral assertion vocabulary (asserts_value, output_contains,
  value_equals) is recognized as a valid assertion alongside existence types.
- FR02: ``classify_assertions`` returns behavioral vs existence counts.
- FR03: the bundled prd_template.md Assertions block documents behavioral
  examples and cites A1 ("value, not just the symbol").
- NFR01: recognition does NOT add a new scoring weight — behavioral and
  existence assertions are counted equivalently for coverage scoring.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.validation._prd_scoring_counts import (
    _has_assertion_evidence,
    classify_assertions,
)

# An FR whose ONLY assertion is behavioral (no existence syntax anywhere).
_BEHAVIORAL_FR = """\
### PRD-CORE-001-FR01: Wiring

**Assertions**:
- asserts_value: "result.estimate == 0.42 in tests/test_x.py::test_wiring"
"""

# An FR whose ONLY assertion is the legacy existence type.
_EXISTENCE_FR = """\
### PRD-CORE-001-FR01: Existence

**Assertions**:
- grep_present: "handle_login" in "src/**/*.py"
"""

# Mixed PRD: 1 behavioral + 2 existence assertions.
_MIXED_PRD = """\
### PRD-CORE-001-FR01: Mixed

**Assertions**:
- asserts_value: "result.estimate == 0.42 in tests/test_x.py"
- grep_present: "handle_login" in "src/**/*.py"
- grep_present: "refresh_token" in "src/**/*.py"
"""


# ---------------------------------------------------------------------------
# FR01: behavioral assertion vocabulary recognized
# ---------------------------------------------------------------------------


class TestBehavioralRecognized:
    """PRD-QUAL-093-FR01."""

    def test_behavioral_detected(self) -> None:
        """A behavioral-only FR is detected as having assertion evidence.

        This is the gap: before QUAL-093 the recognizer only knew existence
        keywords, so a behavioral-only FR returned False.
        """
        assert _has_assertion_evidence(_BEHAVIORAL_FR) is True

    def test_output_contains_detected(self) -> None:
        content = '- output_contains: "0.42" in "stdout of pytest -k test_wiring"'
        assert _has_assertion_evidence(content) is True

    def test_value_equals_detected(self) -> None:
        content = '- value_equals: "config.weight == 1.0 in tests/test_cfg.py"'
        assert _has_assertion_evidence(content) is True

    def test_existence_still_detected(self) -> None:
        """NFR02 backward compatibility — existence detection unchanged."""
        assert _has_assertion_evidence(_EXISTENCE_FR) is True

    def test_prose_mention_not_detected(self) -> None:
        """A prose mention of the word is not an assertion line."""
        content = "We will assert the value of the estimate is correct."
        assert _has_assertion_evidence(content) is False


# ---------------------------------------------------------------------------
# FR02: behavioral vs existence classifier
# ---------------------------------------------------------------------------


class TestClassifyAssertions:
    """PRD-QUAL-093-FR02."""

    def test_classify_counts(self) -> None:
        """1 asserts_value + 2 grep_present -> {behavioral:1, existence:2}."""
        result = classify_assertions(_MIXED_PRD)
        assert result == {"behavioral": 1, "existence": 2}

    def test_classify_behavioral_only(self) -> None:
        result = classify_assertions(_BEHAVIORAL_FR)
        assert result == {"behavioral": 1, "existence": 0}

    def test_classify_existence_only(self) -> None:
        result = classify_assertions(_EXISTENCE_FR)
        assert result == {"behavioral": 0, "existence": 1}

    def test_classify_command_succeeds_is_behavioral(self) -> None:
        """command_succeeds verifies a run outcome -> behavioral."""
        content = '- command_succeeds: "pytest -k test_wiring"'
        assert classify_assertions(content) == {"behavioral": 1, "existence": 0}

    def test_classify_glob_exists_is_existence(self) -> None:
        content = '- glob_exists: "src/module/new_file.py"'
        assert classify_assertions(content) == {"behavioral": 0, "existence": 1}

    def test_classify_empty(self) -> None:
        assert classify_assertions("no assertions here") == {
            "behavioral": 0,
            "existence": 0,
        }


# ---------------------------------------------------------------------------
# FR03: template steers toward behavioral assertions
# ---------------------------------------------------------------------------


class TestTemplateGuidance:
    """PRD-QUAL-093-FR03."""

    def _template_text(self) -> str:
        template = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "prd_template.md"
        return template.read_text(encoding="utf-8")

    def test_template_has_behavioral_guidance(self) -> None:
        text = self._template_text()
        assert "asserts_value" in text or "output_contains" in text

    def test_template_cites_a1_value_not_symbol(self) -> None:
        text = self._template_text()
        lowered = text.lower()
        assert "a1" in lowered
        assert "value, not just the symbol" in lowered


# ---------------------------------------------------------------------------
# NFR01: recognition adds no new scoring weight
# ---------------------------------------------------------------------------


class TestScoreInvariance:
    """PRD-QUAL-093-NFR01: behavioral assertions counted, but not re-weighted.

    Both detect as "has assertion evidence" identically — the recognizer is a
    boolean predicate that does not differentiate weight by assertion family.
    A behavioral-only FR and an existence-only FR are scored equivalently for
    coverage purposes (both contribute a single "has assertions" signal).
    """

    def test_behavioral_and_existence_score_equally(self) -> None:
        """Both produce the same _has_assertion_evidence verdict (no weight delta)."""
        assert _has_assertion_evidence(_BEHAVIORAL_FR) == _has_assertion_evidence(_EXISTENCE_FR)

    def test_adding_behavioral_does_not_double_count_evidence(self) -> None:
        """The predicate stays boolean — adding a behavioral line alongside an
        existence line yields the same True verdict, no escalated signal."""
        both = _EXISTENCE_FR + '\n- asserts_value: "x == 1 in tests/test_x.py"'
        assert _has_assertion_evidence(both) is True
        assert _has_assertion_evidence(_EXISTENCE_FR) is True

    def test_behavioral_and_existence_assertions_score_identically(self) -> None:
        """NFR01 end-to-end: vocabulary widening adds NO differential scoring weight.

        Two otherwise-identical minimal PRDs differ ONLY in one FR's assertion
        keyword — one existence (``grep_present``), one behavioral
        (``asserts_value``) — under an ``**Assertions**:`` heading. The file
        references are held identical in both so the test isolates the
        assertion-TYPE effect (the thing NFR01 is about) from incidental
        impl/test-file coverage, which is scored on a separate axis.

        NOTE on the audit spec: the audit's literal example lines
        (``grep_present: "X" in "a.py"`` vs
        ``asserts_value: "out.x == 1 in tests/t.py"``) embed a confounder — the
        behavioral line cites a *test* file (``tests/t.py``) while the existence
        line cites a non-test file (``a.py``), so they score differently via the
        independent test-file-coverage sub-dimension (17.13 vs 13.94), NOT via
        any assertion-type weight. Using them verbatim would make the
        score-equality claim FALSE. We keep the spec's assertion vocabulary but
        hold the file refs identical, which is the only way to prove the actual
        NFR01 claim (behavioral and existence assertions are weighted equally).
        """
        from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

        def _prd(assertion_line: str) -> str:
            return "\n".join(
                [
                    "---",
                    "id: PRD-CORE-994",
                    "title: Assertion invariance probe",
                    "version: 1.0.0",
                    "status: draft",
                    "priority: P2",
                    "---",
                    "## Functional Requirements",
                    "### PRD-CORE-994-FR01: Wiring",
                    "When a request arrives, the system shall persist the record in `a.py` verified by `tests/t.py`.",
                    "**Assertions**:",
                    f"- {assertion_line}",
                ]
            )

        existence_prd = _prd('grep_present: "handle in a.py and tests/t.py"')
        behavioral_prd = _prd('asserts_value: "out.x == 1 in a.py and tests/t.py"')

        assert validate_prd_quality_v2(existence_prd).total_score == validate_prd_quality_v2(behavioral_prd).total_score
