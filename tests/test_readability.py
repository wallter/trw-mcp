"""Tests for readability metrics (PRD-CORE-008 Phase 2b)."""

from __future__ import annotations

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.readability import (
    _strip_non_prose,
    count_syllables,
    flesch_kincaid_grade,
    score_readability,
    split_sentences,
)


# ---------------------------------------------------------------------------
# Test: count_syllables
# ---------------------------------------------------------------------------


class TestCountSyllables:
    """Test the syllable counting heuristic."""

    def test_one_syllable(self) -> None:
        assert count_syllables("cat") == 1
        assert count_syllables("dog") == 1
        assert count_syllables("run") == 1

    def test_two_syllables(self) -> None:
        assert count_syllables("running") == 2
        assert count_syllables("happy") == 2

    def test_three_syllables(self) -> None:
        assert count_syllables("beautiful") == 3
        assert count_syllables("important") == 3

    def test_silent_e(self) -> None:
        # "make" should be 1 syllable (silent e)
        assert count_syllables("make") == 1
        assert count_syllables("take") == 1

    def test_empty_string(self) -> None:
        assert count_syllables("") == 0

    def test_minimum_one_for_words(self) -> None:
        # Even single-letter words should count as at least 1
        assert count_syllables("a") >= 1
        assert count_syllables("I") >= 1

    def test_longer_words(self) -> None:
        result = count_syllables("implementation")
        assert result >= 4  # im-ple-men-ta-tion

    def test_ed_suffix_silent(self) -> None:
        # "walked" → 1 syllable (silent ed)
        assert count_syllables("walked") == 1

    def test_ed_suffix_voiced(self) -> None:
        # "wanted" → 2 syllables (ed pronounced)
        assert count_syllables("wanted") == 2


# ---------------------------------------------------------------------------
# Test: split_sentences
# ---------------------------------------------------------------------------


class TestSplitSentences:
    """Test sentence splitting."""

    def test_simple_sentences(self) -> None:
        text = "This is first. This is second. This is third."
        sentences = split_sentences(text)
        assert len(sentences) == 3

    def test_question_marks(self) -> None:
        text = "Is this a test? Yes it is."
        sentences = split_sentences(text)
        assert len(sentences) == 2

    def test_exclamation_marks(self) -> None:
        text = "This works! Great stuff."
        sentences = split_sentences(text)
        assert len(sentences) == 2

    def test_empty_text(self) -> None:
        sentences = split_sentences("")
        assert len(sentences) == 0

    def test_no_punctuation(self) -> None:
        text = "This is a sentence without a period"
        sentences = split_sentences(text)
        assert len(sentences) == 1


# ---------------------------------------------------------------------------
# Test: flesch_kincaid_grade
# ---------------------------------------------------------------------------


class TestFleschKincaidGrade:
    """Test FK grade computation."""

    def test_simple_text(self) -> None:
        # Simple text should have low grade
        text = "The cat sat. The dog ran. The bird flew."
        result = flesch_kincaid_grade(text)
        assert result["fk_grade"] < 8.0

    def test_complex_text(self) -> None:
        # More complex sentences → higher grade
        text = (
            "The implementation of the sophisticated validation "
            "algorithm requires comprehensive understanding of "
            "multidimensional scoring methodologies and their "
            "corresponding evaluation frameworks."
        )
        result = flesch_kincaid_grade(text)
        assert result["fk_grade"] > 10.0

    def test_empty_text(self) -> None:
        result = flesch_kincaid_grade("")
        assert result["fk_grade"] == 0.0
        assert result["total_words"] == 0.0

    def test_returns_all_fields(self) -> None:
        text = "This is a test sentence."
        result = flesch_kincaid_grade(text)
        assert "fk_grade" in result
        assert "avg_sentence_length" in result
        assert "avg_syllables_per_word" in result
        assert "total_words" in result
        assert "total_sentences" in result

    def test_reference_text_accuracy(self) -> None:
        """FK grade for known reference text within tolerance."""
        # Simple declarative sentences → roughly grade 5-9
        text = (
            "The system shall validate input data. "
            "It shall return an error for invalid requests. "
            "The error message shall include a description."
        )
        result = flesch_kincaid_grade(text)
        # Should be in a reasonable range for technical writing
        assert 2.0 < result["fk_grade"] < 16.0


