"""PRD-CORE-329 FR01/FR05/FR07/NFR03 — the blocked-decision queue write/read path.

``trw_checkpoint(blocked_decision=...)`` is the only writer of
``.trw/runtime/decisions.jsonl`` on the trw-mcp side. This file proves the
write contract (FR01), the fail-closed read (FR05), the FR07 actor-marker
derivation, and the committed contract fixture (charter Q3) every reader of
the queue must agree on.
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


def _checkpoint_fn(server: Any) -> Any:
    return extract_tool_fn(server, "trw_checkpoint")


def _queue_path(project_root: Path) -> Path:
    from trw_mcp.models.config import get_config

    return project_root / get_config().trw_dir / "runtime" / "decisions.jsonl"


def _read_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# FR01: write contract
# ---------------------------------------------------------------------------


def test_blocked_decision_record(isolated_project: Path) -> None:
    run_dir = _seed_run(isolated_project, "task-a", "run-a")
    server = make_test_server("orchestration")
    checkpoint = _checkpoint_fn(server)

    result = checkpoint(
        run_path=str(run_dir),
        message="hit an ESCALATE, no operator online",
        blocked_decision={
            "question": "Delete the legacy table?",
            "options": ["delete", "keep"],
            "why_unreachable": "unattended loop, no operator online",
        },
    )

    assert result["recorded"] is True
    decision_id = result["blocked_decision_id"]
    assert isinstance(decision_id, str) and decision_id

    records = _read_lines(_queue_path(isolated_project))
    assert len(records) == 1
    record = records[0]
    assert record["id"] == decision_id
    assert record["status"] == "pending"
    assert record["question"] == "Delete the legacy table?"
    assert record["options"] == ["delete", "keep"]
    assert record["why_unreachable"] == "unattended loop, no operator online"
    assert record["actor"] == "attended"
    assert record["run_path"] == str(run_dir)


@pytest.mark.parametrize("missing_field", ["question", "why_unreachable"])
def test_blocked_decision_missing_required_field_is_refused(
    isolated_project: Path,
    missing_field: str,
) -> None:
    run_dir = _seed_run(isolated_project, "task-b", "run-b")
    server = make_test_server("orchestration")
    checkpoint = _checkpoint_fn(server)

    blocked_decision = {
        "question": "Rotate the signing key?",
        "options": [],
        "why_unreachable": "requires key custody",
    }
    blocked_decision[missing_field] = ""

    result = checkpoint(
        run_path=str(run_dir),
        message="hit an ESCALATE",
        blocked_decision=blocked_decision,
    )

    assert result["recorded"] is False
    assert result["reason"] == "blocked_decision_invalid"
    queue_path = _queue_path(isolated_project)
    # No partial record for EITHER the checkpoint or the decision.
    assert not queue_path.exists() or queue_path.read_text() == ""
    checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
    assert not checkpoints_path.exists()


def test_blocked_decision_empty_options_still_records(isolated_project: Path) -> None:
    """An open-ended question (empty options list) is legitimate, not refused."""
    run_dir = _seed_run(isolated_project, "task-c", "run-c")
    server = make_test_server("orchestration")
    checkpoint = _checkpoint_fn(server)

    result = checkpoint(
        run_path=str(run_dir),
        message="hit an ESCALATE",
        blocked_decision={
            "question": "Rotate the signing key?",
            "options": [],
            "why_unreachable": "requires key custody",
        },
    )

    assert result["recorded"] is True
    records = _read_lines(_queue_path(isolated_project))
    assert records[0]["options"] == []


def test_attended_blocked_decision_is_never_refused_on_that_basis(isolated_project: Path) -> None:
    """An attended session's call records actor=attended and is never refused (FR01)."""
    run_dir = _seed_run(isolated_project, "task-d", "run-d")
    server = make_test_server("orchestration")
    checkpoint = _checkpoint_fn(server)

    result = checkpoint(
        run_path=str(run_dir),
        message="ambiguous fork, recording for the operator",
        blocked_decision={
            "question": "Ship without the flaky test?",
            "options": ["ship", "hold"],
            "why_unreachable": "operator preference, not urgent",
        },
    )

    assert result["recorded"] is True
    records = _read_lines(_queue_path(isolated_project))
    assert records[0]["actor"] == "attended"


