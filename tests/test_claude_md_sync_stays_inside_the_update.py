"""The CLAUDE.md sync inside update-project touches nothing outside the update (PRD-CORE-305-FR07, sol round 2).

* It used to swap ``sys.stdout``/``sys.stderr`` (process globals) for the whole
  sync, swallowing every other thread's output meanwhile.
* It ran on a pool thread with a 30 s timeout, then ``shutdown(wait=False)``,
  which does not stop the thread: a slow sync kept writing instruction files
  after the update's transaction had taken its final diff or rolled back.
* The REVIEW.md git-root probe asks git about "the project", which inside an
  install is the target and outside one follows ``TRW_PROJECT_ROOT``.
"""

from __future__ import annotations

import concurrent.futures
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.bootstrap import _ide_targets_finalize, _update_project

_RELEASE_AFTER_S = 0.5


def _blank() -> dict[str, list[str]]:
    return {"errors": [], "warnings": [], "preserved": [], "updated": [], "created": [], "cleaned": []}


def test_overlapping_output_reaches_the_real_streams_during_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Another thread printing while the sync runs is neither captured nor lost."""
    (tmp_path / ".trw").mkdir()
    streams_before = (sys.stdout, sys.stderr)
    seen: dict[str, Any] = {}

    def _fake_sync(**_kwargs: object) -> dict[str, int]:
        def _request() -> None:
            seen["streams"] = (sys.stdout, sys.stderr)
            print("overlapping-request-stdout")
            print("overlapping-request-stderr", file=sys.stderr)

        worker = threading.Thread(target=_request)
        worker.start()
        worker.join()
        return {"learnings_promoted": 0}

    monkeypatch.setattr("trw_mcp.state.claude_md.execute_claude_md_sync", _fake_sync)
    _ide_targets_finalize._run_claude_md_sync(tmp_path, _blank())

    captured = capsys.readouterr()
    assert seen["streams"] == streams_before, "the sync replaced the process-wide stdout/stderr"
    assert "overlapping-request-stdout" in captured.out
    assert "overlapping-request-stderr" in captured.err


def test_a_slow_sync_finishes_inside_the_transaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A sync released only after the old timeout has elapsed still writes before the update's diff, never after.

    The old code gave up waiting (its 30 s timeout, shortened here) and left the
    pool thread running; the thread then wrote after ``_apply_update`` returned.
    The fake sync writes ``AGENTS.md``, the root file the real sync writes; a root
    ``CLAUDE.md`` is no update-owned file since REMOVE-S2, so it is neither
    snapshotted nor reported.
    """
    target = tmp_path / "project"
    (target / ".trw").mkdir(parents=True)
    release, finished = threading.Event(), threading.Event()
    seen: dict[str, Any] = {}

    def _slow_sync(**_kwargs: object) -> dict[str, int]:
        seen["thread"] = threading.current_thread()
        release.wait(timeout=10)
        (target / "AGENTS.md").write_text("# written by the sync\n", encoding="utf-8")
        finished.set()
        return {"learnings_promoted": 0}

    real_result = concurrent.futures.Future.result

    def _short_wait(self: concurrent.futures.Future[Any], timeout: float | None = None) -> Any:
        return real_result(self, timeout=0.05)  # what the 30 s timeout does, without waiting 30 s

    monkeypatch.setattr(concurrent.futures.Future, "result", _short_wait)
    monkeypatch.setattr("trw_mcp.state.claude_md.execute_claude_md_sync", _slow_sync)

    def _writer_phase(root: Path, _data: Path, result: dict[str, list[str]], *_a: object, **_k: object) -> None:
        _ide_targets_finalize._run_claude_md_sync(root, result)

    monkeypatch.setattr(_update_project, "_run_core_update_phases", _writer_phase)
    monkeypatch.setattr(_update_project, "_run_post_update_phases", lambda *_a, **_k: None)
    timer = threading.Timer(_RELEASE_AFTER_S, release.set)
    timer.start()
    result = _blank()
    try:
        _update_project._apply_update(target, target, result, ide=None, on_progress=None, dirty=None, reprovision=None)
        done_inside = finished.is_set()
    finally:
        release.set()
        timer.cancel()
    finished.wait(timeout=5)

    assert done_inside, "the sync was still running when the update's transaction ended"
    reported = "AGENTS.md" in result["created"] or "AGENTS.md" in result["updated"]
    assert reported == (target / "AGENTS.md").exists(), "a sync write escaped the transaction's report"
    assert not any("timed out" in w for w in result["warnings"])
    assert seen["thread"] is threading.current_thread(), "the sync ran on a thread that can outlive the update"


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    return path.resolve()
