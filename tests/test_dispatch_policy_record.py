"""PRD-CORE-290-FR03: the dispatch policy record reaches every surface.

What a dispatch requested versus what its child's command line carried is
written to the active run's events, printed by the CLI's ``--json``, persisted
on a background job and returned by ``trw_dispatch(action="status")``. A model counts as
applied only when the client has a model flag to carry it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.dispatch._types import DispatchRequest, DispatchResult


def _fake_result() -> DispatchResult:
    return DispatchResult(
        client="grok",
        argv_redacted=["grok"],
        read_only_enforced=True,
        exit_code=0,
        timed_out=False,
        duration_s=0.1,
        text="ok",
        raw_stdout="",
        raw_stderr="",
    )


def _policy_events(run: Path) -> list[dict[str, Any]]:
    lines = [line for f in sorted((run / "meta").glob("events-*.jsonl")) for line in f.read_text().splitlines()]
    return [r for r in map(json.loads, lines) if r.get("event_type") == "dispatch_policy"]


def test_a_model_without_a_client_flag_is_requested_not_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch import _policy
    from trw_mcp.dispatch._client_specs import CLIENT_SPECS

    flagless = {**CLIENT_SPECS, "grok": CLIENT_SPECS["grok"].model_copy(update={"model_flag": None})}
    monkeypatch.setattr(_policy, "CLIENT_SPECS", flagless)
    req = DispatchRequest(client="grok", prompt="p", model="grok-4", model_source="config")

    assert _policy.policy_record(req)["model"] == {"requested": "grok-4", "applied": None, "source": "unsupported"}


def test_a_model_with_a_client_flag_is_applied() -> None:
    from trw_mcp.dispatch._policy import policy_record

    req = DispatchRequest(client="grok", prompt="p", model="grok-4", model_source="config")
    assert policy_record(req)["model"] == {"requested": "grok-4", "applied": "grok-4", "source": "config"}


def test_the_policy_is_written_to_the_active_runs_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch._usage import record_dispatch_policy

    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    monkeypatch.setattr("trw_mcp.dispatch._usage._active_run", lambda: run)

    record = record_dispatch_policy(DispatchRequest(client="claude", prompt="p", effort="high"), "job-1")

    (event,) = _policy_events(run)
    assert event["payload"]["child_id"] == "job-1"
    assert event["payload"]["effort"] == record["effort"] == {"requested": "high", "applied": "high", "source": "none"}


def test_the_cli_json_carries_the_policy_and_records_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.dispatch import _cli

    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    monkeypatch.setattr("trw_mcp.dispatch._usage._active_run", lambda: run)
    monkeypatch.setattr(_cli, "dispatch", lambda _req: _fake_result())
    ns = argparse.Namespace(
        client="claude", prompt="x", prompt_file=None, role="adversarial-audit", model=None, effort=None, cwd=None,
        timeout=600, output_file=None, no_isolate=False, allow_writes=False, pty=False, json=True,
    )  # fmt: skip

    with pytest.raises(SystemExit):
        _cli.run_dispatch(ns)

    out = json.loads(capsys.readouterr().out)
    assert out["text"] == "ok"
    assert out["policy"]["effort"] == {"requested": "medium", "applied": "medium", "source": "table"}
    assert out["policy"]["turns"]["source"] == "exempt"
    assert _policy_events(run)[0]["payload"]["effort"] == out["policy"]["effort"]


def test_a_background_job_persists_its_policy_and_status_returns_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp import FastMCP

    from tests.conftest import extract_tool_fn
    from trw_mcp.dispatch import _jobs
    from trw_mcp.tools.dispatch import register_dispatch_tools

    class _Proc:
        pid = 999_999

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(_jobs.subprocess, "Popen", lambda *a, **k: _Proc())
    monkeypatch.setattr(_jobs, "capture_identity", lambda _pid: None)
    job = _jobs.start_background(DispatchRequest(client="grok", prompt="p", effort="low"), trw_dir=tmp_path)
    assert job.policy is not None and job.policy["effort"]["requested"] == "low"

    monkeypatch.setattr("trw_mcp.tools.dispatch.get_status", lambda _id: job)
    server = FastMCP("test")
    register_dispatch_tools(server)
    status = extract_tool_fn(server, "trw_dispatch")(action="status", target=job.job_id)
    assert status["policy"] == job.policy
