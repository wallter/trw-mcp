"""B71-110: ``instructions sync --dry-run`` leaves the whole project untouched.

The dry run used to report ``status: dry_run`` while four writers ignored the
flag: the per-client carriers (with backups), REVIEW.md, analytics and the hook
env file. The existing FR03 test watched only CLAUDE.md, so this one compares
every file in a real ``init_project(ide="all")`` project before and after.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._tools_learning_shared import instructions_sync_fn
from trw_mcp.bootstrap import init_project
from trw_mcp.state.claude_md import TRW_AUTO_COMMENT, TRW_MARKER_END, TRW_MARKER_START

# Carriers whose TRW block a sync rewrites; editing them makes each one stale.
_STALE_CARRIERS = (
    ".github/copilot-instructions.md",
    "ANTIGRAVITY.md",
    ".agents/rules/trw-ceremony.md",
    ".codex/INSTRUCTIONS.md",
    ".opencode/INSTRUCTIONS.md",
    "CLAUDE.md",
    "AGENTS.md",
)


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _make_stale(root: Path) -> None:
    for rel in _STALE_CARRIERS:
        path = root / rel
        if path.is_file():
            path.write_text(path.read_text(encoding="utf-8").replace("TRW", "TRW-STALE", 1), encoding="utf-8")


def _init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()
    assert not init_project(tmp_path, ide="all")["errors"]
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda *a, **k: tmp_path)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda *a, **k: tmp_path / ".trw")


def _assert_untouched(root: Path, before: dict[str, bytes]) -> None:
    after = _tree(root)
    assert sorted(after.keys() - before.keys()) == []
    assert sorted(rel for rel in before if after.get(rel) != before[rel]) == []


@pytest.mark.parametrize("state", ["fresh", "stale", "cache_hit_stale"])
def test_dry_run_leaves_every_file_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    _init(tmp_path, monkeypatch)
    if state == "cache_hit_stale":
        # A real sync stores the render hash; a dry run must still render, not take the cache hit.
        instructions_sync_fn(client="all")
    if state != "fresh":
        _make_stale(tmp_path)

    before = _tree(tmp_path)
    result = instructions_sync_fn(client="all", dry_run=True)

    _assert_untouched(tmp_path, before)
    assert result["status"] == "dry_run"
    assert result["instruction_file_synced"] is False
    assert result["review_md"]["status"] == "skipped"
    if state != "fresh":
        assert {Path(d["file"]).name for d in result["diffs"]} >= {"CLAUDE.md", "AGENTS.md"}


def test_dry_run_does_not_heal_a_pointer_carrier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pointer AGENTS.md carrying a stale block is healed only by a real sync."""
    _init(tmp_path, monkeypatch)
    (tmp_path / "AGENTS.md").write_text(
        f"@CLAUDE.md\n\n{TRW_AUTO_COMMENT}\n{TRW_MARKER_START}\nstale\n{TRW_MARKER_END}\n", encoding="utf-8"
    )

    before = _tree(tmp_path)
    instructions_sync_fn(client="all", dry_run=True)

    _assert_untouched(tmp_path, before)


def test_dry_run_records_no_learning_promotion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AGENTS.md learning injection renders the bullets but promotes nothing on a dry run."""
    _init(tmp_path, monkeypatch)
    entry = {"id": "L-dryrun", "summary": "Dry runs never write", "impact": 0.9, "status": "active"}
    monkeypatch.setattr("trw_mcp.state.claude_md._sync.recall_learnings", lambda *a, **k: [entry])
    promoted: list[str] = []
    monkeypatch.setattr(
        "trw_mcp.state.claude_md._agents_md.mark_promoted", lambda _trw_dir, learning_id: promoted.append(learning_id)
    )

    instructions_sync_fn(client="all", dry_run=True)
    assert promoted == []
    instructions_sync_fn(client="all")
    assert promoted == ["L-dryrun"]
