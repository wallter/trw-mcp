"""Readability metrics — Flesch-Kincaid Grade Level and sentence analysis.

Implements PRD-CORE-008-FR06. Pure Python syllable counting with
heuristic vowel-group algorithm. No external dependencies.

All functions are pure — no side effects, no file I/O.
"""

from __future__ import annotations

import re

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import DimensionScore

# Precompiled patterns
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---", re.DOTALL)
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_HEADING_RE = re.compile(r"^#+\s+.*$", re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_SENTENCE_RE = re.compile(r"[.!?]+(?:\s|$)")
_WORD_RE = re.compile(r"[a-zA-Z]+")
_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+", re.IGNORECASE)
_SILENT_E_RE = re.compile(r"[^aeiouy]e$", re.IGNORECASE)

# Common suffixes that don't add a syllable
_SILENT_SUFFIXES = ("ed", "es")


def _strip_non_prose(content: str) -> str:
    """Remove frontmatter, code blocks, headings, and comments.

    Args:
        content: Full PRD markdown content.

    Returns:
        Prose-only text suitable for readability analysis.
    """
    text = _FRONTMATTER_RE.sub("", content)
    text = _CODE_BLOCK_RE.sub("", text)
    text = _HEADING_RE.sub("", text)
    text = _HTML_COMMENT_RE.sub("", text)
    # Convert markdown links to just their text
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    # Strip inline code
    text = _INLINE_CODE_RE.sub("", text)
    # Strip table separator rows
    text = re.sub(r"^\s*\|[\s\-:|]+\|\s*$", "", text, flags=re.MULTILINE)
    # Strip horizontal rules
    text = re.sub(r"^\s*---\s*$", "", text, flags=re.MULTILINE)
    # Strip markdown bold/italic markers
    text = re.sub(r"\*\*?|__?", "", text)
    # Strip list markers
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    return text


def count_syllables(word: str) -> int:
    """Count syllables in a word using vowel-group heuristic.

    Args:
        word: Single word (letters only).

    Returns:
        Estimated syllable count (minimum 1).
    """
    word = word.lower().strip()
    if not word:
        return 0

    # Count vowel groups
    groups = _VOWEL_GROUP_RE.findall(word)
    count = len(groups)

    # Silent-e adjustment: if word ends in consonant+e, subtract 1
    if _SILENT_E_RE.search(word) and count > 1:
        count -= 1

    # Common suffix adjustment: words ending in "ed" where the e is silent
    if word.endswith("ed") and len(word) > 3 and word[-3] not in "td" and count > 1:
        count -= 1

    return max(count, 1)


def split_sentences(text: str) -> list[str]:
    """Split text into sentences.

    Args:
        text: Prose text.

    Returns:
        List of non-empty sentences.
    """
    # Split on sentence-ending punctuation
    parts = _SENTENCE_RE.split(text)
    sentences = []
    for part in parts:
        cleaned = part.strip()
        if cleaned and len(cleaned) > 2:
            sentences.append(cleaned)
    return sentences


def flesch_kincaid_grade(text: str) -> dict[str, float]:
    """Compute Flesch-Kincaid Grade Level and related metrics.

    Formula: 0.39 * (words/sentences) + 11.8 * (syllables/words) - 15.59

    Args:
        text: Prose text (already stripped of non-prose elements).

    Returns:
        Dict with fk_grade, avg_sentence_length, avg_syllables_per_word.
    """
    sentences = split_sentences(text)
    if not sentences:
        return {
            "fk_grade": 0.0,
            "avg_sentence_length": 0.0,
            "avg_syllables_per_word": 0.0,
            "total_words": 0.0,
            "total_sentences": 0.0,
        }

    words: list[str] = _WORD_RE.findall(text)
    if not words:
        return {
            "fk_grade": 0.0,
            "avg_sentence_length": 0.0,
            "avg_syllables_per_word": 0.0,
            "total_words": 0.0,
            "total_sentences": float(len(sentences)),
        }

    total_syllables = sum(count_syllables(w) for w in words)
    word_count = len(words)
    sentence_count = len(sentences)

    avg_sentence_length = word_count / sentence_count
    avg_syllables_per_word = total_syllables / word_count

    fk_grade = 0.39 * avg_sentence_length + 11.8 * avg_syllables_per_word - 15.59

    return {
        "fk_grade": round(fk_grade, 2),
        "avg_sentence_length": round(avg_sentence_length, 2),
        "avg_syllables_per_word": round(avg_syllables_per_word, 2),
        "total_words": float(word_count),
        "total_sentences": float(sentence_count),
    }


def score_readability(
    content: str,
    config: TRWConfig | None = None,
) -> tuple[DimensionScore, dict[str, float]]:
    """Score the Readability dimension.

    Optimal FK grade is 8-12 (configurable). Score linearly degrades
    for grades outside this range:
    - 6-8 and 12-16: linear degradation
    - <6 or >16: floor at 2/max_score * max_score (20%)

    Args:
        content: Full PRD markdown content.
        config: Optional config for weight/threshold overrides.

    Returns:
        Tuple of (DimensionScore, readability metrics dict).
    """
    _config = config or TRWConfig()
    max_score = _config.validation_readability_weight
    fk_min = _config.validation_fk_optimal_min
    fk_max = _config.validation_fk_optimal_max

    # Strip non-prose and compute FK
    prose = _strip_non_prose(content)
    metrics = flesch_kincaid_grade(prose)
    fk = metrics["fk_grade"]

    # No content → neutral score
    if metrics["total_words"] == 0.0:
        score = max_score * 0.5
        dim = DimensionScore(
            name="readability",
            score=round(score, 2),
            max_score=max_score,
            details={"fk_grade": 0.0, "reason": "no_prose_content"},
        )
        return dim, metrics

    # Score based on FK grade distance from optimal range
    if fk_min <= fk <= fk_max:
        ratio = 1.0
    elif fk < fk_min:
        # Linear degradation from fk_min down to fk_min-4 (floor at 0.2)
        lower_bound = fk_min - 4.0
        if fk <= lower_bound:
            ratio = 0.2
        else:
            ratio = 0.2 + 0.8 * ((fk - lower_bound) / 4.0)
    else:
        # fk > fk_max: linear degradation up to fk_max+4 (floor at 0.2)
        upper_bound = fk_max + 4.0
        if fk >= upper_bound:
            ratio = 0.2
        else:
            ratio = 0.2 + 0.8 * ((upper_bound - fk) / 4.0)

    score = ratio * max_score

    dim = DimensionScore(
        name="readability",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details={
            "fk_grade": fk,
            "optimal_range": f"{fk_min}-{fk_max}",
        },
    )

    return dim, metrics
