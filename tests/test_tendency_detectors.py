"""Tests for the deterministic tendency detectors (PRD-QUAL-109 FR-02, FR-04).

False-positive discipline is the point (the 348-false-positive safety-gate
history): every detector must FIRE on a realistic positive corpus AND stay
SILENT on a below-threshold / clean control. Detectors are fail-open isolated —
one raising yields exactly one error finding and never suppresses the others.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.tendencies.detectors import (
    DETECTOR_REGISTRY,
    Corpus,
    Detector,
    Finding,
    detect_learning_saturation,
    detect_prd_count_uniformity,
    detect_resnapshot_rotation,
    detect_status_flip_only,
    detect_stub_closure_chain,
    run_detectors,
)
from trw_mcp.tendencies.taxonomy import TendencyType

# ──────────────────────────────────────────────────────────────────────────────
# Fixture-corpus synthesis helpers (miniature handoff-archive / PRD corpora)
# ──────────────────────────────────────────────────────────────────────────────


def _write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _handoff_cycle(cycle: int, prd_count: int, *, extra: str = "") -> str:
    prds = "\n".join(f"- PRD-QUAL-{700 + i:03d}" for i in range(prd_count))
    return f"# Cycle {cycle}\n\nBundle: {prd_count} PRDs this cycle.\n\n{prds}\n{extra}\n"


def _build_uniform_handoff(root: Path, *, cycles: int, prd_count: int) -> None:
    """Synthesize ``cycles`` consecutive handoff blocks each carrying ``prd_count`` PRDs."""
    for c in range(1, cycles + 1):
        _write(root, f"handoff-archive/2026-05-03-cycle-{c:02d}.md", _handoff_cycle(c, prd_count))


def _prd_file(status_only_flip: bool, *, code_change: bool = False) -> str:
    body = "---\nprd:\n  id: PRD-X\n  status: live\n---\n\n# PRD\n"
    if code_change:
        body += "\nSubstantive: refactored the resolver to use a sentinel default.\n"
    return body


# ──────────────────────────────────────────────────────────────────────────────
# FR-02 — PRD-count uniformity (> 5 consecutive cycles of 5-or-6 PRDs → QUOTA_GAMING)
# ──────────────────────────────────────────────────────────────────────────────


def test_prd_count_uniformity_fires_on_six_consecutive(tmp_path: Path) -> None:
    _build_uniform_handoff(tmp_path, cycles=6, prd_count=6)
    corpus = Corpus.from_root(tmp_path)
    findings = detect_prd_count_uniformity(corpus)
    assert len(findings) == 1
    assert findings[0].tendency is TendencyType.QUOTA_GAMING
    assert findings[0].evidence  # cites the cycle range


def test_prd_count_uniformity_silent_on_five_consecutive(tmp_path: Path) -> None:
    # 5 consecutive is exactly the threshold; > 5 is required → no finding.
    _build_uniform_handoff(tmp_path, cycles=5, prd_count=6)
    corpus = Corpus.from_root(tmp_path)
    assert detect_prd_count_uniformity(corpus) == []


def test_prd_count_uniformity_broken_run_does_not_fire(tmp_path: Path) -> None:
    # 4 + (break) + 4 must NOT fire — a single non-matching cycle breaks the run.
    for c in range(1, 5):
        _write(tmp_path, f"handoff-archive/cycle-{c:02d}.md", _handoff_cycle(c, 6))
    _write(tmp_path, "handoff-archive/cycle-05.md", _handoff_cycle(5, 2))  # break
    for c in range(6, 10):
        _write(tmp_path, f"handoff-archive/cycle-{c:02d}.md", _handoff_cycle(c, 6))
    corpus = Corpus.from_root(tmp_path)
    assert detect_prd_count_uniformity(corpus) == []


# ──────────────────────────────────────────────────────────────────────────────
# FR-02 — Stub-closure chain (> 2 in one arc → ADDITIVE_BACKLOG)
# ──────────────────────────────────────────────────────────────────────────────


def test_stub_closure_chain_fires_on_three(tmp_path: Path) -> None:
    headline = "Closes FR-RETRY-JITTER stub from cycle 704"
    _write(
        tmp_path,
        "handoff-archive/arc.md",
        f"# arc\n\n{headline}\n... work ...\n{headline}\nmore\n{headline}\n",
    )
    corpus = Corpus.from_root(tmp_path)
    findings = detect_stub_closure_chain(corpus)
    assert len(findings) == 1
    assert findings[0].tendency is TendencyType.ADDITIVE_BACKLOG
    # all 3 evidence pointers present
    assert len(findings[0].evidence) == 3


def test_stub_closure_chain_silent_on_two(tmp_path: Path) -> None:
    headline = "Closes FR-RETRY-JITTER stub from cycle 704"
    _write(tmp_path, "handoff-archive/arc.md", f"{headline}\n{headline}\n")
    corpus = Corpus.from_root(tmp_path)
    assert detect_stub_closure_chain(corpus) == []


# ──────────────────────────────────────────────────────────────────────────────
# FR-02 — Learning saturation ("Nth application of L-X", N >= 5 → BENCHMARK_SATURATION)
# ──────────────────────────────────────────────────────────────────────────────


def test_learning_saturation_fires_on_n5(tmp_path: Path) -> None:
    _write(tmp_path, "handoff-archive/c.md", "5th application of L-J2Hg to the corpus\n")
    corpus = Corpus.from_root(tmp_path)
    findings = detect_learning_saturation(corpus)
    assert len(findings) == 1
    assert findings[0].tendency is TendencyType.BENCHMARK_SATURATION


def test_learning_saturation_silent_below_five(tmp_path: Path) -> None:
    _write(tmp_path, "handoff-archive/c.md", "4th application of L-J2Hg to the corpus\n")
    corpus = Corpus.from_root(tmp_path)
    assert detect_learning_saturation(corpus) == []


# ──────────────────────────────────────────────────────────────────────────────
# FR-02 — Re-snapshot rotation ("ROUND-N byte-identical" any → BENCHMARK_SATURATION)
# ──────────────────────────────────────────────────────────────────────────────


def test_resnapshot_rotation_fires_on_any(tmp_path: Path) -> None:
    _write(tmp_path, "handoff-archive/c.md", "ROUND-10 byte-identical to ROUND-9\n")
    corpus = Corpus.from_root(tmp_path)
    findings = detect_resnapshot_rotation(corpus)
    assert len(findings) == 1
    assert findings[0].tendency is TendencyType.BENCHMARK_SATURATION


def test_resnapshot_rotation_silent_on_clean(tmp_path: Path) -> None:
    _write(tmp_path, "handoff-archive/c.md", "ROUND-10 added 14 new tests vs ROUND-9\n")
    corpus = Corpus.from_root(tmp_path)
    assert detect_resnapshot_rotation(corpus) == []


# ──────────────────────────────────────────────────────────────────────────────
# FR-02 — Status-flip-only PRD (> 3 per cycle → ADDITIVE_BACKLOG)
# ──────────────────────────────────────────────────────────────────────────────


def test_status_flip_only_fires_on_four(tmp_path: Path) -> None:
    for i in range(4):
        _write(tmp_path, f"prds/PRD-X{i}.md", _prd_file(status_only_flip=True))
    corpus = Corpus.from_root(tmp_path)
    findings = detect_status_flip_only(corpus)
    assert len(findings) == 1
    assert findings[0].tendency is TendencyType.ADDITIVE_BACKLOG


def test_status_flip_only_silent_on_three(tmp_path: Path) -> None:
    for i in range(3):
        _write(tmp_path, f"prds/PRD-X{i}.md", _prd_file(status_only_flip=True))
    corpus = Corpus.from_root(tmp_path)
    assert detect_status_flip_only(corpus) == []


def test_status_flip_with_code_change_is_not_flagged(tmp_path: Path) -> None:
    # 4 PRDs but each ALSO carries a substantive change → not flip-only.
    for i in range(4):
        _write(tmp_path, f"prds/PRD-X{i}.md", _prd_file(status_only_flip=True, code_change=True))
    corpus = Corpus.from_root(tmp_path)
    assert detect_status_flip_only(corpus) == []


# ──────────────────────────────────────────────────────────────────────────────
# FR-02 wiring + NFR-02 fail-open isolation
# ──────────────────────────────────────────────────────────────────────────────


def test_detectors_over_fixture_corpus(tmp_path: Path) -> None:
    """One positive + one below-threshold per detector; every detector fires
    on its positive and stays silent on the control; fail-open isolation holds."""
    # Positive corpus: every signal present at threshold-exceeding strength.
    _build_uniform_handoff(tmp_path, cycles=6, prd_count=6)
    headline = "Closes FR-RETRY-JITTER stub from cycle 704"
    _write(
        tmp_path,
        "handoff-archive/signals.md",
        f"{headline}\n{headline}\n{headline}\n5th application of L-J2Hg\nROUND-10 byte-identical to ROUND-9\n",
    )
    for i in range(4):
        _write(tmp_path, f"prds/PRD-FLIP{i}.md", _prd_file(status_only_flip=True))

    corpus = Corpus.from_root(tmp_path)
    findings = run_detectors(corpus)

    fired = {f.tendency for f in findings if not f.is_error}
    assert TendencyType.QUOTA_GAMING in fired
    assert TendencyType.ADDITIVE_BACKLOG in fired
    assert TendencyType.BENCHMARK_SATURATION in fired


def test_run_detectors_silent_on_clean_corpus(tmp_path: Path) -> None:
    _build_uniform_handoff(tmp_path, cycles=3, prd_count=2)
    _write(tmp_path, "handoff-archive/clean.md", "# normal work\nShipped FR-1 with behavior tests.\n")
    _write(tmp_path, "prds/PRD-OK.md", _prd_file(status_only_flip=True, code_change=True))
    corpus = Corpus.from_root(tmp_path)
    findings = [f for f in run_detectors(corpus) if not f.is_error]
    assert findings == []


def test_fail_open_isolation_one_raising_detector(tmp_path: Path) -> None:
    """A detector raising mid-scan yields exactly one error finding and never
    suppresses the others (mirrors _doctor_core)."""
    _build_uniform_handoff(tmp_path, cycles=6, prd_count=6)
    corpus = Corpus.from_root(tmp_path)

    def _boom(_corpus: Corpus) -> list[Finding]:
        raise RuntimeError("synthetic detector failure")

    registry: dict[str, Detector] = dict(DETECTOR_REGISTRY)
    registry["boom"] = _boom

    findings = run_detectors(corpus, registry=registry)
    errors = [f for f in findings if f.is_error]
    assert len(errors) == 1
    assert "boom" in errors[0].detector_name
    # other detectors still fired (QUOTA_GAMING present)
    fired = {f.tendency for f in findings if not f.is_error}
    assert TendencyType.QUOTA_GAMING in fired


def test_findings_are_sorted_stable(tmp_path: Path) -> None:
    """NFR-03: output ordering is stable (TendencyType then evidence path)."""
    _build_uniform_handoff(tmp_path, cycles=6, prd_count=6)
    _write(tmp_path, "handoff-archive/z.md", "ROUND-10 byte-identical to ROUND-9\n")
    corpus = Corpus.from_root(tmp_path)
    a = run_detectors(corpus)
    b = run_detectors(corpus)
    assert a == b


# ──────────────────────────────────────────────────────────────────────────────
# FR-04 — detector protocol seam + registry markers
# ──────────────────────────────────────────────────────────────────────────────


def test_detector_seam_contract(tmp_path: Path) -> None:
    """A no-op Detector conforms to the protocol and registers via the seam, and
    the registry module documents the LLM target PRD + seam expiry markers."""
    import trw_mcp.tendencies.detectors as det_mod

    def noop_detector(corpus: Corpus) -> list[Finding]:
        return []

    # Conforms structurally to the Detector protocol.
    registered: Detector = noop_detector
    registry: dict[str, Detector] = dict(DETECTOR_REGISTRY)
    registry["noop"] = registered
    corpus = Corpus.from_root(tmp_path)
    out = run_detectors(corpus, registry=registry)
    assert isinstance(out, list)

    # Seam markers present in the registry module source.
    src = Path(det_mod.__file__).read_text(encoding="utf-8")
    assert "llm_detection_target" in src
    assert "seam_expiry" in src


def test_default_registry_has_five_detectors() -> None:
    assert len(DETECTOR_REGISTRY) == 5
