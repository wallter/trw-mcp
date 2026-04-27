"""Tests for scripts/migrate_nudge_history.py.

Covers the L-SgB1 snapshot migration utility. The script is intentionally
stdlib-only (no trw-mcp import) so it can run when the package is broken;
accordingly we invoke it via ``subprocess`` rather than importing it.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_nudge_history.py"


def _pathological_entry() -> dict[str, Any]:
    return {
        "turn_first_shown": 0,
        "last_shown_turn": 0,
        "phases_shown": ["deliver"],
        "shown_count": 1,
    }


def _healthy_entry(turn: int = 5) -> dict[str, Any]:
    return {
        "turn_first_shown": turn,
        "last_shown_turn": turn,
        "phases_shown": ["implement", "review"],
        "shown_count": 1,
    }


def _write_state(path: Path, history: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"nudge_history": history}, indent=2), encoding="utf-8")


def _run(path: Path, mode: str) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--path", str(path), "--mode", mode],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"script failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    return json.loads(result.stdout.strip())


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.integration
class TestMigrateNudgeHistory:
    def test_count_mode_identifies_pathological_entries(self, tmp_path: Path) -> None:
        """--mode count reports the pathological count and does not mutate."""
        state_path = tmp_path / "ceremony-state.json"
        history = {f"L-bad-{i}": _pathological_entry() for i in range(3)}
        history["L-ok-1"] = _healthy_entry(turn=7)
        history["L-ok-2"] = _healthy_entry(turn=12)
        _write_state(state_path, history)

        summary = _run(state_path, "count")

        assert summary["pathological_count"] == 3
        assert summary["total_entries"] == 5
        assert summary["mode"] == "count"
        assert summary["changed"] == 0

    def test_prune_mode_removes_pathological_entries(self, tmp_path: Path) -> None:
        """--mode prune deletes pathological entries, preserves healthy ones."""
        state_path = tmp_path / "ceremony-state.json"
        history = {
            "L-bad-1": _pathological_entry(),
            "L-bad-2": _pathological_entry(),
            "L-bad-3": _pathological_entry(),
            "L-ok-1": _healthy_entry(turn=7),
            "L-ok-2": _healthy_entry(turn=12),
        }
        _write_state(state_path, history)

        summary = _run(state_path, "prune")
        assert summary["changed"] == 3

        data = json.loads(state_path.read_text(encoding="utf-8"))
        remaining = data["nudge_history"]
        assert set(remaining.keys()) == {"L-ok-1", "L-ok-2"}
        assert remaining["L-ok-1"]["turn_first_shown"] == 7

    def test_tombstone_mode_marks_without_deleting(self, tmp_path: Path) -> None:
        """--mode tombstone keeps entries but zeros pathological fields."""
        state_path = tmp_path / "ceremony-state.json"
        history = {
            "L-bad-1": _pathological_entry(),
            "L-bad-2": _pathological_entry(),
            "L-ok-1": _healthy_entry(turn=9),
        }
        _write_state(state_path, history)

        summary = _run(state_path, "tombstone")
        assert summary["changed"] == 2

        data = json.loads(state_path.read_text(encoding="utf-8"))
        remaining = data["nudge_history"]
        # All three entries still present
        assert set(remaining.keys()) == {"L-bad-1", "L-bad-2", "L-ok-1"}
        # Pathological entries tombstoned
        for lid in ("L-bad-1", "L-bad-2"):
            assert remaining[lid]["phases_shown"] == []
            assert remaining[lid]["turn_first_shown"] == -1
            assert remaining[lid]["last_shown_turn"] == -1
        # Healthy entry untouched
        assert remaining["L-ok-1"]["turn_first_shown"] == 9
        assert remaining["L-ok-1"]["phases_shown"] == ["implement", "review"]

    def test_count_mode_does_not_modify(self, tmp_path: Path) -> None:
        """File on disk is byte-identical before and after --mode count."""
        state_path = tmp_path / "ceremony-state.json"
        history = {
            "L-bad-1": _pathological_entry(),
            "L-ok-1": _healthy_entry(turn=4),
        }
        _write_state(state_path, history)

        before = _file_sha256(state_path)
        _run(state_path, "count")
        after = _file_sha256(state_path)

        assert before == after, "count mode must not mutate the file"
