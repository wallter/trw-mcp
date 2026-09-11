"""Explicit repair uses update-project dispatch without framework-update effects."""

import json
from unittest.mock import patch

import pytest

from trw_mcp.server._cli_argparse import _build_arg_parser
from trw_mcp.server._subcommands import _run_update_project


def test_repair_dispatch_passes_bound_and_exact_cursor_without_update(tmp_path, capsys):
    (tmp_path / ".trw").mkdir()
    cursor = {"updated_at": "2026-09-08T12:00:00+00:00", "entry_id": "L-last"}
    args = _build_arg_parser().parse_args(
        ["update-project", str(tmp_path), "--repair-embeddings", "7", "--embedding-after", json.dumps(cursor)]
    )
    result = {"status": "completed", "embedded": 2, "failed": 0, "next_cursor": cursor}
    with (
        patch("trw_mcp.bootstrap.update_project") as update,
        patch("trw_mcp.state._memory_connection.repair_embeddings", create=True, return_value=result) as repair,
        pytest.raises(SystemExit) as exit_info,
    ):
        _run_update_project(args)
    assert exit_info.value.code == 0
    update.assert_not_called()
    assert repair.call_args.args == (tmp_path / ".trw",)
    assert repair.call_args.kwargs["max_entries"] == 7
    assert vars(repair.call_args.kwargs["after"]) == cursor
    assert json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize("bound", ["0", "-1", "1.5", "bad"])
def test_parser_rejects_invalid_repair_bounds(bound):
    with pytest.raises(SystemExit) as caught:
        _build_arg_parser().parse_args(["update-project", "--repair-embeddings", bound])
    assert caught.value.code == 2


@pytest.mark.parametrize("extra", [["--pip-install"], ["--ide", "codex"], ["--dry-run"]])
def test_repair_rejects_mixed_update_options(tmp_path, extra, capsys):
    (tmp_path / ".trw").mkdir()
    args = _build_arg_parser().parse_args(["update-project", str(tmp_path), "--repair-embeddings", "1", *extra])
    with patch("trw_mcp.bootstrap.update_project") as update, pytest.raises(SystemExit) as caught:
        _run_update_project(args)
    assert caught.value.code == 2
    update.assert_not_called()
    assert json.loads(capsys.readouterr().out)["status"] == "error"


@pytest.mark.parametrize(
    "cursor", ["bad", "[]", "{}", '{"updated_at":"x","entry_id":1}', '{"updated_at":"","entry_id":"x"}']
)
def test_repair_rejects_invalid_cursor_without_writes(tmp_path, cursor, capsys):
    (tmp_path / ".trw").mkdir()
    args = _build_arg_parser().parse_args(
        ["update-project", str(tmp_path), "--repair-embeddings", "1", "--embedding-after", cursor]
    )
    with (
        patch("trw_mcp.bootstrap.update_project") as update,
        patch("trw_mcp.state._memory_connection.repair_embeddings", create=True) as repair,
        pytest.raises(SystemExit) as caught,
    ):
        _run_update_project(args)
    assert caught.value.code == 2
    update.assert_not_called()
    repair.assert_not_called()
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_cursor_requires_explicit_repair(tmp_path, capsys):
    args = _build_arg_parser().parse_args(["update-project", str(tmp_path), "--embedding-after", "{}"])
    with patch("trw_mcp.bootstrap.update_project") as update, pytest.raises(SystemExit) as caught:
        _run_update_project(args)
    assert caught.value.code == 2
    update.assert_not_called()
    assert "requires --repair-embeddings" in json.loads(capsys.readouterr().out)["error"]


def test_repair_does_not_create_project(tmp_path, capsys):
    args = _build_arg_parser().parse_args(["update-project", str(tmp_path), "--repair-embeddings", "1"])
    with pytest.raises(SystemExit) as caught:
        _run_update_project(args)
    assert caught.value.code == 2
    assert not (tmp_path / ".trw").exists()
    assert "existing project" in json.loads(capsys.readouterr().out)["error"]


def test_repair_failure_is_json_and_nonzero(tmp_path, capsys):
    (tmp_path / ".trw").mkdir()
    args = _build_arg_parser().parse_args(["update-project", str(tmp_path), "--repair-embeddings", "1"])
    with (
        patch("trw_mcp.bootstrap.update_project") as update,
        patch(
            "trw_mcp.state._memory_connection.repair_embeddings", create=True, side_effect=RuntimeError("unavailable")
        ),
        pytest.raises(SystemExit) as caught,
    ):
        _run_update_project(args)
    assert caught.value.code == 2
    update.assert_not_called()
    assert json.loads(capsys.readouterr().out) == {"status": "error", "error": "unavailable"}


@pytest.mark.parametrize("status,failed,exit_code", [("partial", 0, 0), ("blocked", 0, 1), ("completed", 1, 1)])
def test_repair_result_status_and_counts_control_exit(tmp_path, capsys, status, failed, exit_code):
    (tmp_path / ".trw").mkdir()
    args = _build_arg_parser().parse_args(["update-project", str(tmp_path), "--repair-embeddings", "3"])
    result = {"status": status, "failed": failed, "next_cursor": None}
    with (
        patch("trw_mcp.bootstrap.update_project") as update,
        patch("trw_mcp.state._memory_connection.repair_embeddings", create=True, return_value=result) as repair,
        pytest.raises(SystemExit) as caught,
    ):
        _run_update_project(args)
    assert caught.value.code == exit_code
    assert repair.call_args.kwargs == {"max_entries": 3, "after": None}
    update.assert_not_called()
    assert json.loads(capsys.readouterr().out) == result
