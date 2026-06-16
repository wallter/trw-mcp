"""Unit tests for PRD-SCALE-001 Scout signal computation (FR01, NFR04)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.cognitive_scaling import _scout_signals as sig


def test_extract_symbols_is_deterministic_and_capped() -> None:
    """Symbols are de-duped order-preserving and capped (language-agnostic)."""
    text = "tenant_id column orders; orders orders TenantScopedBase mixin a bb"
    out = sig._extract_symbols(text, limit=3)
    # De-dup order-preserving, capped at 3; short toks (a, bb) skipped.
    assert out == ["tenant_id", "column", "orders"]
    # Deterministic: same input -> same output.
    assert sig._extract_symbols(text, limit=3) == out


def test_compute_blast_radius_no_symbols_is_unavailable() -> None:
    """No usable symbols -> signal unavailable (not a false zero-hit)."""
    count, hit, available = sig.compute_blast_radius([], project_root=Path("/tmp"), threshold=10)
    assert (count, hit, available) == (0, False, False)


def test_compute_blast_radius_counts_grep_hits(tmp_path: Path) -> None:
    """grep fan-out over a real worktree crosses the threshold."""
    (tmp_path / "a.txt").write_text("WidgetService\nWidgetService\nWidgetService\n")
    (tmp_path / "b.txt").write_text("WidgetService used here\n")
    count, hit, available = sig.compute_blast_radius(["WidgetService"], project_root=tmp_path, threshold=3)
    assert available is True
    assert count >= 4
    assert hit is True


def test_compute_churn_non_git_is_unavailable(tmp_path: Path) -> None:
    """A non-git directory degrades churn to unavailable (fail-open)."""
    commits, authors, hit, available = sig.compute_churn(["src/"], project_root=tmp_path, commit_threshold=8)
    assert available is False
    assert (commits, authors, hit) == (0, 0, False)


def test_compute_precedent_gap_failure_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A recall failure marks precedent_gap unavailable (FR12 input)."""

    def _boom(*_a: object, **_k: object) -> list[dict[str, object]]:
        raise RuntimeError("recall down")

    monkeypatch.setattr("trw_mcp.state._memory_recall.recall_learnings", _boom)
    gap, hit, available = sig.compute_precedent_gap("anything", trw_dir=tmp_path)
    assert available is False
    assert gap == "HIGH"


def test_compute_precedent_gap_no_hits_is_high(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero recall hits -> HIGH gap + threshold hit (no precedent)."""
    monkeypatch.setattr(
        "trw_mcp.state._memory_recall.recall_learnings",
        lambda *a, **k: [],
    )
    gap, hit, available = sig.compute_precedent_gap("novel task", trw_dir=tmp_path)
    assert (gap, hit, available) == ("HIGH", True, True)


def test_compute_precedent_gap_many_hits_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """>=3 recall hits -> strong precedent (NONE, no hit)."""
    monkeypatch.setattr(
        "trw_mcp.state._memory_recall.recall_learnings",
        lambda *a, **k: [{"id": i} for i in range(5)],
    )
    gap, hit, available = sig.compute_precedent_gap("known task", trw_dir=tmp_path)
    assert (gap, hit, available) == ("NONE", False, True)
