"""An unavailable or empty learnings store must not shrink a tracked REVIEW.md (canary fold, HB-2 class)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastmcp.exceptions import ToolError

from trw_mcp.state.claude_md._review_md import _REVIEW_TEMPLATE, record_review_md
from trw_mcp.state.claude_md._sync import generate_review_md
from trw_mcp.state.persistence import FileStateReader

_RECALL = "trw_mcp.state.recall_factories.recall_for_review_tags"
_RULES = "\n".join(f"- Flag: rule number {i} (L-{i})" for i in range(22))
# The file an earlier sync wrote, with its recorded hash (REVIEW-MD-USER-EDIT-OVERWRITE keeps any REVIEW.md
# whose bytes TRW did not record as the user's, so an unrecorded stand-in would be preserved as a user edit).
_EXISTING = _REVIEW_TEMPLATE.replace("{learning_entries}", _RULES)


def _project(tmp_path: Path) -> tuple[Path, Path]:
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (tmp_path / "REVIEW.md").write_text(_EXISTING, encoding="utf-8")
    record_review_md(trw_dir, _EXISTING)  # as generate_review_md does, so the file is provably TRW's
    return trw_dir, tmp_path / "REVIEW.md"


def test_an_empty_store_keeps_the_existing_review_md(tmp_path: Path) -> None:
    trw_dir, review = _project(tmp_path)
    with patch(_RECALL, return_value=[]):
        result = generate_review_md(trw_dir, repo_root=tmp_path)

    assert review.read_text(encoding="utf-8") == _EXISTING
    assert result["status"] == "skipped"
    assert "kept" in result["kept_existing"] and "No qualifying" not in review.read_text(encoding="utf-8")


def test_an_unavailable_store_keeps_the_existing_review_md(tmp_path: Path) -> None:
    trw_dir, review = _project(tmp_path)
    with patch(_RECALL, side_effect=ToolError("daemon gone")):
        result = generate_review_md(trw_dir, repo_root=tmp_path)

    assert review.read_text(encoding="utf-8") == _EXISTING
    assert result["status"] == "skipped"
    assert "store unavailable" in result["kept_existing"]


def test_an_explicit_flag_still_allows_the_shrink(tmp_path: Path) -> None:
    trw_dir, review = _project(tmp_path)
    with patch(_RECALL, return_value=[]):
        result = generate_review_md(trw_dir, repo_root=tmp_path, allow_empty=True)

    assert result["status"] == "generated"
    assert "No qualifying learnings" in review.read_text(encoding="utf-8")


def test_a_store_with_learnings_still_regenerates(tmp_path: Path) -> None:
    trw_dir, review = _project(tmp_path)
    with patch(_RECALL, return_value=[{"id": "L-9", "summary": "new rule"}]):
        result = generate_review_md(trw_dir, repo_root=tmp_path)

    assert result["status"] == "generated"
    assert "- Flag: new rule (L-9)" in review.read_text(encoding="utf-8")


def test_the_sync_passes_force_as_the_explicit_flag(tmp_path: Path) -> None:
    """`instructions sync --force` is the explicit flag; a plain sync never passes it."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state.claude_md._sync import execute_claude_md_sync

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    seen: list[tuple[bool, bool]] = []

    def fake(
        trw: Path, repo_root: Path | None = None, *, allow_empty: bool = False, force: bool = False
    ) -> dict[str, object]:
        seen.append((allow_empty, force))
        return {"path": str(tmp_path / "REVIEW.md"), "rules_count": 0, "status": "generated"}

    for force in (False, True):
        with (
            patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.claude_md._sync.generate_review_md", side_effect=fake),
        ):
            execute_claude_md_sync("root", None, get_config(), FileStateReader(), None, "claude-code", force=force)

    # --force sets both: shrink on an empty store, and regenerate a user-edited file.
    assert seen[0] == (False, False) and seen[-1] == (True, True)


def test_recall_switched_off_still_removes_the_learnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The recall gate is the operator asking for an empty learnings section, so the keep-existing guard steps aside."""
    trw_dir, review = _project(tmp_path)
    monkeypatch.setattr("trw_mcp.state._recall_gate.learnings_injection_allowed", lambda *a, **k: False)
    with patch(_RECALL, return_value=[]):
        result = generate_review_md(trw_dir, repo_root=tmp_path)

    assert result["status"] == "generated"
    assert "rule number 3" not in review.read_text(encoding="utf-8")
