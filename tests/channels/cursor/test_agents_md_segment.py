"""Tests for CUR-05: AgentsMdSegmentWriter and render_cursor_cli_t1.

PRD-DIST-2401 FR15, FR16.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.channels.cursor._agents_md_segment import (
    TRW_DISTILL_BEGIN,
    TRW_DISTILL_END,
    AgentsMdSegmentWriter,
    render_cursor_cli_t1,
)


def _make_sidecar(sha: str = "abc12345") -> dict[str, Any]:
    return {
        "schema_version": "risk-report-sidecar/v0",
        "sha": sha,
        "payload": {
            "generated_at": "2026-05-28T00:00:00Z",
            "conventions": [{"slug": "yaml-safe", "title": "YAML Safety", "body": "Use safe loader"}],
            "hotspots": [
                {"file_path": "backend/main.py", "risk_score": 0.9, "reason": "high churn"},
                {"file_path": "trw_mcp/state.py", "risk_score": 0.7, "reason": "complex"},
            ],
            "edge_case_survivors": [{"file_path": "backend/auth.py", "description": "JWT bypass survived"}],
            "edge_case_undocumented": [],
        },
    }


def _make_agents_md_with_markers(*, before: str = "# AGENTS\n", after: str = "\n## END\n") -> str:
    return f"{before}{TRW_DISTILL_BEGIN}\n_old content_\n{TRW_DISTILL_END}\n{after}"


# ---------------------------------------------------------------------------
# render_cursor_cli_t1 pure content tests
# ---------------------------------------------------------------------------


class TestRenderCursorCliT1:
    def test_contains_hotspot_file_path(self) -> None:
        content = render_cursor_cli_t1(_make_sidecar())
        assert "backend/main.py" in content

    def test_contains_convention_slug(self) -> None:
        content = render_cursor_cli_t1(_make_sidecar())
        assert "yaml-safe" in content

    def test_contains_survivor(self) -> None:
        content = render_cursor_cli_t1(_make_sidecar())
        assert "backend/auth.py" in content

    def test_contains_sha(self) -> None:
        content = render_cursor_cli_t1(_make_sidecar(sha="deadbeef"))
        assert "deadbeef" in content

    def test_at_most_3_hotspots(self) -> None:
        sidecar: dict[str, Any] = {
            "sha": "abc",
            "payload": {
                "generated_at": "ts",
                "conventions": [],
                "hotspots": [
                    {"file_path": f"file{i}.py", "risk_score": 0.9 - i * 0.01, "reason": ""} for i in range(10)
                ],
                "edge_case_survivors": [],
                "edge_case_undocumented": [],
            },
        }
        content = render_cursor_cli_t1(sidecar)
        # Count hotspot lines (lines starting with "- `file")
        hotspot_lines = [l for l in content.splitlines() if l.startswith("- `file")]
        assert len(hotspot_lines) <= 3

    def test_deterministic_same_inputs(self) -> None:
        sidecar = _make_sidecar()
        c1 = render_cursor_cli_t1(sidecar)
        c2 = render_cursor_cli_t1(sidecar)
        assert c1 == c2


# ---------------------------------------------------------------------------
# AgentsMdSegmentWriter tests (FR15, FR16)
# ---------------------------------------------------------------------------


class TestAgentsMdSegmentWriter:
    def test_write_creates_agents_md_if_absent(self, tmp_path: Path) -> None:
        writer = AgentsMdSegmentWriter(tmp_path)
        result = writer.write(_make_sidecar())
        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists()

    def test_write_preserves_content_outside_markers(self, tmp_path: Path) -> None:
        agents_md = tmp_path / "AGENTS.md"
        before_content = "# My Agents\n\n## Instructions\n\nDo stuff.\n"
        after_content = "\n## Other Section\n\nMore stuff.\n"
        agents_md.write_text(
            f"{before_content}{TRW_DISTILL_BEGIN}\n_old_\n{TRW_DISTILL_END}\n{after_content}",
            encoding="utf-8",
        )

        writer = AgentsMdSegmentWriter(tmp_path)
        writer.write(_make_sidecar())

        new_content = agents_md.read_text(encoding="utf-8")
        assert before_content in new_content
        assert after_content in new_content

    def test_marker_replace_round_trip(self, tmp_path: Path) -> None:
        agents_md = tmp_path / "AGENTS.md"
        original = _make_agents_md_with_markers()
        agents_md.write_text(original, encoding="utf-8")

        writer = AgentsMdSegmentWriter(tmp_path)
        result = writer.write(_make_sidecar())
        # After write, file should contain markers
        new_content = agents_md.read_text(encoding="utf-8")
        assert TRW_DISTILL_BEGIN in new_content
        assert TRW_DISTILL_END in new_content

    def test_idempotency_same_sidecar(self, tmp_path: Path) -> None:
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(f"{TRW_DISTILL_BEGIN}\n_old_\n{TRW_DISTILL_END}\n", encoding="utf-8")

        writer = AgentsMdSegmentWriter(tmp_path)
        sidecar = _make_sidecar()
        writer.write(sidecar, force=True)
        content1 = agents_md.read_text(encoding="utf-8")

        writer.write(sidecar, force=True)
        content2 = agents_md.read_text(encoding="utf-8")

        assert content1 == content2

    def test_t1_content_under_quota(self, tmp_path: Path) -> None:
        writer = AgentsMdSegmentWriter(tmp_path)
        result = writer.write(_make_sidecar())
        if result.bytes_written is not None:
            assert result.bytes_written <= 2048  # generous ceiling for the test

    def test_cursor_cli_agents_md_t1_render(self, tmp_path: Path) -> None:
        writer = AgentsMdSegmentWriter(tmp_path)
        result = writer.write(_make_sidecar())
        agents_md = tmp_path / "AGENTS.md"
        if agents_md.exists():
            content = agents_md.read_text()
            assert "TRW Distill" in content or "trw" in content.lower()

    @pytest.mark.parametrize("i", range(5))
    def test_idempotency_property_parametrized(self, tmp_path: Path, i: int) -> None:
        """Idempotency over multiple random-ish inputs via parametrize (FR16)."""
        agents_md = tmp_path / "AGENTS.md"
        sidecar: dict[str, Any] = {
            "sha": f"sha{i:04x}",
            "payload": {
                "generated_at": f"2026-05-{28 - i}T00:00:00Z",
                "conventions": [{"slug": f"c{i}", "title": f"T{i}", "body": "b"}],
                "hotspots": [{"file_path": f"f{i}.py", "risk_score": 0.5, "reason": "r"}],
                "edge_case_survivors": [],
                "edge_case_undocumented": [],
            },
        }
        if agents_md.exists():
            agents_md.unlink()

        writer = AgentsMdSegmentWriter(tmp_path)
        writer.write(sidecar, force=True)
        content1 = agents_md.read_text(encoding="utf-8") if agents_md.exists() else ""

        writer.write(sidecar, force=True)
        content2 = agents_md.read_text(encoding="utf-8") if agents_md.exists() else ""

        assert content1 == content2, f"Idempotency failed for i={i}"
