"""Bounded bare-filename basename-index walk (trw_prd_validate latency fix).

The PRD integrity checker builds a basename index by walking the repo once. On
a large monorepo that unbounded ``os.walk`` dominated validate latency (~8s per
PRD). These tests pin the new bounded-walk contract:

- the walk stops at ``path_index_max_files`` / ``path_index_max_seconds`` and
  marks the index partial;
- a partial index degrades to advisory-skip (never a false "no match" warning);
- the exclude-dir set is a single shared constant, extendable via the
  ``path_index_exclude_dirs`` config knob;
- built-in exclude dirs are still pruned (behavioral parity guard, replacing the
  deleted set-equality test).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from structlog.testing import capture_logs

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.validation import _prd_integrity_paths as integ
from trw_mcp.state.validation._path_exclusions import PATH_INDEX_EXCLUDE_DIRS
from trw_mcp.state.validation._prd_integrity_paths import (
    _GLOB_EXCLUDE_DIRS,
    _INDEX_PARTIAL_SENTINEL,
    _check_repo_path_references,
    _populate_basename_index,
)
from trw_mcp.state.validation._prd_scoring_grounding import _PROJECT_FILE_EXCLUDE_DIRS


def _cfg(tmp_path: Path, **overrides: object) -> TRWConfig:
    return TRWConfig(trw_dir=str(tmp_path / ".trw"), **overrides)  # type: ignore[arg-type]


# --- Structural parity: one shared constant, aliased at both walk sites --------


def test_both_exclude_sets_are_the_shared_constant() -> None:
    """Parity is now structural: both historical names ARE the single constant,
    so the two walks can never prune different trees (replaces the old
    set-equality drift test)."""
    assert _GLOB_EXCLUDE_DIRS is PATH_INDEX_EXCLUDE_DIRS
    assert _PROJECT_FILE_EXCLUDE_DIRS is PATH_INDEX_EXCLUDE_DIRS


# --- Behavioral prune guard (carried over from the deleted parity test) -------


def test_builtin_exclude_dir_is_pruned_from_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file under a built-in excluded dir (``coverage``) is not indexed while
    a sibling real file is — proves the constant is actually consulted by the
    dirnames prune, which import-time set-equality could not verify."""
    monkeypatch.setattr(integ, "get_config", lambda: _cfg(tmp_path))
    (tmp_path / "coverage").mkdir()
    (tmp_path / "coverage" / "buried.py").write_text("")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "kept.py").write_text("")

    cache: dict[str, tuple[bool, int]] = {}
    _populate_basename_index(tmp_path, cache)

    assert "kept.py" in cache
    assert "buried.py" not in cache
    assert _INDEX_PARTIAL_SENTINEL not in cache  # small tree — not truncated


# --- Config-knob exclude-dir union --------------------------------------------


def test_config_exclude_knob_extends_pruned_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A project-specific dir named only in ``path_index_exclude_dirs`` is
    pruned in addition to the built-in set."""
    monkeypatch.setattr(integ, "get_config", lambda: _cfg(tmp_path, path_index_exclude_dirs=["myvendor"]))
    (tmp_path / "myvendor").mkdir()
    (tmp_path / "myvendor" / "dep.py").write_text("")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "real.py").write_text("")

    cache: dict[str, tuple[bool, int]] = {}
    _populate_basename_index(tmp_path, cache)

    assert "real.py" in cache
    assert "dep.py" not in cache


# --- Bounded walk: stops early + marks partial --------------------------------


def test_walk_stops_early_and_marks_partial_over_max_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``path_index_max_files=1`` a top-level file trips the cap after the
    first directory, so a file buried in a later subdir is never indexed and the
    index is flagged partial."""
    monkeypatch.setattr(integ, "get_config", lambda: _cfg(tmp_path, path_index_max_files=1))
    (tmp_path / "top.py").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "buried.py").write_text("")

    cache: dict[str, tuple[bool, int]] = {}
    _populate_basename_index(tmp_path, cache)

    assert _INDEX_PARTIAL_SENTINEL in cache
    assert "top.py" in cache
    assert "buried.py" not in cache  # walk broke before descending into sub/


