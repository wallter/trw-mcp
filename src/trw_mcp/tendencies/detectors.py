"""Deterministic tendency detectors (PRD-QUAL-109 FR-02) + detector seam (FR-04).

Five pure, deterministic detectors implementing the grep-detectable signals from
``AUDIT-2026-05-17-EXTERNAL-OPERATOR-REVIEW.md`` §8, each emitting zero or more
``Finding`` records. Detectors are pure functions of the corpus bytes — no clock,
no randomness, no environment dependence (NFR-03) — so fixture-corpus tests are
reproducible. Each detector is **fail-open isolated** by ``run_detectors``: an
exception in one becomes a single error finding and never aborts the others
(mirrors ``_subcommands_doctor._doctor_core``).

Detector seam (FR-04)
=====================
A ``Detector`` is any callable ``(Corpus) -> list[Finding]``. New detectors —
including future LLM-assisted ones for the non-grep-able tendencies
(``PREMATURE_SCAFFOLDING``, ``NIH_UNDER_RESEARCH``, ``CLAIM_PROPAGATION``,
``SELF_SILENCING`` semantic nuance) — register by adding an entry to a registry
dict passed to ``run_detectors(registry=...)`` without modifying ``TendencyType``
or the deterministic detectors here.

<!-- llm_detection_target: PRD-TBD -->  (assigned when G3 dynamic-adaptation is
groomed; OQ-2 of PRD-QUAL-109)
<!-- seam_expiry: 2026-12-31 -->  (review the seam for promotion/removal after
this date)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from trw_mcp.tendencies.taxonomy import TendencyType

# ──────────────────────────────────────────────────────────────────────────────
# Corpus + Finding models
# ──────────────────────────────────────────────────────────────────────────────

# A handoff file name carrying a cycle index, e.g. "...-cycle-07.md" or "cycle-7".
_CYCLE_RE = re.compile(r"cycle[-_]?(\d+)", re.IGNORECASE)
# "Bundle: N PRDs" / "N PRDs this cycle" — the per-cycle PRD count signal.
_PRD_COUNT_RE = re.compile(r"(\d+)\s+PRDs?\b", re.IGNORECASE)
# "Closes FR-X stub from cycle K".
_STUB_CLOSURE_RE = re.compile(r"Closes\s+FR-[\w-]+\s+stub\s+from\s+cycle\s+\d+", re.IGNORECASE)
# "Nth application of L-X" — capture N.
_LEARNING_SAT_RE = re.compile(r"(\d+)(?:st|nd|rd|th)\s+application\s+of\s+L-[\w-]+", re.IGNORECASE)
# "ROUND-N byte-identical" (the literal claim; N need not be parsed).
_RESNAPSHOT_RE = re.compile(r"ROUND-\d+\s+byte-identical", re.IGNORECASE)


@dataclass(frozen=True)
class CorpusFile:
    """One corpus file with its text and an optional parsed cycle index."""

    path: Path
    text: str
    cycle: int | None


@dataclass(frozen=True)
class Corpus:
    """An ordered, immutable collection of corpus files (FR-02 input).

    Built from a configurable root via :meth:`from_root`; never hardcodes a
    package path. Treats all content as untrusted text (regex inputs are never
    eval'd) per NFR-04.
    """

    root: Path
    files: tuple[CorpusFile, ...]

    @classmethod
    def from_root(cls, root: Path, *, suffixes: tuple[str, ...] = (".md", ".markdown")) -> Corpus:
        """Walk ``root`` for ``suffixes`` files, reading each as untrusted text.

        Unreadable files are skipped silently here (the detector layer reports
        per-detector errors); ordering is stable (sorted by path) for NFR-03.
        """
        root = Path(root)
        collected: list[CorpusFile] = []
        if root.is_dir():
            paths = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)
        elif root.is_file():
            paths = [root]
        else:
            paths = []
        for p in paths:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            collected.append(CorpusFile(path=p, text=text, cycle=_parse_cycle(p.name)))
        return cls(root=root, files=tuple(collected))


def _parse_cycle(name: str) -> int | None:
    m = _CYCLE_RE.search(name)
    return int(m.group(1)) if m else None


@dataclass(frozen=True)
class Finding:
    """One detector finding (or a fail-open error finding when ``is_error``)."""

    tendency: TendencyType
    detector_name: str
    message: str
    evidence: tuple[str, ...] = field(default_factory=tuple)
    is_error: bool = False

    def sort_key(self) -> tuple[str, str, str]:
        # NFR-03: stable sort by TendencyType then evidence path then detector.
        first_evidence = self.evidence[0] if self.evidence else ""
        return (self.tendency.value, first_evidence, self.detector_name)


class Detector(Protocol):
    """The detector seam (FR-04): a pure callable ``(Corpus) -> list[Finding]``."""

    def __call__(self, corpus: Corpus) -> list[Finding]: ...


def _snippet(text: str, match_start: int, *, width: int = 80) -> str:
    """Bounded matched snippet — never a full-file dump (NFR-04)."""
    line_start = text.rfind("\n", 0, match_start) + 1
    line_end = text.find("\n", match_start)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end].strip()[:width]


def _evidence(path: Path, text: str, match_start: int) -> str:
    line_no = text.count("\n", 0, match_start) + 1
    return f"{path}:{line_no}: {_snippet(text, match_start)}"


# ──────────────────────────────────────────────────────────────────────────────
# FR-02 detector 1 — PRD-count uniformity (> 5 consecutive cycles of 5-or-6 PRDs)
# ──────────────────────────────────────────────────────────────────────────────


def detect_prd_count_uniformity(corpus: Corpus) -> list[Finding]:
    """QUOTA_GAMING — PRD count == 5 or 6 across > 5 consecutive cycles.

    "consecutive" = a maximal run of adjacent cycle indices; a single
    non-matching cycle breaks the run. ``> 5`` is strictly greater (6 consecutive
    matching cycles is the first detection).
    """
    # Map cycle -> (matches threshold band, evidence) using the first count found.
    per_cycle: dict[int, tuple[bool, str]] = {}
    for cf in corpus.files:
        if cf.cycle is None:
            continue
        m = _PRD_COUNT_RE.search(cf.text)
        if m is None:
            per_cycle.setdefault(cf.cycle, (False, str(cf.path)))
            continue
        count = int(m.group(1))
        in_band = count in (5, 6)
        per_cycle[cf.cycle] = (in_band, _evidence(cf.path, cf.text, m.start()))

    findings: list[Finding] = []
    run_cycles: list[int] = []
    for cyc in sorted(per_cycle):
        in_band, _ev = per_cycle[cyc]
        if in_band and (not run_cycles or cyc == run_cycles[-1] + 1):
            run_cycles.append(cyc)
        else:
            findings.extend(_emit_uniformity_run(run_cycles, per_cycle))
            run_cycles = [cyc] if in_band else []
    findings.extend(_emit_uniformity_run(run_cycles, per_cycle))
    return findings


def _emit_uniformity_run(run_cycles: list[int], per_cycle: dict[int, tuple[bool, str]]) -> list[Finding]:
    if len(run_cycles) <= 5:
        return []
    evidence = tuple(per_cycle[c][1] for c in run_cycles)
    lo, hi = run_cycles[0], run_cycles[-1]
    return [
        Finding(
            tendency=TendencyType.QUOTA_GAMING,
            detector_name="prd_count_uniformity",
            message=f"PRD count stayed in the 5-6 band across {len(run_cycles)} consecutive cycles ({lo}-{hi}).",
            evidence=evidence,
        )
    ]


# ──────────────────────────────────────────────────────────────────────────────
# FR-02 detector 2 — Stub-closure chain (> 2 in one arc)
# ──────────────────────────────────────────────────────────────────────────────


def detect_stub_closure_chain(corpus: Corpus) -> list[Finding]:
    """ADDITIVE_BACKLOG — "Closes FR-X stub from cycle K" appearing > 2 times."""
    evidence: list[str] = []
    for cf in corpus.files:
        evidence.extend(_evidence(cf.path, cf.text, m.start()) for m in _STUB_CLOSURE_RE.finditer(cf.text))
    if len(evidence) <= 2:
        return []
    return [
        Finding(
            tendency=TendencyType.ADDITIVE_BACKLOG,
            detector_name="stub_closure_chain",
            message=f"{len(evidence)} stub-closure headlines in the corpus arc (> 2).",
            evidence=tuple(evidence),
        )
    ]


# ──────────────────────────────────────────────────────────────────────────────
# FR-02 detector 3 — Learning saturation ("Nth application of L-X", N >= 5)
# ──────────────────────────────────────────────────────────────────────────────


def detect_learning_saturation(corpus: Corpus) -> list[Finding]:
    """BENCHMARK_SATURATION — "Nth application of L-X" with N >= 5."""
    evidence: list[str] = []
    for cf in corpus.files:
        evidence.extend(
            _evidence(cf.path, cf.text, m.start()) for m in _LEARNING_SAT_RE.finditer(cf.text) if int(m.group(1)) >= 5
        )
    if not evidence:
        return []
    return [
        Finding(
            tendency=TendencyType.BENCHMARK_SATURATION,
            detector_name="learning_saturation",
            message=f"{len(evidence)} learning-saturation headline(s) at N >= 5.",
            evidence=tuple(evidence),
        )
    ]


# ──────────────────────────────────────────────────────────────────────────────
# FR-02 detector 4 — Re-snapshot rotation ("ROUND-N byte-identical", any)
# ──────────────────────────────────────────────────────────────────────────────


def detect_resnapshot_rotation(corpus: Corpus) -> list[Finding]:
    """BENCHMARK_SATURATION — any "ROUND-N byte-identical" claim (threshold any)."""
    evidence: list[str] = []
    for cf in corpus.files:
        evidence.extend(_evidence(cf.path, cf.text, m.start()) for m in _RESNAPSHOT_RE.finditer(cf.text))
    if not evidence:
        return []
    return [
        Finding(
            tendency=TendencyType.BENCHMARK_SATURATION,
            detector_name="resnapshot_rotation",
            message=f"{len(evidence)} byte-identical re-snapshot claim(s).",
            evidence=tuple(evidence),
        )
    ]


# ──────────────────────────────────────────────────────────────────────────────
# FR-02 detector 5 — Status-flip-only PRD (> 3 per cycle)
# ──────────────────────────────────────────────────────────────────────────────

# A PRD body line is "substantive" if it is non-blank prose outside frontmatter
# and not a pure heading/list-of-pointers — anything that looks like real change.
_STATUS_FRONTMATTER_RE = re.compile(r"^\s*status:\s*(live|done|implemented)\b", re.IGNORECASE | re.MULTILINE)
_SUBSTANTIVE_RE = re.compile(r"^Substantive:", re.IGNORECASE | re.MULTILINE)


def detect_status_flip_only(corpus: Corpus) -> list[Finding]:
    """ADDITIVE_BACKLOG — > 3 PRDs whose only substantive change is a terminal
    status flip. A PRD that ALSO carries a substantive change is NOT flagged.
    """
    flip_only: list[str] = []
    for cf in corpus.files:
        if "prd:" not in cf.text and "PRD-" not in cf.text:
            continue
        if not _STATUS_FRONTMATTER_RE.search(cf.text):
            continue
        # Treat a "Substantive:" marker as a real change → exclude from flip-only.
        if _SUBSTANTIVE_RE.search(cf.text):
            continue
        flip_only.append(f"{cf.path}: terminal status flip with no substantive change")
    if len(flip_only) <= 3:
        return []
    return [
        Finding(
            tendency=TendencyType.ADDITIVE_BACKLOG,
            detector_name="status_flip_only",
            message=f"{len(flip_only)} status-flip-only PRDs in the corpus (> 3).",
            evidence=tuple(flip_only),
        )
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Registry + fail-open orchestration (NFR-02)
# ──────────────────────────────────────────────────────────────────────────────

DETECTOR_REGISTRY: dict[str, Detector] = {
    "prd_count_uniformity": detect_prd_count_uniformity,
    "stub_closure_chain": detect_stub_closure_chain,
    "learning_saturation": detect_learning_saturation,
    "resnapshot_rotation": detect_resnapshot_rotation,
    "status_flip_only": detect_status_flip_only,
}


def run_detectors(corpus: Corpus, *, registry: dict[str, Detector] | None = None) -> list[Finding]:
    """Run every registered detector fail-open isolated, returning sorted findings.

    An exception in one detector becomes exactly one error finding for that
    detector and never aborts the others (NFR-02). Output is stably sorted by
    ``TendencyType`` then evidence path (NFR-03).
    """
    reg = registry if registry is not None else DETECTOR_REGISTRY
    findings: list[Finding] = []
    for name, fn in reg.items():
        try:
            findings.extend(fn(corpus))
        except Exception as exc:  # fail-open isolation by design (NFR-02)
            findings.append(
                Finding(
                    tendency=TendencyType.DOC_DRIFT,
                    detector_name=name,
                    message=f"detector '{name}' raised: {exc}",
                    is_error=True,
                )
            )
    findings.sort(key=Finding.sort_key)
    return findings


__all__ = [
    "DETECTOR_REGISTRY",
    "Corpus",
    "CorpusFile",
    "Detector",
    "Finding",
    "detect_learning_saturation",
    "detect_prd_count_uniformity",
    "detect_resnapshot_rotation",
    "detect_status_flip_only",
    "detect_stub_closure_chain",
    "run_detectors",
]
