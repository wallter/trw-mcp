"""Tests for the ``trw-mcp doctor`` stubs / NotImplementedError section.

Behavioral tests over a tmp project fixture: a PRD catalogue holding stub/partial
frontmatter and source files carrying ``raise NotImplementedError`` produce the
advisory WARN row with the surfaced ids/locations; a clean / non-PRD tree SKIPs.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.server._doctor_stubs import (
    _is_partial_prd,
    build_stubs_message,
    scan_not_implemented,
    scan_partial_prds,
)
from trw_mcp.server._subcommands_doctor import _check_stubs, _doctor_core

_PRDS_REL = "docs/requirements-aare-f/prds"


def _write_prd(catalogue: Path, name: str, body: str) -> None:
    path = catalogue / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _partial_prd(prd_id: str) -> str:
    return (
        "---\n"
        "prd:\n"
        f"  id: {prd_id}\n"
        "  status: implemented\n"
        "  functionality_level: partial\n"
        "  stubs:\n"
        "  - location: foo.py — placeholder\n"
        "    activation_gate: TBD\n"
        "---\n\n"
        "## body\n"
    )


def _full_prd(prd_id: str) -> str:
    return f"---\nprd:\n  id: {prd_id}\n  status: implemented\n  functionality_level: full\n---\n\n## body\n"


# ── _is_partial_prd unit ──────────────────────────────────────────────────────


def test_is_partial_prd_partial_with_stubs() -> None:
    fm: dict[str, object] = {"prd": {"id": "PRD-X-001", "functionality_level": "partial", "stubs": [{"location": "a"}]}}
    assert _is_partial_prd(fm) is True


def test_is_partial_prd_stub_status_implemented() -> None:
    fm: dict[str, object] = {"prd": {"id": "PRD-X-002", "functionality_level": "stub", "status": "implemented"}}
    assert _is_partial_prd(fm) is True


def test_is_partial_prd_full_level_excluded() -> None:
    fm: dict[str, object] = {"prd": {"id": "PRD-X-003", "functionality_level": "full", "stubs": [{"location": "a"}]}}
    assert _is_partial_prd(fm) is False


def test_is_partial_prd_partial_but_no_stubs_no_implemented_excluded() -> None:
    """partial level but empty stubs AND not implemented -> not flagged."""
    fm: dict[str, object] = {
        "prd": {"id": "PRD-X-004", "functionality_level": "partial", "status": "draft", "stubs": []}
    }
    assert _is_partial_prd(fm) is False


def test_is_partial_prd_top_level_fallback() -> None:
    """functionality_level at top level (not under prd:) is tolerated."""
    fm: dict[str, object] = {"functionality_level": "partial", "stubs": [{"x": 1}]}
    assert _is_partial_prd(fm) is True


# ── scan helpers ──────────────────────────────────────────────────────────────


def test_scan_partial_prds_finds_only_partial(tmp_path: Path) -> None:
    catalogue = tmp_path / _PRDS_REL
    _write_prd(catalogue, "PRD-A.md", _partial_prd("PRD-A-001"))
    _write_prd(catalogue, "PRD-B.md", _full_prd("PRD-B-002"))
    _write_prd(catalogue, "sub/PRD-C.md", _partial_prd("PRD-C-003"))
    ids = scan_partial_prds(catalogue)
    assert ids == ["PRD-A-001", "PRD-C-003"]


def test_scan_not_implemented_finds_sites_skips_tests(tmp_path: Path) -> None:
    src = tmp_path / "pkg" / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "real.py").write_text("def f():\n    raise NotImplementedError\n", encoding="utf-8")
    # A test file site must be skipped (tests/ excluded).
    tests = tmp_path / "pkg" / "src" / "pkg" / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def t():\n    raise NotImplementedError\n", encoding="utf-8")
    hits = scan_not_implemented(tmp_path)
    assert len(hits) == 1
    assert hits[0].endswith("real.py:2")
    assert all("tests" not in h for h in hits)


# ── build_stubs_message + _check_stubs ───────────────────────────────────────


def test_build_message_skip_on_empty_tree(tmp_path: Path) -> None:
    """No PRD catalogue and no NotImplementedError sites -> SKIP (fail-open)."""
    status, message = build_stubs_message(tmp_path, _PRDS_REL)
    assert status == "SKIP"
    assert "advisory" in message.lower()


def test_build_message_warn_on_partial_prd(tmp_path: Path) -> None:
    catalogue = tmp_path / _PRDS_REL
    _write_prd(catalogue, "PRD-A.md", _partial_prd("PRD-A-001"))
    status, message = build_stubs_message(tmp_path, _PRDS_REL)
    assert status == "WARN"
    assert "1 stub/partial PRD" in message
    assert "PRD-A-001" in message


def test_build_message_warn_on_not_implemented(tmp_path: Path) -> None:
    src = tmp_path / "pkg" / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "real.py").write_text("def f():\n    raise NotImplementedError()\n", encoding="utf-8")
    status, message = build_stubs_message(tmp_path, _PRDS_REL)
    assert status == "WARN"
    assert "raise NotImplementedError site" in message
    assert "real.py:2" in message


def test_build_message_pass_when_catalogue_clean(tmp_path: Path) -> None:
    """A PRD catalogue with only full PRDs and no NotImplementedError -> PASS."""
    catalogue = tmp_path / _PRDS_REL
    _write_prd(catalogue, "PRD-B.md", _full_prd("PRD-B-002"))
    status, message = build_stubs_message(tmp_path, _PRDS_REL)
    assert status == "PASS"
    assert "0 stub/partial PRDs" in message


def test_check_stubs_integrates_in_doctor_core(tmp_path: Path) -> None:
    """The stubs row appears in the full doctor catalogue and is advisory."""
    catalogue = tmp_path / _PRDS_REL
    _write_prd(catalogue, "PRD-A.md", _partial_prd("PRD-A-001"))
    config = TRWConfig(target_platforms=["claude-code"])
    results = _doctor_core(tmp_path, config)
    stubs = next((r for r in results if r.name == "stubs"), None)
    assert stubs is not None
    assert stubs.status == "WARN"
    assert "PRD-A-001" in stubs.message


def test_check_stubs_returns_check_result(tmp_path: Path) -> None:
    """_check_stubs wrapper returns a CheckResult named 'stubs'."""
    config = TRWConfig(target_platforms=["claude-code"])
    result = _check_stubs(tmp_path, config)
    assert result.name == "stubs"
    assert result.status in {"PASS", "WARN", "SKIP"}
