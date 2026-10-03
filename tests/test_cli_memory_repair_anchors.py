"""``trw-mcp memory repair-anchors``: loop the anchor repair to completion and report its counts (PRD-FIX-159 FR05)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from trw_memory.models.memory import Anchor, MemoryEntry

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.server import _cli_argparse_memory
from trw_mcp.server._subcommands_memory import run_memory

pytestmark = pytest.mark.usefixtures("fake_memory_store")


def _seed(store: FakeMemoryStore, entry_id: str, *files: str) -> None:
    anchors = [
        Anchor.model_construct(file=f, symbol_name="sym", symbol_type="function", signature="", line_range=None)
        for f in files
    ]
    store.rows[(FAKE_NAMESPACE, entry_id)] = MemoryEntry(
        id=entry_id, content=f"lesson {entry_id}", namespace=FAKE_NAMESPACE, anchors=anchors
    )


def _run(tmp_path: Path, *extra: str) -> argparse.Namespace:
    """Parse the real subparser and dispatch it."""
    parser = argparse.ArgumentParser()
    _cli_argparse_memory.add_memory_subcommands(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["memory", "repair-anchors", "--target-dir", str(tmp_path), *extra])
    run_memory(args)
    return args


def _exit_code(tmp_path: Path, *extra: str) -> object:
    with pytest.raises(SystemExit) as exc:
        _run(tmp_path, *extra)
    return exc.value.code


def test_the_verb_loops_pages_until_the_marker_and_prints_every_count(
    fake_memory_store: FakeMemoryStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from trw_mcp.state import _anchor_repair

    monkeypatch.setattr(_anchor_repair, "_PAGE", 2)
    (tmp_path / ".trw").mkdir()
    for i in range(3):
        _seed(fake_memory_store, f"L{i}", f".trw/worktrees/wt/src/f{i}.py", "private/tmp/x/gone.py")
    _seed(fake_memory_store, "LABS", "/abs/x.py", "Users/bob/maybe.py")

    assert _exit_code(tmp_path, "--json") == 0

    counts = json.loads(capsys.readouterr().out)
    assert counts == {
        "rewritten": 4,
        "dropped_absolute": 1,
        "dropped_temp": 3,
        "ambiguous": 1,
        "held": False,
        "validity_refreshed": 4,
        "complete": True,
    }
    assert (tmp_path / ".trw" / "context" / "anchors_repo_relative").exists()


def test_the_plain_output_names_each_count(
    fake_memory_store: FakeMemoryStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".trw").mkdir()
    _seed(fake_memory_store, "L1", ".trw/worktrees/wt/src/a.py")

    assert _exit_code(tmp_path) == 0

    out = capsys.readouterr().out
    assert "rewritten 1" in out and "dropped_temp 0" in out and "held 0" in out


def test_a_held_row_exits_four(
    fake_memory_store: FakeMemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".trw").mkdir()
    _seed(fake_memory_store, "L1", ".trw/worktrees/wt/src/a.py")
    monkeypatch.setattr(
        fake_memory_store, "correct", lambda lid, patch: {"learning_id": lid, "status": "conflict", "error": "moved"}
    )

    assert _exit_code(tmp_path) == 4
    assert not (tmp_path / ".trw" / "context" / "anchors_repo_relative").exists()


def test_a_target_without_a_trw_dir_is_a_caller_error(tmp_path: Path) -> None:
    assert _exit_code(tmp_path) == 2
