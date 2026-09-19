"""Duplicate-overlap scan: only file paths count as shared control points."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.validation._prd_integrity_duplicates import _check_duplicate_candidates

_PRDS = "docs/requirements-aare-f/prds"


def _write_prd(root: Path, prd_id: str, title: str, body: str) -> None:
    prds = root / _PRDS
    prds.mkdir(parents=True, exist_ok=True)
    (prds / f"{prd_id}.md").write_text(
        f"---\nprd:\n  id: {prd_id}\n  title: {title}\n  status: draft\n---\n\n{body}\n", encoding="utf-8"
    )


def test_directory_only_references_are_not_overlap(tmp_path: Path) -> None:
    for rel in ("src", "tests", "trw-mcp", "scripts"):
        (tmp_path / rel).mkdir()
    _write_prd(tmp_path, "PRD-CORE-001", "Unrelated recall ranking", "Touches `src/`, `tests/` and `trw-mcp/`.")
    content = "Edits `src/`, `tests/` and `trw-mcp/` only."
    warnings = _check_duplicate_candidates(
        content, {"id": "PRD-QUAL-900", "title": "Pre-commit gates"}, tmp_path, _PRDS
    )
    assert warnings == []


def test_shared_file_paths_still_overlap(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    for name in ("pre-commit.sh", "check_fail_silent.py"):
        (tmp_path / "scripts" / name).write_text("", encoding="utf-8")
    _write_prd(
        tmp_path,
        "PRD-CORE-002",
        "Something else",
        "Changes `scripts/pre-commit.sh` and `scripts/check_fail_silent.py`.",
    )
    content = "Changes `scripts/pre-commit.sh` and `scripts/check_fail_silent.py`."
    warnings = _check_duplicate_candidates(
        content, {"id": "PRD-QUAL-900", "title": "Pre-commit gates"}, tmp_path, _PRDS
    )
    assert len(warnings) == 1
    assert "PRD-CORE-002" in warnings[0]
    assert "scripts/pre-commit.sh" in warnings[0]