def test_max_seconds_zero_floor_marks_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tiny time budget trips the wall-clock cap after the first directory."""
    monkeypatch.setattr(
        integ,
        "get_config",
        lambda: _cfg(tmp_path, path_index_max_files=10_000, path_index_max_seconds=1e-9),
    )
    (tmp_path / "top.py").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "buried.py").write_text("")

    cache: dict[str, tuple[bool, int]] = {}
    _populate_basename_index(tmp_path, cache)

    assert _INDEX_PARTIAL_SENTINEL in cache


# --- Degrade: partial index never emits a false "no match" warning ------------


def test_partial_index_degrades_to_advisory_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare filename absent from a PARTIAL index yields NO failure — the
    truncated walk cannot prove the file is missing, so we must not emit the
    false 'no match in repo' warning."""
    monkeypatch.setattr(integ, "get_config", lambda: _cfg(tmp_path, path_index_max_files=1))
    (tmp_path / "top.py").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "other.py").write_text("")

    content = "The PRD references `absent_module.py` which is not present."
    with capture_logs() as logs:
        failures = _check_repo_path_references(content, tmp_path)

    assert failures == []
    assert any(e.get("event") == "prd_integrity_bare_filename_partial_skip" for e in logs)


def test_complete_index_still_warns_on_missing_bare_filename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: with a COMPLETE index the same missing bare filename still
    surfaces the 'no match in repo' warning — the degrade is scoped to the
    partial case only, not a blanket suppression."""
    monkeypatch.setattr(integ, "get_config", lambda: _cfg(tmp_path))  # default caps → complete
    (tmp_path / "top.py").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "other.py").write_text("")

    content = "The PRD references `absent_module.py` which is not present."
    failures = _check_repo_path_references(content, tmp_path)

    assert len(failures) == 1
    failure = failures[0]
    assert failure.rule == "repo_path_exists"
    assert failure.severity == "warning"
    assert "absent_module.py" in failure.message


def test_present_bare_filename_resolves_under_complete_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare filename that DOES exist resolves cleanly (no failure) — the
    bounded walk still indexes real files under the default caps."""
    monkeypatch.setattr(integ, "get_config", lambda: _cfg(tmp_path))
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "widget.py").write_text("")

    content = "See `widget.py` for the implementation."
    failures = _check_repo_path_references(content, tmp_path)

    assert failures == []


# --- Loud degrade: partial_report out-param surfaces the skipped signal --------


def test_partial_report_flags_partial_and_counts_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement (d): a truncated index must be OBSERVABLE. With a tiny
    max_files the index goes partial; two absent bare filenames both degrade to
    advisory-skip AND the out-param reports ``path_index_partial=True`` with the
    exact skip count — the signal that makes a silent degrade impossible."""
    monkeypatch.setattr(integ, "get_config", lambda: _cfg(tmp_path, path_index_max_files=1))
    (tmp_path / "top.py").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "other.py").write_text("")

    content = "The PRD references `absent_alpha.py` and `absent_beta.py`."
    report: dict[str, object] = {}
    failures = _check_repo_path_references(content, tmp_path, partial_report=report)

    assert failures == []  # partial index → advisory-skip, no false "no match"
    assert report["path_index_partial"] is True
    assert report["path_index_skipped_refs"] == 2


def test_partial_report_flag_false_when_index_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: a COMPLETE index reports ``path_index_partial=False`` and zero
    skips — the loud marker must fire ONLY on a real truncation, never on a
    healthy index (else the warning becomes noise and gets ignored)."""
    monkeypatch.setattr(integ, "get_config", lambda: _cfg(tmp_path))
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "widget.py").write_text("")

    content = "See `widget.py` and a missing `ghost.py`."
    report: dict[str, object] = {}
    failures = _check_repo_path_references(content, tmp_path, partial_report=report)

    # ghost.py is a real miss under a complete index → one warning fires.
    assert len(failures) == 1
    assert "ghost.py" in failures[0].message
    assert report["path_index_partial"] is False
    assert report["path_index_skipped_refs"] == 0


