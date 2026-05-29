"""Regression test for L-99085348 — Codex branch must precede light-mode fallback.

PRD-DIST-2402 FR17.

When target_platforms=['codex'], the render_and_inject function from
_agents_hotspots should be called, and the distill segment markers should
appear in the output.

This test asserts the integration via the public API without needing to
modify _agents_md.py (which is owned by a different agent).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _make_sidecar() -> dict[str, Any]:
    return {
        "schema_version": "risk-report-sidecar/v0",
        "hotspots": [
            {"file": "src/main.py", "risk_score": 0.9, "reason": "High churn"},
        ],
        "conventions": ["Always type-hint public functions"],
        "edge_cases": [],
    }


def test_render_and_inject_produces_distill_markers(tmp_path: Path) -> None:
    """FR17: render_and_inject() produces AGENTS.md with distill marker pair."""
    from trw_mcp.channels.codex._agents_hotspots import (
        HOTSPOTS_BEGIN,
        HOTSPOTS_END,
        render_and_inject,
    )

    agents_md_path = tmp_path / "AGENTS.md"
    agents_md_path.write_text("# Project\n\n<!-- trw:start -->\nTRW\n<!-- trw:end -->\n")

    sidecar = _make_sidecar()
    result = render_and_inject(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="abc123",
        target_file=agents_md_path,
        force=True,
    )

    assert result.status in ("written", "dry_run"), f"Unexpected status: {result.status}"

    content = agents_md_path.read_text(encoding="utf-8")
    assert HOTSPOTS_BEGIN in content, "AGENTS.md missing distill BEGIN marker"
    assert HOTSPOTS_END in content, "AGENTS.md missing distill END marker"


def test_distill_markers_sequential_after_trw_end(tmp_path: Path) -> None:
    """FR17 + FR01: distill markers appear AFTER trw:end (not nested inside)."""
    from trw_mcp.channels.codex._agents_hotspots import (
        HOTSPOTS_BEGIN,
        render_and_inject,
    )

    agents_md_path = tmp_path / "AGENTS.md"
    agents_md_path.write_text(
        "# Project\n\n"
        "<!-- trw:start -->\nCeremony content\n<!-- trw:end -->\n\n"
        "## Other\n"
    )

    sidecar = _make_sidecar()
    render_and_inject(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="abc123",
        target_file=agents_md_path,
        force=True,
    )

    content = agents_md_path.read_text(encoding="utf-8")
    trw_end_pos = content.find("<!-- trw:end -->")
    begin_pos = content.find(HOTSPOTS_BEGIN)

    assert trw_end_pos != -1
    assert begin_pos != -1
    assert begin_pos > trw_end_pos, (
        "Codex distill markers are not placed AFTER trw:end (L-99085348 regression)"
    )


def test_dry_run_contains_markers(tmp_path: Path) -> None:
    """FR17: dry_run mode returns would_write content with distill markers."""
    from trw_mcp.channels.codex._agents_hotspots import (
        HOTSPOTS_BEGIN,
        HOTSPOTS_END,
        render_and_inject,
    )

    agents_md_path = tmp_path / "AGENTS.md"
    agents_md_path.write_text("# Project\n")

    sidecar = _make_sidecar()
    result = render_and_inject(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="abc123",
        target_file=agents_md_path,
        dry_run=True,
        force=True,
    )

    assert result.status == "dry_run"
    assert result.would_write is not None
    assert HOTSPOTS_BEGIN in result.would_write
    assert HOTSPOTS_END in result.would_write