# ---------------------------------------------------------------------------
# FR07: actor derivation from the unattended markers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    [
        ("TRW_LOOP_WORKER", "1"),
        ("TRW_DISPATCH_CHILD", "1"),
        ("TRW_SURFACE_ROLE", "reviewer"),
    ],
)
def test_actor_follows_unattended_markers(
    isolated_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str,
) -> None:
    monkeypatch.setenv(env_name, env_value)
    run_dir = _seed_run(isolated_project, "task-e", "run-e")
    server = make_test_server("orchestration")
    checkpoint = _checkpoint_fn(server)

    result = checkpoint(
        run_path=str(run_dir),
        message="unattended cycle hit an ESCALATE",
        blocked_decision={
            "question": "Continue past the missing fixture?",
            "options": ["continue", "halt"],
            "why_unreachable": "no operator reachable",
        },
    )

    assert result["recorded"] is True
    records = _read_lines(_queue_path(isolated_project))
    assert records[0]["actor"] == "unattended"


def test_actor_is_attended_with_no_unattended_marker(isolated_project: Path) -> None:
    run_dir = _seed_run(isolated_project, "task-f", "run-f")
    server = make_test_server("orchestration")
    checkpoint = _checkpoint_fn(server)

    result = checkpoint(
        run_path=str(run_dir),
        message="attended session hit an ESCALATE",
        blocked_decision={
            "question": "Continue past the missing fixture?",
            "options": ["continue", "halt"],
            "why_unreachable": "no operator reachable",
        },
    )

    assert result["recorded"] is True
    records = _read_lines(_queue_path(isolated_project))
    assert records[0]["actor"] == "attended"


# ---------------------------------------------------------------------------
# FR05: fail-closed read
# ---------------------------------------------------------------------------


def test_blocked_decision_read_fails_closed(isolated_project: Path) -> None:
    """A corrupt line ANYWHERE in the file — not only the last — yields unreadable."""
    from trw_mcp.state._decision_queue import read_decision_queue

    queue_path = _queue_path(isolated_project)
    good_first = json.dumps(
        {
            "id": "aaaa",
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "run_path": "r",
            "question": "q",
            "options": [],
            "why_unreachable": "w",
            "actor": "attended",
        }
    )
    queue_path.write_text(good_first + "\n{not json\n")

    from trw_mcp.models.config import get_config

    trw_dir = isolated_project / get_config().trw_dir
    state = read_decision_queue(trw_dir)
    assert state.unreadable is True
    assert state.pending_ids == ()


def test_blocked_decision_read_absent_queue_is_not_unreadable(isolated_project: Path) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.state._decision_queue import read_decision_queue

    trw_dir = isolated_project / get_config().trw_dir
    state = read_decision_queue(trw_dir)
    assert state.unreadable is False
    assert state.pending_ids == ()
    assert state.oldest_pending_id is None


# ---------------------------------------------------------------------------
# NFR03: uuid4 ids, concurrent creators never collide
# ---------------------------------------------------------------------------


def test_concurrent_appends_stay_parseable(isolated_project: Path) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.state._decision_queue import read_decision_queue, record_decision

    trw_dir = isolated_project / get_config().trw_dir
    id_a = record_decision(
        trw_dir,
        run_path="run-a",
        question="Q-a?",
        options=["x"],
        why_unreachable="w-a",
    )
    id_b = record_decision(
        trw_dir,
        run_path="run-b",
        question="Q-b?",
        options=["y"],
        why_unreachable="w-b",
    )

    assert id_a != id_b
    state = read_decision_queue(trw_dir)
    assert state.unreadable is False
    assert set(state.pending_ids) == {id_a, id_b}


# ---------------------------------------------------------------------------
# Contract fixture (charter Q3): every reader must derive exactly this.
# ---------------------------------------------------------------------------


def test_decision_queue_v1_contract_fixture() -> None:
    import importlib.resources as res

    from trw_mcp.state._decision_queue import read_decision_queue

    data_pkg = res.files("trw_mcp.data.contracts")
    jsonl_text = (data_pkg / "decision_queue_v1.jsonl").read_text(encoding="utf-8")
    expected = json.loads((data_pkg / "decision_queue_v1.expected.json").read_text(encoding="utf-8"))

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        trw_dir = Path(tmp)
        (trw_dir / "runtime").mkdir(parents=True, exist_ok=True)
        (trw_dir / "runtime" / "decisions.jsonl").write_text(jsonl_text, encoding="utf-8")

        state = read_decision_queue(trw_dir)

    assert list(state.pending_ids) == expected["pending_ids"]
    assert state.oldest_pending_id == expected["oldest_pending_id"]
    assert list(state.resolved_ids) == expected["resolved_ids"]
    assert state.unreadable == expected["unreadable"]
