"""PRD-CORE-329 FR02/FR05/NFR01 — trw_status surfaces a pending decision first.

``trw_status`` reads the project-level ``.trw/runtime/decisions.jsonl`` queue
independently of which run it resolved, adds ``blocked_decision`` ahead of
every other advisory/scope-classified field when one is pending, and omits
the key entirely when nothing is pending (NFR01). A resolution whose actor is
``unattended`` never counts as accepted (FR08 rule 3).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import extract_tool_fn, make_test_server

pytestmark = pytest.mark.integration


@pytest.fixture
def isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    from trw_mcp.models.config import _reset_config, get_config

    _reset_config()
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs

    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    config = get_config()
    (tmp_path / config.runs_root).mkdir(parents=True, exist_ok=True)
    (tmp_path / config.trw_dir).mkdir(parents=True, exist_ok=True)
    (tmp_path / config.trw_dir / "runtime").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _seed_run(project_root: Path, task: str, run_id: str) -> Path:
    from trw_mcp.models.config import get_config

    runs_root = project_root / get_config().runs_root
    run_dir = runs_root / task / run_id
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    (run_dir / "meta" / "run.yaml").write_text(
        f"run_id: {run_id}\ntask: {task}\nstatus: active\nphase: implement\ncreated_at: {ts}\n",
        encoding="utf-8",
    )
    (run_dir / "meta" / "events.jsonl").touch()
    return run_dir


def _status_fn(server: Any) -> Any:
    return extract_tool_fn(server, "trw_status")


def _write_line(queue_path: Path, record: dict[str, object]) -> None:
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with queue_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def test_blocked_decision_surfaces_first(isolated_project: Path) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.state._decision_queue import record_decision

    run_dir = _seed_run(isolated_project, "task-a", "run-a")
    trw_dir = isolated_project / get_config().trw_dir
    decision_id = record_decision(
        trw_dir,
        run_path=str(run_dir),
        question="Delete the legacy table?",
        options=["delete", "keep"],
        why_unreachable="unattended loop, no operator online",
    )

    server = make_test_server("orchestration")
    result = _status_fn(server)(run_path=str(run_dir))

    keys = list(result.keys())
    assert "blocked_decision" in keys
    # First key after the run-identity block (run_id/task/phase/status/
    # confidence/framework/task_type/event_count/reflection).
    identity_keys = {
        "run_id",
        "task",
        "phase",
        "status",
        "confidence",
        "framework",
        "task_type",
        "event_count",
        "reflection",
    }
    idx = keys.index("blocked_decision")
    assert set(keys[:idx]) <= identity_keys
    assert result["blocked_decision"]["id"] == decision_id
    assert result["blocked_decision"]["question"] == "Delete the legacy table?"


def test_blocked_decision_omitted_when_none_pending(isolated_project: Path) -> None:
    run_dir = _seed_run(isolated_project, "task-b", "run-b")
    server = make_test_server("orchestration")
    result = _status_fn(server)(run_path=str(run_dir))

    assert "blocked_decision" not in result
    assert "blocked_decisions_pending" not in result


def test_unattended_resolution_does_not_count_as_accepted(isolated_project: Path) -> None:
    """FR08 rule 3: a resolution with actor=unattended leaves the decision pending."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._decision_queue import record_decision

    run_dir = _seed_run(isolated_project, "task-c", "run-c")
    trw_dir = isolated_project / get_config().trw_dir
    decision_id = record_decision(
        trw_dir,
        run_path=str(run_dir),
        question="Continue past the missing fixture?",
        options=["continue", "halt"],
        why_unreachable="no operator reachable",
    )
    _write_line(
        trw_dir / "runtime" / "decisions.jsonl",
        {
            "id": decision_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": "resolved",
            "choice": "continue",
            "actor": "unattended",
        },
    )

    server = make_test_server("orchestration")
    result = _status_fn(server)(run_path=str(run_dir))

    assert "blocked_decision" in result
    assert result["blocked_decision"]["id"] == decision_id


def test_multiple_pending_decisions_add_count(isolated_project: Path) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.state._decision_queue import record_decision

    run_dir = _seed_run(isolated_project, "task-d", "run-d")
    trw_dir = isolated_project / get_config().trw_dir
    record_decision(trw_dir, run_path=str(run_dir), question="Q1?", options=[], why_unreachable="w1")
    record_decision(trw_dir, run_path=str(run_dir), question="Q2?", options=[], why_unreachable="w2")

    server = make_test_server("orchestration")
    result = _status_fn(server)(run_path=str(run_dir))

    assert result["blocked_decisions_pending"] == 2


def test_unreadable_queue_surfaces_unreadable_status(isolated_project: Path) -> None:
    from trw_mcp.models.config import get_config

    run_dir = _seed_run(isolated_project, "task-e", "run-e")
    trw_dir = isolated_project / get_config().trw_dir
    (trw_dir / "runtime" / "decisions.jsonl").write_text("{not json\n")

    server = make_test_server("orchestration")
    result = _status_fn(server)(run_path=str(run_dir))

    assert result["blocked_decision"] == {"status": "unreadable"}


def test_blocked_decision_is_classified_project_scope(isolated_project: Path) -> None:
    from trw_mcp.tools._orchestration_status_assembly import _FIELD_SCOPE

    assert _FIELD_SCOPE["blocked_decision"] == "project"
    assert _FIELD_SCOPE["blocked_decisions_pending"] == "project"