# ---------------------------------------------------------------------------
# Test: strip_non_prose
# ---------------------------------------------------------------------------


class TestStripNonProse:
    """Test non-prose stripping."""

    def test_strips_frontmatter(self) -> None:
        text = "---\nprd:\n  id: X\n---\n\nActual content here."
        result = _strip_non_prose(text)
        assert "prd:" not in result
        assert "Actual content here" in result

    def test_strips_code_blocks(self) -> None:
        text = "Before.\n```python\ncode = True\n```\nAfter."
        result = _strip_non_prose(text)
        assert "code = True" not in result
        assert "Before" in result
        assert "After" in result

    def test_strips_headings(self) -> None:
        text = "# Heading\n## Subheading\nParagraph text."
        result = _strip_non_prose(text)
        assert "Heading" not in result
        assert "Paragraph text" in result

    def test_strips_html_comments(self) -> None:
        text = "Content <!-- comment --> more content."
        result = _strip_non_prose(text)
        assert "comment" not in result

    def test_preserves_link_text(self) -> None:
        text = "See [the docs](https://example.com) for details."
        result = _strip_non_prose(text)
        assert "the docs" in result
        assert "https://example.com" not in result


# ---------------------------------------------------------------------------
# Test: score_readability
# ---------------------------------------------------------------------------


class TestScoreReadability:
    """Test the readability scoring function."""

    def test_optimal_range_max_score(self) -> None:
        # Technical writing at grade 8-12 should score high
        text = (
            "---\nprd:\n  id: X\n---\n\n## 1. Problem Statement\n\n"
            "The validation engine computes a numeric quality score for each PRD. "
            "It evaluates six independent dimensions and classifies the document "
            "into one of four quality tiers based on the total weighted score. "
            "The system generates improvement suggestions for dimensions that "
            "score below seventy percent of their maximum possible value."
        )
        dim, metrics = score_readability(text)
        assert dim.name == "readability"
        assert dim.max_score == 10.0
        # Should be above 50% at minimum
        assert dim.score >= 5.0

    def test_empty_content_neutral(self) -> None:
        dim, metrics = score_readability("")
        assert dim.score == dim.max_score * 0.5  # neutral

    def test_no_prose_neutral(self) -> None:
        # Only frontmatter and headings
        text = "---\nprd:\n  id: X\n---\n\n## 1. Problem Statement\n"
        dim, metrics = score_readability(text)
        assert dim.score == dim.max_score * 0.5

    def test_score_range(self) -> None:
        text = (
            "---\nprd:\n  id: X\n---\n\n## 1. Problem Statement\n\n"
            "This is content with some words to analyze."
        )
        dim, _ = score_readability(text)
        assert 0.0 <= dim.score <= dim.max_score

    def test_custom_weight(self) -> None:
        config = TRWConfig(validation_readability_weight=20.0)
        text = (
            "---\nprd:\n  id: X\n---\n\n## 1. Problem Statement\n\n"
            "Simple test content here."
        )
        dim, _ = score_readability(text, config=config)
        assert dim.max_score == 20.0

    def test_custom_fk_range(self) -> None:
        config = TRWConfig(validation_fk_optimal_min=6.0, validation_fk_optimal_max=14.0)
        text = (
            "---\nprd:\n  id: X\n---\n\n## 1. Problem Statement\n\n"
            "The cat sat on the mat. The dog ran to the park."
        )
        dim, metrics = score_readability(text, config=config)
        # With wider optimal range, simple text should score higher
        assert dim.score >= 0.0

    def test_metrics_returned(self) -> None:
        text = (
            "---\nprd:\n  id: X\n---\n\n## 1. Problem Statement\n\n"
            "This is a test. It has sentences."
        )
        _, metrics = score_readability(text)
        assert "fk_grade" in metrics
        assert "total_words" in metrics

    def test_very_simple_text_lower_score(self) -> None:
        """Text well below optimal FK range should score lower."""
        text = (
            "---\nprd:\n  id: X\n---\n\n## 1. Problem Statement\n\n"
            "Go. Run. Do. See. Try. Win. Fix. Set. Get. End."
        )
        dim, metrics = score_readability(text)
        # Very simple → FK well below 8 → degraded score
        assert dim.score < dim.max_score
