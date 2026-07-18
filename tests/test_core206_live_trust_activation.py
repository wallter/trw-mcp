"""CORE-206 production trust activation through the deferred delivery step."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.trust import read_trust_registry, write_trust_registry
from trw_mcp.tools._deferred_steps_learning import _step_trust_increment
from trw_mcp.tools._evidence_writers import parse_build_command_results, record_build_receipt


def test_default_enforce_path_consumes_one_real_plan_bound_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    source = project / "src" / "feature.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    run = project / ".trw" / "runs" / "task" / "run-1"
    meta = run / "meta"
    meta.mkdir(parents=True)
    (meta / "events.jsonl").write_text(
        json.dumps({"event": "file_modified", "file": str(source)}) + "\n",
        encoding="utf-8",
    )
    (meta / "run.yaml").write_text("run_id: run-1\ntask_type: coding\n", encoding="utf-8")
    commands = parse_build_command_results(
        [
            {"command_id": "tests", "label": "pytest", "command_class": "test", "exit_code": 0},
            {
                "command_id": "static_checks",
                "label": "ruff+mypy",
                "command_class": "static",
                "exit_code": 0,
            },
        ]
    )
    assert commands is not None
    written = record_build_receipt(
        run,
        project,
        tests_passed=True,
        static_checks_clean=True,
        scope_label="full",
        coverage_pct=None,
        policy_mode="enforce",
        command_results=commands,
    )
    assert written is not None and written.ok

    trw_dir = project / ".trw"
    write_trust_registry(
        trw_dir,
        {
            "project": {
                "session_count": 0,
                "successful_sessions": 0,
                "last_session_at": None,
                "tier": "crawl",
                "consumed_trust_outcome_ids": {},
            }
        },
    )
    config = TRWConfig()
    assert config.evidence_receipt_mode == "enforce"
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)

    first = _step_trust_increment(run)
    assert first is not None and first.get("reason") == "outcome_receipt_consumed"
    second = _step_trust_increment(run)
    assert second == {"skipped": True, "reason": "already_consumed_identical"}
    registry = read_trust_registry(trw_dir)["project"]
    assert isinstance(registry, dict)
    assert registry["session_count"] == 1
    assert registry["successful_sessions"] == 1
