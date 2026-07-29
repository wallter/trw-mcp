"""PRD-CORE-233 NFR02 — FR02's softening must not weaken PRD-CORE-141 anti-hijack.

Returning a result instead of raising is a caller-visible change only. A
pin-less, context-aware session must still never resolve to — or write into —
another session's run directory, and the mtime scan must stay suppressed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests._structlog_capture import captured_structlog  # noqa: F401


@pytest.fixture
def isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    from trw_mcp.models.config import _reset_config, get_config

    _reset_config()
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs

    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    config = get_config()
    (tmp_path / config.runs_root).mkdir(parents=True, exist_ok=True)
    (tmp_path / config.trw_dir).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _ctx(session_id: str) -> Any:
    from trw_mcp.state._paths import TRWCallContext

    return TRWCallContext(
        session_id=session_id,
        client_hint=None,
        explicit=False,
        fastmcp_session=None,
    )


def _seed_run(project_root: Path, task: str, run_id: str) -> Path:
    from trw_mcp.models.config import get_config

    run_dir = project_root / get_config().runs_root / task / run_id
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(
        f"run_id: {run_id}\ntask: {task}\nstatus: active\nphase: implement\n",
        encoding="utf-8",
    )
    return run_dir


def test_pinless_caller_never_writes_into_another_sessions_run(
    isolated_project: Path,
) -> None:
    """NFR02: another session's pinned run is untouched by a pin-less checkpoint."""
    from trw_mcp.state._paths import pin_active_run
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    victim_run = _seed_run(isolated_project, "victim-task", "20260101T000000Z-aaaa1111")
    pin_active_run(victim_run, context=_ctx("victim-session"))

    result = execute_checkpoint(None, "stolen?", None, None, context=_ctx("attacker-session"))

    assert result["recorded"] is False
    assert not (victim_run / "meta" / "checkpoints.jsonl").exists()
    assert not (victim_run / "meta" / "events.jsonl").exists()


def test_pinless_caller_does_not_fall_back_to_the_mtime_scan(
    isolated_project: Path,
    captured_structlog: list[dict[str, object]],
) -> None:
    """NFR02: FR05 scan suppression still fires on the softened path."""
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    stranger = _seed_run(isolated_project, "stranger-task", "20260102T000000Z-bbbb2222")

    result = execute_checkpoint(None, "no scan please", None, None, context=_ctx("fresh-session"))

    assert result["recorded"] is False
    assert not (stranger / "meta" / "checkpoints.jsonl").exists()
    suppressed = [e for e in captured_structlog if e.get("event") == "run_resolution_no_pin_scan_suppressed"]
    assert suppressed, f"FR05 suppression event missing; logs were {captured_structlog!r}"
    assert any(e.get("pin_key") == "fresh-session" for e in suppressed)


def test_not_recorded_outcome_is_logged_for_the_monitoring_signal(
    isolated_project: Path,
    captured_structlog: list[dict[str, object]],
) -> None:
    """The failure goes quiet in the response — it must stay loud in the logs."""
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    execute_checkpoint(None, "quiet", None, None, context=_ctx("observed-session"))

    events = [e for e in captured_structlog if e.get("event") == "checkpoint_not_recorded"]
    assert events, f"no checkpoint_not_recorded event; logs were {captured_structlog!r}"
    assert events[0].get("pin_key") == "observed-session"
