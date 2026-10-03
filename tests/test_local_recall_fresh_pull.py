"""FRESH-PULL-CLI-EXIT: ``trw-mcp local recall`` finishes the team pull it started, then asks once more.

A one-shot CLI process cannot finish a pull after it returns (see ``test_sync_fresh_pull_exit``), so the command waits for
the pull it started while the interpreter is alive. When the pull landed, the first answer is stale: the recall runs
once more, so a host's first recall after install already sees the team's learnings.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class _Spy:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.pull_landed = False


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    from trw_mcp.sync import _fresh_pull
    from trw_mcp.tools import _recall_impl

    seen = _Spy()

    def fake_recall(*_a: object, **_k: object) -> dict[str, object]:
        seen.events.append("recall")
        return {"learnings": [{"id": f"answer-{seen.events.count('recall')}"}]}

    def fake_finish() -> bool:
        seen.events.append("drain")
        return seen.pull_landed

    monkeypatch.setattr(_recall_impl, "execute_recall", fake_recall)
    monkeypatch.setattr(_fresh_pull, "finish_inflight", fake_finish)
    return seen


def test_a_pull_that_landed_during_the_recall_makes_the_cli_ask_again(spy: _Spy, tmp_path: Path) -> None:
    from trw_mcp.services.local_surface_service import run_local_recall

    spy.pull_landed = True

    result = run_local_recall("q", trw_dir=tmp_path / ".trw")

    assert spy.events == ["recall", "drain", "recall"]
    assert result["learnings"] == [{"id": "answer-2"}]  # the answer after the pull


def test_with_no_pull_to_wait_for_the_cli_asks_once(spy: _Spy, tmp_path: Path) -> None:
    from trw_mcp.services.local_surface_service import run_local_recall

    result = run_local_recall("q", trw_dir=tmp_path / ".trw")

    assert spy.events == ["recall", "drain"]
    assert result["learnings"] == [{"id": "answer-1"}]
