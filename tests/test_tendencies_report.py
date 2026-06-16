"""Tests for the advisory tendency report builder (PRD-QUAL-109 FR-03 / NFRs)."""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.tendencies.report import (
    build_report,
    default_corpus_roots,
    render_human,
    render_json,
)


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _dirty_corpus(root: Path) -> None:
    for c in range(1, 7):
        _write(root, f"handoff-archive/cycle-{c:02d}.md", f"# Cycle {c}\nBundle: 6 PRDs this cycle.\n")
    _write(root, "handoff-archive/sig.md", "ROUND-10 byte-identical to ROUND-9\n")


def test_build_report_dirty_corpus_lists_tendencies(tmp_path: Path) -> None:
    _dirty_corpus(tmp_path)
    report = build_report([tmp_path])
    tendencies = {f.tendency.name for f in report.findings if not f.is_error}
    assert "QUOTA_GAMING" in tendencies
    assert "BENCHMARK_SATURATION" in tendencies
    # every finding carries a countermeasure pointer in the rendered payload
    payload = json.loads(render_json(report))
    for f in payload["findings"]:
        assert f["countermeasure"].strip()
        assert f["tendency"]
        assert isinstance(f["evidence"], list)


def test_build_report_clean_corpus_no_tendencies(tmp_path: Path) -> None:
    _write(tmp_path, "handoff-archive/c1.md", "# Cycle 1\nShipped FR-1 with behavior tests.\n")
    report = build_report([tmp_path])
    assert [f for f in report.findings if not f.is_error] == []
    human = render_human(report)
    assert "no tendencies detected" in human.lower()


def test_render_json_is_valid_and_keyed_by_tendency(tmp_path: Path) -> None:
    _dirty_corpus(tmp_path)
    report = build_report([tmp_path])
    payload = json.loads(render_json(report))
    assert "findings" in payload
    for f in payload["findings"]:
        assert set(f.keys()) >= {"tendency", "evidence", "countermeasure"}


def test_report_output_deterministic(tmp_path: Path) -> None:
    """NFR-03 — identical output across two runs on the same corpus."""
    _dirty_corpus(tmp_path)
    a = render_json(build_report([tmp_path]))
    b = render_json(build_report([tmp_path]))
    assert a == b


def test_report_reads_only_corpus_root(tmp_path: Path) -> None:
    """NFR-04 — no reads/writes outside the provided corpus root; snippet bounded."""
    corpus_root = tmp_path / "corpus"
    outside = tmp_path / "outside"
    outside.mkdir(parents=True)
    secret = outside / "secret.md"
    secret.write_text("TOP SECRET should never appear\n" + ("X" * 5000), encoding="utf-8")
    _dirty_corpus(corpus_root)

    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    report = build_report([corpus_root])
    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    # no artifact written anywhere under tmp_path
    assert before == after
    # nothing from the outside dir leaked into evidence
    rendered = render_json(report) + render_human(report)
    assert "TOP SECRET" not in rendered
    # snippet bounding: no single evidence line dumps the 5000-char file
    for f in report.findings:
        for ev in f.evidence:
            assert len(ev) < 400


def test_default_corpus_roots_includes_handoff_and_prds(tmp_path: Path) -> None:
    """Defaults resolve to .trw/distill/handoff-archive + the PRDs path when present."""
    (tmp_path / ".trw" / "distill" / "handoff-archive").mkdir(parents=True)
    (tmp_path / "docs" / "requirements-aare-f" / "prds").mkdir(parents=True)
    roots = default_corpus_roots(tmp_path, prds_relative_path="docs/requirements-aare-f/prds")
    root_strs = {str(r) for r in roots}
    assert any("handoff-archive" in s for s in root_strs)
    assert any("prds" in s for s in root_strs)


def test_default_corpus_roots_skips_absent_paths(tmp_path: Path) -> None:
    # Neither default path exists → empty list (the report runs on an empty corpus).
    roots = default_corpus_roots(tmp_path, prds_relative_path="docs/requirements-aare-f/prds")
    assert roots == []
