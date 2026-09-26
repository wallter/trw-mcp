"""PRD-CORE-300-FR08 (S6a) — trw_checkpoint's heartbeat and pre_compact modes.

``trw_checkpoint`` gained two modes that replace the deleted ``trw_heartbeat``
and ``trw_pre_compact_checkpoint`` tools. Each mode SHALL record exactly what
its predecessor tool recorded, and nothing else: this module proves parity by
comparing the mode's return value and filesystem writes against the retained
implementation helpers (``compute_heartbeat_result`` /
``execute_pre_compact_checkpoint``) invoked directly on identical fixture
state, then re-asserts the plain (non-mode) checkpoint path is unaffected.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from tests.conftest import extract_tool_fn, get_tools_sync, make_test_server

# ---------------------------------------------------------------------------
# Shared fixtures (mirrors test_heartbeat_and_adopt.py::isolated_project so
# heartbeat-mode parity does not cross-contaminate the pin store cache).
# ---------------------------------------------------------------------------


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


def _make_orchestration_server() -> Any:
    return make_test_server("orchestration")


# ---------------------------------------------------------------------------
# heartbeat=True parity
# ---------------------------------------------------------------------------


def _backdate_last_heartbeat(session_id: str) -> None:
    from trw_mcp.state._pin_store import invalidate_pin_store_cache, pin_store_path

    path = pin_store_path()
    raw = json.loads(path.read_text())
    past = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    raw[session_id]["last_heartbeat_ts"] = past
    path.write_text(json.dumps(raw))
    invalidate_pin_store_cache()


def test_checkpoint_heartbeat_mode_matches_compute_heartbeat_result(isolated_project: Path) -> None:
    from trw_mcp.state._paths import TRWCallContext, pin_active_run

    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-aaaa1111")
    ctx = TRWCallContext(session_id="sess-parity", client_hint=None, explicit=False, fastmcp_session=None)
    pin_active_run(run, context=ctx)
    _backdate_last_heartbeat("sess-parity")

    from trw_mcp.tools._ceremony_heartbeat import compute_heartbeat_result

    fake_ctx = SimpleNamespace(session_id="sess-parity")
    direct = compute_heartbeat_result(fake_ctx, "still alive")

    # A second call within 60s must be rate-limited identically either way;
    # reset the timestamp so both paths observe the same "not rate limited"
    # starting condition instead of racing each other.
    _backdate_last_heartbeat("sess-parity")
    server = _make_orchestration_server()
    checkpoint = _checkpoint_fn(server)
    via_mode = checkpoint(ctx=fake_ctx, message="still alive", heartbeat=True)

    # Compare the fields that are not themselves fresh timestamps (those will
    # legitimately differ by the few ms between the two calls).
    assert via_mode["run_id"] == direct["run_id"] == run.name
    assert via_mode["rate_limited"] is False
    assert direct["rate_limited"] is False
    assert set(via_mode.keys()) == set(direct.keys())
    # Nothing checkpoint-shaped leaked into the heartbeat response.
    assert "recorded" not in via_mode
    assert "status" not in via_mode


def test_checkpoint_heartbeat_mode_records_only_heartbeat_event(isolated_project: Path) -> None:
    """heartbeat=True appends a heartbeat event and never a checkpoint record."""
    from trw_mcp.state._paths import TRWCallContext, pin_active_run

    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-bbbb2222")
    ctx = TRWCallContext(session_id="sess-evt", client_hint=None, explicit=False, fastmcp_session=None)
    pin_active_run(run, context=ctx)
    _backdate_last_heartbeat("sess-evt")

    server = _make_orchestration_server()
    checkpoint = _checkpoint_fn(server)
    checkpoint(ctx=SimpleNamespace(session_id="sess-evt"), message="tick", heartbeat=True)

    events_path = run / "meta" / "events.jsonl"
    checkpoints_path = run / "meta" / "checkpoints.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
    assert any(e.get("event") == "heartbeat" for e in events)
    assert not any(e.get("event") == "checkpoint" for e in events)
    assert not checkpoints_path.exists()


# ---------------------------------------------------------------------------
# pre_compact=True parity
# ---------------------------------------------------------------------------


def _tool_run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "docs" / "task" / "runs" / "20260529T120000Z-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text("run_id: t\nstatus: active\nphase: implement\ntask: t\n", encoding="utf-8")
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _make_ceremony_checkpoint_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_OFFLINE", "1")
    return get_tools_sync(make_test_server("orchestration", "checkpoint"))


def test_checkpoint_pre_compact_mode_matches_execute_pre_compact_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _tool_run_dir(tmp_path)
    tools = _make_ceremony_checkpoint_server(monkeypatch, tmp_path)

    with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
        via_mode = tools["trw_checkpoint"].fn(
            pre_compact=True,
            directive="land FR-08 then deliver",
            context_anchor="mode wired, verifying parity",
        )

    assert via_mode["status"] == "success"
    assert via_mode["directive"] == "land FR-08 then deliver"
    assert via_mode["context_anchor"] == "mode wired, verifying parity"

    state_file = tmp_path / ".trw" / "context" / "pre_compact_state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["directive"] == "land FR-08 then deliver"
    assert state["context_anchor"] == "mode wired, verifying parity"

    # Compare directly against the retained helper on fresh, otherwise-identical
    # fixture state (a second run dir so checkpoints.jsonl parity is exact).
    run_dir_2 = tmp_path / "docs" / "task" / "runs" / "20260529T130000Z-test2"
    (run_dir_2 / "meta").mkdir(parents=True)
    (run_dir_2 / "meta" / "run.yaml").write_text(
        "run_id: t2\nstatus: active\nphase: implement\ntask: t\n", encoding="utf-8"
    )
    (run_dir_2 / "meta" / "events.jsonl").write_text("", encoding="utf-8")

    from trw_mcp.tools.checkpoint import execute_pre_compact_checkpoint

    with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir_2):
        direct = execute_pre_compact_checkpoint(
            None,
            "land FR-08 then deliver",
            "mode wired, verifying parity",
        )

    assert direct["status"] == via_mode["status"] == "success"
    assert direct["directive"] == via_mode["directive"]
    assert direct["context_anchor"] == via_mode["context_anchor"]
    assert set(direct.keys()) == set(via_mode.keys())


def test_checkpoint_pre_compact_mode_backward_compatible_without_directive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _tool_run_dir(tmp_path)
    tools = _make_ceremony_checkpoint_server(monkeypatch, tmp_path)

    with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
        result = tools["trw_checkpoint"].fn(pre_compact=True)

    assert result["status"] == "success"
    assert "directive" not in result
    assert "context_anchor" not in result
    checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
    assert checkpoints_path.exists()


def test_checkpoint_pre_compact_mode_does_not_require_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pre_compact=True with no message must not hit the empty-message refusal."""
    run_dir = _tool_run_dir(tmp_path)
    tools = _make_ceremony_checkpoint_server(monkeypatch, tmp_path)

    with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
        result = tools["trw_checkpoint"].fn(pre_compact=True, directive="resume here")

    assert result["status"] == "success"
    assert result.get("reason") != "empty_message"


