"""Two learnings with the same summary slug on the same day keep separate YAML sidecars (INC-119 h).

The sidecar is named ``{created}-{slug}.yaml``; a second learning whose summary slugs the same on the same date used to
overwrite the first (the SQLite row survives, so the loss was the YAML projection, and the id-to-path resolver then
reported the first learning's sidecar as unresolved).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.learning import LearningEntry
from trw_mcp.state.analytics.entries import save_learning_entry
from trw_mcp.state.persistence import FileStateReader


def _entry(entry_id: str, summary: str = "Same Summary, punctuation differs!") -> LearningEntry:
    return LearningEntry(id=entry_id, summary=summary, detail=f"detail of {entry_id}", tags=["x"], impact=0.5)


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    path = tmp_path / ".trw"
    (path / "learnings" / "entries").mkdir(parents=True)
    return path


def _ids(paths: list[Path]) -> list[str]:
    return [str(FileStateReader().read_yaml(p)["id"]) for p in paths]


def test_a_same_slug_same_day_learning_does_not_overwrite_the_first(trw_dir: Path) -> None:
    first = save_learning_entry(trw_dir, _entry("L-first001"))
    second = save_learning_entry(trw_dir, _entry("L-second02", "Same summary punctuation differs"))
    assert first != second
    assert _ids([first, second]) == ["L-first001", "L-second02"]


def test_resaving_the_same_learning_is_idempotent(trw_dir: Path) -> None:
    first = save_learning_entry(trw_dir, _entry("L-first001"))
    again = save_learning_entry(trw_dir, _entry("L-first001"))
    assert first == again
    assert len(list((trw_dir / "learnings" / "entries").glob("*.yaml"))) == 1


def test_the_id_to_path_resolver_finds_both(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state import memory_adapter
    from trw_mcp.state._entry_paths import resolve_entry_file

    entries = {}
    for entry in (_entry("L-first001"), _entry("L-second02", "Same summary punctuation differs")):
        path = save_learning_entry(trw_dir, entry)
        entries[entry.id] = (path, {"summary": entry.summary, "created": entry.created.isoformat()})
    monkeypatch.setattr(memory_adapter, "find_entry_by_id", lambda _dir, entry_id: entries[entry_id][1])
    for entry_id, (path, _row) in entries.items():
        found = resolve_entry_file(trw_dir / "learnings" / "entries", entry_id, FileStateReader(), trw_dir=trw_dir)
        assert found is not None and found[0] == path
