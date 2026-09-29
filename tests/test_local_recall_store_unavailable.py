"""``trw-mcp local recall`` must not report false success when the memory store
is unreachable (daemon down, autostart off, or the store otherwise refuses to
open).

``execute_recall`` already distinguishes this case from a genuinely empty
result by attaching ``store_unavailable`` to the returned dict instead of
raising (see ``trw_mcp.state._memory_recall.recall_learnings``). The CLI
marshaller (``run_local_recall`` / ``_run_local``) used to ignore that field:
``format_local_recall`` only inspects ``learnings``, so an unreachable store
printed "No matching learnings." and exited 0 — a false success indistinguishable
from a store that genuinely has nothing to say.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest


def _invoke_recall(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, result: dict[str, object]) -> None:
    from trw_mcp.server import _subcommands_misc

    def _fake_execute_recall(query: str, trw_dir: Path, config: object, **kwargs: Any) -> dict[str, object]:
        return result

    monkeypatch.setattr("trw_mcp.tools._recall_impl.execute_recall", _fake_execute_recall)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMORY_DAEMON_AUTOSTART", "false")

    _subcommands_misc._run_local(
        argparse.Namespace(
            local_command="recall",
            query="anything",
            tag=[],
            max_results=5,
        )
    )


def test_local_recall_store_unavailable_exits_nonzero_and_reports_on_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unreachable store must be a loud, non-zero failure, not a silent empty list."""
    with pytest.raises(SystemExit) as exit_info:
        _invoke_recall(
            monkeypatch,
            tmp_path,
            result={
                "query": "anything",
                "total_matches": 0,
                "store_unavailable": "StoreUnavailableError: no memory daemon is reachable at 127.0.0.1:7583",
            },
        )
    assert exit_info.value.code != 0
    captured = capsys.readouterr()
    assert "No matching learnings" not in captured.out
    assert "StoreUnavailableError" in captured.err
    # A remedy, not just the raw error class.
    assert "doctor" in captured.err.lower() or "daemon" in captured.err.lower()


def test_local_recall_genuinely_empty_store_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Control: a reachable store with no matches is a real (quiet) success."""
    with pytest.raises(SystemExit) as exit_info:
        _invoke_recall(
            monkeypatch,
            tmp_path,
            result={"query": "anything", "total_matches": 0, "learnings": []},
        )
    assert exit_info.value.code == 0
    captured = capsys.readouterr()
    assert "No matching learnings" in captured.out
    assert captured.err == ""