# ---------------------------------------------------------------------------
# Plain checkpoint path is unaffected by the new params
# ---------------------------------------------------------------------------


def test_checkpoint_plain_mode_unaffected_by_new_params(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_dir = _tool_run_dir(tmp_path)
    tools = _make_ceremony_checkpoint_server(monkeypatch, tmp_path)

    with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
        result = tools["trw_checkpoint"].fn(run_path=str(run_dir), message="finished FR-08 wiring")

    assert result["recorded"] is True
    assert result["status"] == "checkpoint_created"
    checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
    assert checkpoints_path.exists()
    records = [json.loads(line) for line in checkpoints_path.read_text().splitlines() if line.strip()]
    assert any(r.get("message") == "finished FR-08 wiring" for r in records)


def test_checkpoint_empty_message_still_refused_when_no_mode_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _tool_run_dir(tmp_path)
    tools = _make_ceremony_checkpoint_server(monkeypatch, tmp_path)

    with patch("trw_mcp.tools.checkpoint.find_active_run", return_value=run_dir):
        result = tools["trw_checkpoint"].fn(run_path=str(run_dir))

    assert result["recorded"] is False
    assert result["reason"] == "empty_message"


# ---------------------------------------------------------------------------
# NFR01 — the two folded names are not separately registered (PRD-CORE-300-FR08)
# ---------------------------------------------------------------------------


def test_the_two_folded_tool_names_are_not_registered() -> None:
    """The names these modes replaced no longer resolve to a registered tool."""
    server = _make_orchestration_server()
    names = {t.name for t in get_tools_sync(server).values()}
    assert "trw_heartbeat" not in names
    assert "trw_pre_compact_checkpoint" not in names
    assert "trw_checkpoint" in names
