"""REVIEW.md still renders when the learnings store is unreadable."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import structlog
from fastmcp.exceptions import ToolError

from trw_mcp.state._store_selection import StoreUnavailableError
from trw_mcp.state.claude_md._sync import generate_review_md

_RECALL = "trw_mcp.state.recall_factories.recall_for_review_tags"


@pytest.mark.parametrize(
    "exc",
    [
        ToolError("[Errno 2] No such file or directory"),
        StoreUnavailableError("store gone"),
        FileNotFoundError(2, "No such file or directory"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_store_failure_still_writes_review_md(tmp_path: Path, exc: Exception) -> None:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    with structlog.testing.capture_logs() as logs, patch(_RECALL, side_effect=exc):
        result = generate_review_md(trw_dir, repo_root=tmp_path)

    name = type(exc).__name__
    assert result["status"] == "generated"
    assert result["rules_count"] == 0
    assert result.get("learnings_skipped") == f"store unavailable: {name}"
    content = (tmp_path / "REVIEW.md").read_text(encoding="utf-8")
    assert f"_Learnings section skipped: store unavailable ({name})._" in content
    assert "## Always check" in content and "## Skip" in content
    warnings = [e for e in logs if e["log_level"] == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["event"] == "review_md_learnings_skipped"
    assert warnings[0]["reason"] == f"store unavailable: {name}"
    assert "exc_info" not in warnings[0]


def test_working_store_output_has_no_skip_line(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    with patch(_RECALL, return_value=[{"id": "L-1", "summary": "check x"}]):
        result = generate_review_md(trw_dir, repo_root=tmp_path)
    assert "learnings_skipped" not in result
    content = (tmp_path / "REVIEW.md").read_text(encoding="utf-8")
    assert "- Flag: check x (L-1)" in content
    assert "skipped" not in content


def test_unexpected_error_still_escapes_to_outer_fail_open(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    with patch(_RECALL, side_effect=ValueError("boom")), pytest.raises(ValueError):
        generate_review_md(trw_dir, repo_root=tmp_path)
    assert not (tmp_path / "REVIEW.md").exists()