def test_validate_result_surfaces_path_index_partial_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end (d): a partial index surfaces a LOUD ``path_index_partial:``
    entry in ``validate_prd_quality_v2(...).integrity_warnings`` and does NOT
    raise a false ``repo_path_exists`` failure for the skipped bare filename.
    This is the wired channel an agent actually reads from the MCP output."""
    monkeypatch.setattr(integ, "get_config", lambda: _cfg(tmp_path, path_index_max_files=1))
    (tmp_path / "top.py").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "other.py").write_text("")

    from trw_mcp.state.validation import prd_quality

    content = "# PRD\n\nThe implementation lives in `absent_ghost_module.py`.\n"
    result = prd_quality.validate_prd_quality_v2(content, project_root=str(tmp_path))

    assert any(w.startswith("path_index_partial:") for w in result.integrity_warnings)
    assert not any(f.rule == "repo_path_exists" and "absent_ghost_module.py" in f.message for f in result.failures)


# ---------------------------------------------------------------------------
# PRD-QUAL-121-FR05 / NFR02: changed-scope and full-corpus validation lanes
# ---------------------------------------------------------------------------


def _lane_corpus(tmp_path):
    """Corpus: changed PRD-CORE-001 (clean, depends on 002) + an UNRELATED
    duplicate-identifier collision between two other files."""
    prds = tmp_path / "prds"
    prds.mkdir()
    (prds / "PRD-CORE-001.md").write_text(
        "---\nprd:\n  id: PRD-CORE-001\n  title: Changed thing\n  status: approved\n"
        "  traceability:\n    depends_on:\n      - PRD-CORE-002\n---\n",
        encoding="utf-8",
    )
    (prds / "PRD-CORE-002.md").write_text(
        "---\nprd:\n  id: PRD-CORE-002\n  title: Dependency\n  status: approved\n---\n",
        encoding="utf-8",
    )
    # Unrelated collision: two files own PRD-CORE-153.
    (prds / "PRD-CORE-153.md").write_text("---\nprd:\n  id: PRD-CORE-153\n  title: A\n---\n", encoding="utf-8")
    (prds / "PRD-CORE-153-registry-hygiene.md").write_text(
        "---\nprd:\n  id: PRD-CORE-153\n  title: B\n---\n", encoding="utf-8"
    )
    return prds


def test_prd_qual_121_fr05(tmp_path) -> None:
    """FR05 acceptance: changed scope passes while an unrelated collision exists;
    the consumed results report scoped pass + full-corpus fail/unknown, and the
    scoped pass cannot satisfy release."""
    from trw_mcp.state.validation.prd_integrity import (
        evaluate_changed_scope,
        evaluate_full_corpus,
    )

    prds = _lane_corpus(tmp_path)

    changed = evaluate_changed_scope(prds, ["PRD-CORE-001"])
    assert changed.lane == "changed_scope"
    assert changed.outcome == "pass"  # scoped truth: closure is clean
    assert sorted(changed.evaluated) == ["PRD-CORE-001.md", "PRD-CORE-002.md"]  # dependency closure
    assert changed.full_corpus_status == "unknown"  # never claims corpus truth
    assert changed.scope_digest.startswith("sha256:")
    assert not changed.satisfies_release()  # a scoped pass can never satisfy release

    full = evaluate_full_corpus(prds, baseline_receipt_digest="sha256:abc123")
    assert full.lane == "full_corpus"
    assert full.outcome == "fail"
    assert any("duplicate_identifier: PRD-CORE-153" in finding for finding in full.findings)
    assert full.baseline_comparison == {"baseline_receipt_digest": "sha256:abc123"}
    assert not full.satisfies_release()

    # Positive control: with the collision repaired, full corpus satisfies release.
    (prds / "PRD-CORE-153-registry-hygiene.md").unlink()
    repaired = evaluate_full_corpus(prds)
    assert repaired.outcome == "pass" and repaired.satisfies_release()


def test_prd_qual_121_nfr02(tmp_path) -> None:
    """NFR02: changed-scope work is proportional to the dependency closure;
    full-corpus work exposes duration, item counts, and truncation — and a
    truncated run never claims skipped work passed."""
    from trw_mcp.state.validation.prd_integrity import (
        evaluate_changed_scope,
        evaluate_full_corpus,
    )

    prds = _lane_corpus(tmp_path)

    changed = evaluate_changed_scope(prds, ["PRD-CORE-001"])
    assert changed.item_count == 2  # closure only, not the 4-file corpus
    assert changed.duration_seconds >= 0.0

    truncated = evaluate_full_corpus(prds, max_items=2)
    assert truncated.truncated is True
    assert truncated.item_count == 2
    assert truncated.outcome == "unknown"  # skipped work is never a pass
    assert not truncated.satisfies_release()
    assert any("truncated" in finding for finding in truncated.findings)

    full = evaluate_full_corpus(prds)
    assert full.item_count == 4
    assert full.duration_seconds >= 0.0
    assert full.truncated is False


def test_prd_qual_119_nfr03() -> None:
    """NFR03: blocking reasons stay within bounds with an explicit truncation marker."""
    from trw_mcp.models.gate_decision import (
        MAX_DECISION_REASONS,
        REASONS_TRUNCATED_MARKER,
        CompletionComponent,
        CompletionComponentState,
        derive_effective_completion,
    )

    components = tuple(
        CompletionComponent(component_id=f"component-{index:03d}", state=CompletionComponentState.ABSENT)
        for index in range(50)
    )
    decision = derive_effective_completion("PRD-X", components=components)
    assert len(decision.reasons) == MAX_DECISION_REASONS
    assert decision.reasons[-1] == REASONS_TRUNCATED_MARKER
