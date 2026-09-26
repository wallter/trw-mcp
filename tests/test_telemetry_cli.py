"""``trw-mcp telemetry events|classify|surface-diff|security|channel-stats|pipeline-health``
(PRD-CORE-300 slices S3a and S3b) — the CLI verbs six former MCP tools moved to
(see ``server/_cli_replacements.py::CLI_REPLACEMENTS`` for the exact name each
replaced), plus the pipeline-health doctor row.

The FR02 shared contract (``--json``, unknown-subcommand exit, refusal) is
exercised generically by ``test_cli_replacement_contract.py``; this file
covers the per-command output shape and each handler's exit-code contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def _cli(argv: list[str], cwd: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    from trw_mcp.server._cli import main

    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "argv", ["trw-mcp", "telemetry", *argv, "--json"])
    try:
        main()
    except SystemExit as exc:
        return 0 if exc.code is None else int(exc.code) if isinstance(exc.code, int) else 1
    return 0


def test_events_with_no_matching_run_returns_an_empty_merged_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _cli(["events"], tmp_path, monkeypatch)
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload == {
        "events": [],
        "count": 0,
        "source_file_count": 0,
        "source_files": [],
        "applied_filters": {},
    }


def test_events_session_id_becomes_an_applied_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _cli(["events", "--session-id", "sess-1"], tmp_path, monkeypatch)
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["applied_filters"] == {"session_id": "sess-1"}


@pytest.mark.parametrize(
    ("path", "expected_classification"),
    [
        (".trw/config.yaml", "control"),
        ("CLAUDE.md", "advisory"),
        ("docs/README.md", "control"),  # FR-8 fail-safe-closed default: untagged -> control
    ],
)
def test_classify_reports_the_same_shape_as_the_former_tool(
    path: str,
    expected_classification: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = _cli(["classify", "--path", path], tmp_path, monkeypatch)
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["path"] == path
    assert payload["classification"] == expected_classification
    assert isinstance(payload["surfaces"], list)
    assert isinstance(payload["rationale"], str)


def test_surface_diff_reports_not_found_without_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing snapshot is a reported result, not an execution failure."""
    code = _cli(["surface-diff", "--snapshot-id-a", "a", "--snapshot-id-b", "b"], tmp_path, monkeypatch)
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["error"] == "snapshot_not_found"
    assert payload["a_found"] is False
    assert payload["b_found"] is False


def test_security_reports_the_mcp_security_status_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _cli(["security"], tmp_path, monkeypatch)
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(payload) == {"registered_servers", "allowlist_hash", "recent_anomalies", "quarantined_servers"}


def test_channel_stats_reports_no_activity_on_an_empty_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _cli(["channel-stats", "--repo-root", str(tmp_path)], tmp_path, monkeypatch)
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["status"] == "no_activity"
    assert payload["channels"] == []


def test_unknown_telemetry_subcommand_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server._cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["trw-mcp", "telemetry", "not-a-real-verb"])
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code) if isinstance(exc.code, int) else 1
    assert code != 0


# ── pipeline-health (S3b) ──────────────────────────────────────────────


def _health_args(*, as_json: bool = True) -> argparse.Namespace:
    return argparse.Namespace(telemetry_command="pipeline-health", as_json=as_json)


def test_run_telemetry_rejects_an_unknown_verb(capsys: pytest.CaptureFixture[str]) -> None:
    from trw_mcp.tools._telemetry_cli import run_telemetry

    with pytest.raises(SystemExit) as exc:
        run_telemetry(argparse.Namespace(telemetry_command="not-a-real-verb", as_json=False))

    assert exc.value.code == 2
    assert "pipeline-health" in capsys.readouterr().err


def test_run_telemetry_pipeline_health_prints_json_and_exits_zero_when_measured(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from trw_mcp.tools._telemetry_cli import run_telemetry

    healthy = {"degraded": False, "advisory": "", "sync_push": {}, "graph_edges": {}}
    with patch("trw_mcp.tools._telemetry_cli.safe_pipeline_health", return_value=healthy):
        with pytest.raises(SystemExit) as exc:
            run_telemetry(_health_args(as_json=True))

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert '"degraded": false' in out


def test_run_telemetry_pipeline_health_exits_one_only_when_unmeasured(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A *degraded* result is a reported outcome (exit 0); a probe that could
    not run at all (``measured: False`` at the top level) is the CLI error."""
    from trw_mcp.tools._telemetry_cli import run_telemetry

    degraded_but_measured = {"degraded": True, "advisory": "pipeline degraded: sync_push"}
    with patch("trw_mcp.tools._telemetry_cli.safe_pipeline_health", return_value=degraded_but_measured):
        with pytest.raises(SystemExit) as exc:
            run_telemetry(_health_args(as_json=False))
    assert exc.value.code == 0

    crashed = {"degraded": False, "measured": False, "advisory": "health_probe_failed"}
    with patch("trw_mcp.tools._telemetry_cli.safe_pipeline_health", return_value=crashed):
        with pytest.raises(SystemExit) as exc:
            run_telemetry(_health_args(as_json=False))
    assert exc.value.code == 1


def test_safe_pipeline_health_reports_measured_false_on_crash() -> None:
    from trw_mcp.tools._telemetry_cli import safe_pipeline_health

    with patch("trw_mcp.state._paths.resolve_trw_dir", side_effect=RuntimeError("boom")):
        result = safe_pipeline_health()

    assert result["measured"] is False
    assert result["degraded"] is False
    for key in ("sync_push", "graph_edges", "embedding_coverage", "recall_feedback"):
        assert result[key]["measured"] is False, key


# ---------------------------------------------------------------------------
# Doctor row
# ---------------------------------------------------------------------------


def test_doctor_pipeline_health_row_warns_when_degraded(tmp_path: Path) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._doctor_pipeline_health import pipeline_health_row

    degraded = {"degraded": True, "advisory": "pipeline degraded: sync_push"}
    with patch("trw_mcp.tools._telemetry_cli.safe_pipeline_health", return_value=degraded):
        status, message = pipeline_health_row(tmp_path, TRWConfig())

    assert status == "WARN"
    assert "trw-mcp telemetry pipeline-health" in message


def test_doctor_pipeline_health_row_passes_when_healthy(tmp_path: Path) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._doctor_pipeline_health import pipeline_health_row

    healthy = {"degraded": False, "advisory": ""}
    with patch("trw_mcp.tools._telemetry_cli.safe_pipeline_health", return_value=healthy):
        status, _message = pipeline_health_row(tmp_path, TRWConfig())

    assert status == "PASS"


def test_doctor_subcommand_includes_the_pipeline_health_row() -> None:
    from trw_mcp.server import _subcommands_doctor as doctor

    assert any(fn_name == "_check_pipeline_health" for _, fn_name in doctor._CHECKS)
