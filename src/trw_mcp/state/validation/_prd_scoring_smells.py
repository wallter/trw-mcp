"""Requirement-smell detection + EARS pattern classification (informational).

Belongs to the ``prd_quality.py`` facade. These functions populate
``ValidationResultV2.smell_findings`` and ``.ears_classifications`` as **advisory
diagnostics only** — they do NOT affect ``total_score`` (``validation_smell_weight``
and ``validation_ears_weight`` stay 0). They operationalize AARE-F v3.0.0 §2.4
(requirements-smell taxonomy) and §2.1 (EARS phrasing), which the validator
previously declared but never computed (the fields were always empty stubs).

Detection is scoped to requirement-like lines outside fenced code blocks, and uses
word-boundary regexes, to keep precision high and avoid flagging ordinary prose.
"""

from __future__ import annotations

import re

from trw_mcp.models.requirements import SmellFinding

# A requirement-like line: an FR/NFR id, a checkbox item, a "shall" clause, or a
# line opening with an EARS condition keyword.
_REQUIREMENT_LINE_RE = re.compile(
    r"(?:\bFR-?\d+|\bNFR-?\d+|^\s*-\s*\[[ x]\]|\bshall\b|^\s*(?:When|While|If|Where)\b)",
    re.IGNORECASE,
)

_FENCE_RE = re.compile(r"^\s*```")

# Strip list/checkbox/id prefixes so EARS keyword detection sees the real clause.
_PREFIX_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\[[ x]\]\s*)?(?:\*\*)?(?:FR-?\d+|NFR-?\d+)?[:.)\s]*", re.IGNORECASE)

# (category, pattern, severity, suggestion). Patterns are word-bounded and
# case-insensitive; each targets a distinct AARE-F §2.4 smell class.
_SMELL_PATTERNS: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
    (
        "weak_modal",
        re.compile(r"\b(?:should|might|may|could|would)\b", re.IGNORECASE),
        "warning",
        "Use 'shall' for a binding requirement; reserve should/may for explicitly non-binding notes (INCOSE R3).",
    ),
    (
        "vague_adverb",
        re.compile(r"\b(?:quickly|efficiently|easily|appropriately|adequately|sufficiently|properly|reasonably|seamlessly)\b", re.IGNORECASE),
        "warning",
        "Replace the adverb with a measurable criterion (a number, bound, or condition).",
    ),
    (
        "subjective",
        re.compile(r"\b(?:user-friendly|intuitive|seamless|robust|flexible|scalable|lightweight|simple|fast|slow|good|nice)\b", re.IGNORECASE),
        "warning",
        "Quantify the quality or state the acceptance criterion; subjective terms are not verifiable (ISO 29148 Unambiguous).",
    ),
    (
        "escape_clause",
        re.compile(r"\b(?:if possible|where possible|as appropriate|as needed|as required|if practical(?:able)?|where feasible|to the extent practicable|if applicable)\b", re.IGNORECASE),
        "warning",
        "Remove the escape clause or specify the exact triggering condition (INCOSE R8).",
    ),
    (
        "open_ended",
        re.compile(r"\b(?:etc\.?|and so on|and/or|including but not limited to|tbd|to be determined)\b", re.IGNORECASE),
        "warning",
        "Enumerate the cases explicitly; open-ended lists are not testable (INCOSE R9).",
    ),
    (
        "superlative",
        re.compile(r"\b(?:best|fastest|highest|lowest|optimal|maximal|minimal)\b", re.IGNORECASE),
        "info",
        "Define the target value instead of a superlative.",
    ),
    (
        "absolute",
        re.compile(r"\b(?:always|never|100%|every case|all cases)\b", re.IGNORECASE),
        "info",
        "Absolutes are rarely verifiable; state the measurable bound (INCOSE R26 Realism).",
    ),
)

# A bare pronoun opening a clause is an anaphora/vagueness smell.
_PRONOUN_OPEN_RE = re.compile(r"^\s*(?:it|they|them|this|that|these|those)\b", re.IGNORECASE)

# EARS opening keywords for compound detection.
_AND_OR_RE = re.compile(r"\b(?:and|or)\b", re.IGNORECASE)


