"""A run may outlive the PRD it was scoped to — that must not kill its receipt.

Regression guard for the defect found 2026-07-28. `_governing_prd_paths` used a
single `len(matches) != 1` check and raised "did not resolve uniquely" for a
MISSING PRD as well as an ambiguous one. Because `record_build_receipt` is
fail-open, the receipt was then silently skipped — so every `trw_build_check` on
such a run wrote no typed receipt, and `trw_deliver` blocked forever citing
"content-stale build evidence" against the last good receipt. The operator saw a
staleness message about a file, for a run whose real problem was a PRD id that no
longer resolved to anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _resolve(tmp_path: Path, ids: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    from trw_mcp.tools import _evidence_writers as ew

    prds = tmp_path / "docs" / "requirements-aare-f" / "prds"
    prds.mkdir(parents=True, exist_ok=True)
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)

    def _fake_discover(_run_path: Path, _config: object) -> tuple[str, ...]:
        return ids

    import trw_mcp.state.prd_utils as prd_utils

    original = prd_utils.discover_governing_prds
    prd_utils.discover_governing_prds = _fake_discover  # type: ignore[assignment]
    try:
        return ew._governing_prd_paths(run, tmp_path)
    finally:
        prd_utils.discover_governing_prds = original  # type: ignore[assignment]


def test_absent_prd_does_not_block_the_receipt(tmp_path: Path) -> None:
    """The bug: a run scoped to an archived PRD could never write a receipt."""
    ids, paths = _resolve(tmp_path, ("PRD-GONE-001",))
    assert ids == ("PRD-GONE-001",), "the id is still reported — the run really was scoped to it"
    assert paths == (), "no path binds, and that is not an error"


def test_a_present_prd_still_binds(tmp_path: Path) -> None:
    """Control: without this the test above would pass on a resolver that returns nothing."""
    prds = tmp_path / "docs" / "requirements-aare-f" / "prds"
    prds.mkdir(parents=True, exist_ok=True)
    (prds / "PRD-HERE-001-thing.md").write_text("# x", encoding="utf-8")
    _ids, paths = _resolve(tmp_path, ("PRD-HERE-001",))
    assert paths == ("docs/requirements-aare-f/prds/PRD-HERE-001-thing.md",)


def test_a_partially_missing_set_still_binds_what_resolved(tmp_path: Path) -> None:
    prds = tmp_path / "docs" / "requirements-aare-f" / "prds"
    prds.mkdir(parents=True, exist_ok=True)
    (prds / "PRD-HERE-002.md").write_text("# x", encoding="utf-8")
    _ids, paths = _resolve(tmp_path, ("PRD-HERE-002", "PRD-GONE-002"))
    assert paths == ("docs/requirements-aare-f/prds/PRD-HERE-002.md",)


def test_two_files_claiming_one_id_still_raises(tmp_path: Path) -> None:
    """Ambiguity is a real integrity problem and must NOT be softened along with absence.

    Binding evidence to an arbitrary one of two files claiming the same
    requirement id would be worse than refusing.
    """
    prds = tmp_path / "docs" / "requirements-aare-f" / "prds"
    prds.mkdir(parents=True, exist_ok=True)
    (prds / "PRD-DUP-001-a.md").write_text("# a", encoding="utf-8")
    (prds / "PRD-DUP-001-b.md").write_text("# b", encoding="utf-8")
    with pytest.raises(ValueError, match="matches 2 files"):
        _resolve(tmp_path, ("PRD-DUP-001",))
