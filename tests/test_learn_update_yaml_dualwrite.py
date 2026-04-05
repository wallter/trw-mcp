"""Tests for YAML dual-write of 10 new typed fields in trw_learn_update.

Covers PRD-CORE-110 fix: YAML backup must include all 10 typed fields
(type, nudge_line, expires, confidence, task_type, domain, phase_origin,
phase_affinity, team_origin, protection_tier) when updating via trw_learn_update.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.state.persistence import FileStateWriter


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Set up a minimal .trw/ project structure with one YAML entry."""
    trw = tmp_path / ".trw"
    trw.mkdir()
    entries_dir = trw / "learnings" / "entries"
    entries_dir.mkdir(parents=True)
    (trw / "memory").mkdir()
    # Write a sample YAML entry
    (entries_dir / "test-entry.yaml").write_text(
        "id: L-test\n"
        "summary: test summary\n"
        "detail: test detail\n"
        "status: active\n"
        "impact: 0.5\n"
        "tags: []\n"
        "created: '2026-01-01'\n"
        "updated: '2026-01-01'\n",
        encoding="utf-8",
    )
    return tmp_path


def _make_mock_backend(learning_id: str = "L-test") -> MagicMock:
    mock_entry = MagicMock()
    mock_entry.id = learning_id
    mock_backend = MagicMock()
    mock_backend.get.return_value = mock_entry
    mock_backend.update.return_value = None
    return mock_backend


class TestYamlDualWriteNewFields:
    """Verify YAML backup captures all 10 new PRD-CORE-110 fields."""

    def _run_update_and_capture_yaml(
        self, tmp_project: Path, **kwargs: object
    ) -> dict[str, object]:
        """Run trw_learn_update and return the YAML data that was written."""
        import asyncio

        from fastmcp import FastMCP

        from trw_mcp.tools.learning import register_learning_tools

        server = FastMCP("test")
        register_learning_tools(server)

        async def _get_tool_fn() -> object:
            tools = await server.list_tools()
            for t in tools:
                if t.name == "trw_learn_update":
                    return t.fn
            raise KeyError("trw_learn_update not found")

        fn = asyncio.run(_get_tool_fn())

        trw_dir = tmp_project / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entry_path = entries_dir / "test-entry.yaml"

        # The found data simulating what find_entry_by_id returns
        found_data: dict[str, object] = {
            "id": "L-test",
            "summary": "test summary",
            "detail": "test detail",
            "status": "active",
            "impact": 0.5,
        }

        written_data: dict[str, object] = {}

        def _mock_write_yaml(path: Path, data: dict[str, object]) -> None:
            written_data.update(data)

        with (
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.state.memory_adapter.get_backend",
                return_value=_make_mock_backend(),
            ),
            patch(
                "trw_mcp.tools.learning.get_backend",
                return_value=_make_mock_backend(),
            ),
            patch("trw_mcp.tools.learning.adapter_update") as mock_update,
            patch(
                "trw_mcp.state.analytics.find_entry_by_id",
                return_value=(entry_path, dict(found_data)),
            ),
            patch("trw_mcp.state.analytics.resync_learning_index"),
            patch.object(FileStateWriter, "write_yaml", side_effect=_mock_write_yaml),
        ):
            mock_update.return_value = {
                "learning_id": "L-test",
                "changes": "updated",
                "status": "updated",
            }
            fn(learning_id="L-test", **kwargs)

        return written_data

    def test_type_written_to_yaml(self, tmp_project: Path) -> None:
        """type field is written to YAML backup."""
        data = self._run_update_and_capture_yaml(tmp_project, type="incident")
        assert data.get("type") == "incident"

    def test_nudge_line_written_to_yaml(self, tmp_project: Path) -> None:
        """nudge_line field is written to YAML backup."""
        data = self._run_update_and_capture_yaml(tmp_project, nudge_line="Use X")
        assert data.get("nudge_line") == "Use X"

    def test_expires_written_to_yaml(self, tmp_project: Path) -> None:
        """expires field is written to YAML backup."""
        data = self._run_update_and_capture_yaml(tmp_project, expires="2026-12-31")
        assert data.get("expires") == "2026-12-31"

    def test_confidence_written_to_yaml(self, tmp_project: Path) -> None:
        """confidence field is written to YAML backup."""
        data = self._run_update_and_capture_yaml(tmp_project, confidence="verified")
        assert data.get("confidence") == "verified"

    def test_task_type_written_to_yaml(self, tmp_project: Path) -> None:
        """task_type field is written to YAML backup."""
        data = self._run_update_and_capture_yaml(tmp_project, task_type="bug-fix")
        assert data.get("task_type") == "bug-fix"

    def test_domain_written_to_yaml(self, tmp_project: Path) -> None:
        """domain field is written to YAML backup."""
        data = self._run_update_and_capture_yaml(
            tmp_project, domain=["testing", "mcp"]
        )
        assert data.get("domain") == ["testing", "mcp"]

    def test_phase_origin_written_to_yaml(self, tmp_project: Path) -> None:
        """phase_origin field is written to YAML backup."""
        data = self._run_update_and_capture_yaml(
            tmp_project, phase_origin="IMPLEMENT"
        )
        assert data.get("phase_origin") == "IMPLEMENT"

    def test_phase_affinity_written_to_yaml(self, tmp_project: Path) -> None:
        """phase_affinity field is written to YAML backup."""
        data = self._run_update_and_capture_yaml(
            tmp_project, phase_affinity=["IMPLEMENT", "VALIDATE"]
        )
        assert data.get("phase_affinity") == ["IMPLEMENT", "VALIDATE"]

    def test_team_origin_written_to_yaml(self, tmp_project: Path) -> None:
        """team_origin field is written to YAML backup."""
        data = self._run_update_and_capture_yaml(
            tmp_project, team_origin="sprint-80"
        )
        assert data.get("team_origin") == "sprint-80"

    def test_protection_tier_written_to_yaml(self, tmp_project: Path) -> None:
        """protection_tier field is written to YAML backup."""
        data = self._run_update_and_capture_yaml(
            tmp_project, protection_tier="protected"
        )
        assert data.get("protection_tier") == "protected"

    def test_none_fields_not_written(self, tmp_project: Path) -> None:
        """Fields passed as None are not written to YAML backup."""
        data = self._run_update_and_capture_yaml(tmp_project, type="incident")
        # Only type and updated should be in the written data
        assert "type" in data
        assert "nudge_line" not in data
        assert "expires" not in data
        assert "confidence" not in data