def _iter_requirement_lines(content: str) -> list[tuple[int, str]]:
    """Return (1-based line number, raw line) for requirement-like lines outside code fences."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for idx, line in enumerate(content.splitlines(), start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _REQUIREMENT_LINE_RE.search(line):
            out.append((idx, line))
    return out


def detect_smells(content: str) -> list[SmellFinding]:
    """Detect requirement smells in requirement-like lines (AARE-F §2.4).

    Informational only — never affects the score. Returns at most one finding per
    (line, category) pair to avoid duplicate spam on repeated terms.
    """
    findings: list[SmellFinding] = []
    for line_no, line in _iter_requirement_lines(content):
        for category, pattern, severity, suggestion in _SMELL_PATTERNS:
            m = pattern.search(line)
            if m:
                findings.append(
                    SmellFinding(
                        category=category,
                        line_number=line_no,
                        matched_text=m.group(0),
                        severity=severity,
                        suggestion=suggestion,
                    )
                )
        # Compound requirement: a single line joining independent clauses (INCOSE R18 Singularity).
        clause = _PREFIX_RE.sub("", line)
        if "shall" in clause.lower() and len(_AND_OR_RE.findall(clause)) >= 2:
            findings.append(
                SmellFinding(
                    category="compound",
                    line_number=line_no,
                    matched_text=clause.strip()[:80],
                    severity="info",
                    suggestion="Split into singular requirements — one capability per statement (INCOSE R18).",
                )
            )
        # Anaphoric/vague pronoun opening a requirement clause.
        if _PRONOUN_OPEN_RE.match(clause):
            findings.append(
                SmellFinding(
                    category="vague_pronoun",
                    line_number=line_no,
                    matched_text=clause.strip()[:40],
                    severity="info",
                    suggestion="Name the subject explicitly instead of a pronoun (INCOSE R24).",
                )
            )
    return findings


def summarize_smells(findings: list[SmellFinding]) -> str | None:
    """Summarize warning-severity smells into one concise advisory line (FR01).

    Returns a single-line advisory naming the top smell categories (most
    frequent first) and up to ~5 cited line numbers, or ``None`` when there
    are no ``warning``-severity findings. Advisory only -- the caller appends
    this as one informational ``ImprovementSuggestion`` and never alters the
    score (NFR01) or validity/tier (FR03). Output is bounded (NFR02): one
    suggestion, distinct categories (pattern-table bounded), and at most 5
    cited line numbers.
    """
    warnings = [f for f in findings if f.severity == "warning"]
    if not warnings:
        return None

    # List distinct warning categories, most frequent first (ties broken by name).
    # The pattern table bounds this to a handful of categories, so naming them all
    # keeps the advisory honest (every detected smell class is cited) and bounded.
    counts: dict[str, int] = {}
    for f in warnings:
        counts[f.category] = counts.get(f.category, 0) + 1
    top_categories = sorted(counts, key=lambda c: (-counts[c], c))

    # Cite up to 5 distinct line numbers, in ascending order.
    lines = sorted({f.line_number for f in warnings})[:5]
    lines_str = ", ".join(str(n) for n in lines)

    return (
        f"Requirement smells detected ({len(warnings)} warning(s)): "
        f"{', '.join(top_categories)}. See line(s) {lines_str}. "
        "Tighten phrasing per AARE-F §2.4 (advisory — does not affect score)."
    )


def classify_ears(content: str) -> list[dict[str, object]]:
    """Classify requirement-like lines by EARS pattern (AARE-F §2.1).

    Informational only. ``pattern`` is one of: ubiquitous, state-driven,
    event-driven, optional-feature, unwanted-behavior, complex, or non-ears
    (a requirement-shaped line that follows no EARS pattern — an authoring smell).
    """
    out: list[dict[str, object]] = []
    for line_no, line in _iter_requirement_lines(content):
        clause = _PREFIX_RE.sub("", line).strip()
        low = clause.lower()
        has_shall = "shall" in low
        is_requirement = has_shall or bool(re.search(r"\b(?:FR-?\d+|NFR-?\d+)\b|\bmust\b", line, re.IGNORECASE))
        if not is_requirement:
            continue
        if re.match(r"^while\b", low) and re.search(r"\bwhen\b", low):
            pattern = "complex"
        elif re.match(r"^while\b", low):
            pattern = "state-driven"
        elif re.match(r"^when\b", low):
            pattern = "event-driven"
        elif re.match(r"^where\b", low):
            pattern = "optional-feature"
        elif re.match(r"^if\b", low):
            pattern = "unwanted-behavior"
        elif has_shall:
            pattern = "ubiquitous"
        else:
            pattern = "non-ears"
        out.append({"line_number": line_no, "pattern": pattern, "text": clause[:120]})
    return out
