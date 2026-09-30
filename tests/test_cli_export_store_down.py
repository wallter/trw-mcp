"""INC-075: ``trw-mcp export`` with the memory store down prints one plain line with the remedy, not a traceback."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest


def test_export_with_the_store_down_prints_one_error_line_and_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server._subcommands import _run_export
    from trw_mcp.state._store_selection import StoreUnavailableError

    remedy = "memory store unavailable: the daemon is not running; start it with `trw-mcp memory start`"

    def store_down(*_a: object, **_k: object) -> dict[str, object]:
        raise StoreUnavailableError(remedy)

    monkeypatch.setattr("trw_mcp.export.export_data", store_down)
    args = argparse.Namespace(target_dir=str(tmp_path), scope="learnings", format="csv", output=None)
    with pytest.raises(SystemExit) as exited:
        _run_export(args)
    captured = capsys.readouterr()
    assert exited.value.code == 1
    assert captured.err.strip() == f"Error: {remedy}"
    assert "Traceback" not in captured.err + captured.out
