"""Load-bearing invariant families as checkable core-coverage anchors.

Belongs to the ``trw_mcp.canons.registry`` facade. Re-exported there.

PRD-CORE-207 Appendix A enumerates the normative obligation families that MUST
survive in each compact core (FR03/NFR01). We encode each family as one or more
required anchor phrases that MUST occur verbatim in the generated core. This is
the structural obligation-coverage gate: a core missing any anchor fails before
promotion, so a "shorter but weaker" core (US-004) cannot ship.

``FORBIDDEN_CORE_PATTERNS`` is the NFR05 portability scan: the core must stay
client-, provider-, model-, language-, and VCS-neutral. Adapter *filenames*
(``GEMINI.md``, ``.codex/``) are permitted path syntax, so the scan targets
concrete model-version / fixed-context-window / single-language-build mandates,
never bare vendor words.

Standard-library only (NFR02): tuples + re.
"""

from __future__ import annotations

import re

# FRAMEWORK core: (family_id, required_anchor_phrase). All phrases MUST be
# present verbatim in the generated FRAMEWORK compact core.
FRAMEWORK_CORE_ANCHORS: tuple[tuple[str, str], ...] = (
    ("truthfulness-authority", "READING CONTRACT"),
    ("truthfulness-authority", "higher-authority instruction"),
    ("first-session-recovery", "trw_session_start"),
    ("compaction-recovery", "After compaction"),
    ("rigid-flexible-tools", "RIGID / FLEXIBLE TOOL CLASSIFICATION"),
    ("phase-complexity", "CEREMONY TIERS"),
    ("phase-complexity", "VALIDATE is never skipped"),
    ("requirements-traceability", "Before IMPLEMENT"),
    ("validation-evidence", "LANGUAGE-AGNOSTIC VALIDATION"),
    ("validation-evidence", "Build evidence MUST postdate"),
    ("review-independence", "REVIEW is mandatory at STANDARD+"),
    ("review-independence", "trw_review"),
    ("delivery-gate", "Deliver gate (no fourth path)"),
    ("acceptable-failure", "acceptable-failure record"),
    ("override-not-verification", "never turns unverified work into verified work"),
    ("status-truthfulness", "misreporting a check is a hard-boundary violation"),
    ("delegation-ownership", "DELEGATION AND FILE OWNERSHIP"),
    ("no-self-certification", "No self-certification"),
    ("shared-worktree-commit", "Commit each coherent, focused, green milestone"),
    ("destructive-git-prohibition", "`git add -A`"),
    ("destructive-git-prohibition", "command-specific operator authorization and exclusive ownership"),
    ("portability", "capability labels"),
    ("autonomous-closure", "Outcome-gated closure"),
    ("autonomous-escalation", "Un-suppressible escalation"),
    ("gates-recourse", "Gates need recourse"),
    ("reflection-learning", "Delivery reflection is mandatory"),
    ("end-of-session", "trw_deliver"),
)

# AARE-F core: (family_id, required_anchor_phrase).
AAREF_CORE_ANCHORS: tuple[tuple[str, str], ...] = (
    ("specification-authority", "Specification Primacy"),
    ("risk-scaling", "Risk-Based Rigor"),
    ("readiness-vs-closure", "Verified-closure"),
    ("readiness-vs-closure", "drafting aid"),
    ("verification-mappings", "Acceptance Criteria and Verification Evidence"),
    ("verification-mappings", "Demonstration"),
    ("status-truthfulness", "status: implemented"),
    ("review-independence", "Role Separation"),
    ("review-independence", "self-certify"),
    ("anti-patterns", "Existence ≠ wiring"),
    ("anti-patterns", "Self-review only"),
    ("lifecycle-gates", "Verified Closure"),
    ("override-semantics", "override may deliver known risk"),
)

_CORE_ANCHORS: dict[str, tuple[tuple[str, str], ...]] = {
    "framework": FRAMEWORK_CORE_ANCHORS,
    "aaref": AAREF_CORE_ANCHORS,
}

# NFR05 forbidden patterns: concrete provider-model / fixed-context / single-language
# build mandates. Adapter filenames and the *concept* of a context window are allowed.
FORBIDDEN_CORE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("provider-model-version", r"\bOpus\s*\d"),
    ("provider-model-version", r"\bSonnet\s*\d"),
    ("provider-model-version", r"\bClaude\s*\d"),
    ("provider-model-version", r"\bGPT-?\d"),
    ("provider-model-version", r"\bgpt-4"),
    ("provider-model-version", r"\bGemini\s*\d"),
    ("provider-model-version", r"\bLlama\s*\d"),
    ("fixed-context-window", r"\b\d{2,3}k[- ]token"),
    ("fixed-context-window", r"\bfixed 128k\b"),
    ("beta-coordination-only", r"\bAgentTeams\b"),
)


def core_anchors(canon_id: str) -> tuple[tuple[str, str], ...]:
    """Required core anchor phrases for ``canon_id`` (fail-closed on unknown id)."""
    try:
        return _CORE_ANCHORS[canon_id]
    except KeyError as exc:
        raise KeyError(f"no core invariant anchors registered for canon: {canon_id}") from exc


def missing_core_anchors(canon_id: str, core_text: str) -> tuple[str, ...]:
    """Return the anchor phrases NOT present in ``core_text`` (empty == 100% coverage)."""
    return tuple(phrase for _family, phrase in core_anchors(canon_id) if phrase not in core_text)


def covered_families(canon_id: str, core_text: str) -> frozenset[str]:
    """Families with at least one present anchor phrase in ``core_text``."""
    return frozenset(family for family, phrase in core_anchors(canon_id) if phrase in core_text)


def all_families(canon_id: str) -> frozenset[str]:
    """Every invariant family declared for ``canon_id``."""
    return frozenset(family for family, _phrase in core_anchors(canon_id))


def scan_forbidden(text: str) -> tuple[tuple[str, str], ...]:
    """Return ``(rule_id, matched_text)`` for every NFR05 portability violation."""
    hits: list[tuple[str, str]] = []
    for rule_id, pattern in FORBIDDEN_CORE_PATTERNS:
        hits.extend((rule_id, match.group(0)) for match in re.finditer(pattern, text))
    return tuple(hits)


__all__ = [
    "AAREF_CORE_ANCHORS",
    "FORBIDDEN_CORE_PATTERNS",
    "FRAMEWORK_CORE_ANCHORS",
    "all_families",
    "core_anchors",
    "covered_families",
    "missing_core_anchors",
    "scan_forbidden",
]
