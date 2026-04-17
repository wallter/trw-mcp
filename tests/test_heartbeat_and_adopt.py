"""PRD-CORE-141 Wave 4 Track B — trw_heartbeat + trw_adopt_run.

FR07 covers heartbeat rate-limit behavior, event append, restart
persistence, and should_checkpoint derivation.  FR08 covers adoption
transfer, live-owner/terminal-status/containment guards, and the
``run_adopted`` audit event.

The fixture mirrors ``tests/test_pin_isolation_ctx.py::isolated_project``
(TRW_PROJECT_ROOT + config reset + pin-store cache flush) so these tests
do not cross-contaminate the Wave 1-3 baseline fixtures.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

# ---------------------------------------------------------------------------
# Helpers + fixture
# ---------------------------------------------------------------------------


def _seed_run(
    project_root: Path,
    task: str,
    run_id: str,
    *,
    status: str = "active",
    created_at: str | None = None,
) -> Path:
    """Create ``runs_root/{task}/{run_id}/meta/run.yaml`` + events.jsonl."""
    from trw_mcp.models.config import get_config

    runs_root = project_root / get_config().runs_root
    run_dir = runs_root / task / run_id
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    ts = created_at or datetime.now(timezone.utc).isoformat()
    (run_dir / "meta" / "run.yaml").write_text(
        f"""run_id: {run_id}
task: {task}
framework: v24.5_TRW
status: {status}
phase: implement
created_at: {ts}
""",
        encoding="utf-8",
    )
    # Ensure events.jsonl exists so heartbeat append has a target.
    (run_dir / "meta" / "events.jsonl").touch()
    return run_dir


def _count_events(events_path: Path, event_type: str) -> int:
    import json

    if not events_path.exists():
        return 0
    n = 0
    with events_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event") == event_type:
                n += 1
    return n


@pytest.fixture
def isolated_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
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


def _heartbeat(server: Any) -> Any:
    from tests.conftest import extract_tool_fn

    return extract_tool_fn(server, "trw_heartbeat")


def _adopt(server: Any) -> Any:
    from tests.conftest import extract_tool_fn

    return extract_tool_fn(server, "trw_adopt_run")


def _make_server() -> Any:
    from tests.conftest import make_test_server

    return make_test_server("ceremony")


# ---------------------------------------------------------------------------
# FR07 — trw_heartbeat
# ---------------------------------------------------------------------------


def test_heartbeat_refreshes_pin_timestamp(isolated_project: Path) -> None:
    from trw_mcp.state._paths import TRWCallContext, pin_active_run
    from trw_mcp.state._pin_store import get_pin_entry, invalidate_pin_store_cache

    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-aaaa1111")
    ctx = TRWCallContext(session_id="sess-1", client_hint=None, explicit=False, fastmcp_session=None)
    pin_active_run(run, context=ctx)
    entry_before = get_pin_entry("sess-1")
    assert entry_before is not None
    ts_before = entry_before["last_heartbeat_ts"]

    # Force first-beat rate limit to pass: backdate the stored timestamp.
    from trw_mcp.state._pin_store import upsert_pin_entry  # noqa: F401 — exercised through heartbeat

    invalidate_pin_store_cache()
    # Direct JSON rewrite to backdate past 60s.
    import json

    from trw_mcp.state._pin_store import pin_store_path

    path = pin_store_path()
    raw = json.loads(path.read_text())
    past = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    raw["sess-1"]["last_heartbeat_ts"] = past
    path.write_text(json.dumps(raw))
    invalidate_pin_store_cache()

    server = _make_server()
    hb = _heartbeat(server)
    result = hb(ctx=SimpleNamespace(session_id="sess-1"), message="still alive")
    assert result.get("rate_limited") is False
    assert "last_heartbeat_ts" in result
    assert result["last_heartbeat_ts"] != ts_before
    assert result["run_id"] == run.name


def test_heartbeat_appends_event_to_events_jsonl(isolated_project: Path) -> None:
    from trw_mcp.state._paths import TRWCallContext, pin_active_run
    from trw_mcp.state._pin_store import invalidate_pin_store_cache, pin_store_path

    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-aaaa1111")
    ctx = TRWCallContext(session_id="sess-evt", client_hint=None, explicit=False, fastmcp_session=None)
    pin_active_run(run, context=ctx)

    import json

    path = pin_store_path()
    raw = json.loads(path.read_text())
    raw["sess-evt"]["last_heartbeat_ts"] = (
        (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    )
    path.write_text(json.dumps(raw))
    invalidate_pin_store_cache()

    server = _make_server()
    hb = _heartbeat(server)
    hb(ctx=SimpleNamespace(session_id="sess-evt"), message="tick")

    assert _count_events(run / "meta" / "events.jsonl", "heartbeat") == 1


def test_heartbeat_rate_limited_within_60s(isolated_project: Path) -> None:
    from trw_mcp.state._paths import TRWCallContext, pin_active_run

    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-aaaa1111")
    ctx = TRWCallContext(session_id="sess-rl", client_hint=None, explicit=False, fastmcp_session=None)
    pin_active_run(run, context=ctx)

    server = _make_server()
    hb = _heartbeat(server)
    sns = SimpleNamespace(session_id="sess-rl")

    first = hb(ctx=sns, message="one")
    second = hb(ctx=sns, message="two")

    # First beat after pin_active_run was already within 60s of the pin upsert
    # (pin_active_run sets last_heartbeat_ts=now).  Therefore the FIRST
    # heartbeat call should see the recent upsert and short-circuit.
    assert first.get("rate_limited") is True
    assert second.get("rate_limited") is True
    # Zero heartbeat events appended because both calls short-circuited.
    assert _count_events(run / "meta" / "events.jsonl", "heartbeat") == 0


def test_heartbeat_rate_limit_survives_restart(isolated_project: Path) -> None:
    """FR07: restart (== cache invalidation) must not bypass the 60s window."""
    from trw_mcp.state._paths import TRWCallContext, pin_active_run
    from trw_mcp.state._pin_store import invalidate_pin_store_cache, pin_store_path

    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-aaaa1111")
    ctx = TRWCallContext(session_id="sess-restart", client_hint=None, explicit=False, fastmcp_session=None)
    pin_active_run(run, context=ctx)

    # Backdate to guarantee the first heartbeat fires the write path.
    import json

    path = pin_store_path()
    raw = json.loads(path.read_text())
    raw["sess-restart"]["last_heartbeat_ts"] = (
        (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    )
    path.write_text(json.dumps(raw))
    invalidate_pin_store_cache()

    server = _make_server()
    hb = _heartbeat(server)
    sns = SimpleNamespace(session_id="sess-restart")

    first = hb(ctx=sns, message="before restart")
    assert first.get("rate_limited") is False

    # Simulate server restart: flush the in-memory cache.
    invalidate_pin_store_cache()

    second = hb(ctx=sns, message="after restart")
    assert second.get("rate_limited") is True


def test_heartbeat_no_pin_returns_error_without_creating_pin(
    isolated_project: Path,
) -> None:
    from trw_mcp.state._pin_store import load_pin_store

    server = _make_server()
    hb = _heartbeat(server)
    result = hb(ctx=SimpleNamespace(session_id="no-pin-session"), message="x")

    assert result.get("error") == "no_active_pin"
    assert "trw_init" in str(result.get("hint", ""))
    # Pin store must still be empty (no auto-creation).
    assert "no-pin-session" not in load_pin_store()


def test_heartbeat_should_checkpoint_true_for_aged_run(
    isolated_project: Path,
) -> None:
    from trw_mcp.state._paths import TRWCallContext, pin_active_run
    from trw_mcp.state._pin_store import invalidate_pin_store_cache, pin_store_path

    aged_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-aaaa1111", created_at=aged_ts)
    ctx = TRWCallContext(session_id="sess-aged", client_hint=None, explicit=False, fastmcp_session=None)
    pin_active_run(run, context=ctx)

    # Backdate pin so heartbeat takes the write path.
    import json

    path = pin_store_path()
    raw = json.loads(path.read_text())
    raw["sess-aged"]["last_heartbeat_ts"] = (
        (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    )
    path.write_text(json.dumps(raw))
    invalidate_pin_store_cache()

    server = _make_server()
    hb = _heartbeat(server)
    result = hb(ctx=SimpleNamespace(session_id="sess-aged"))
    assert result.get("rate_limited") is False
    assert result.get("should_checkpoint") is True
    assert result.get("age_hours", 0.0) > 4.0


def test_heartbeat_returns_stale_after_ts(isolated_project: Path) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import TRWCallContext, pin_active_run
    from trw_mcp.state._pin_store import invalidate_pin_store_cache, pin_store_path

    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-aaaa1111")
    ctx = TRWCallContext(session_id="sess-stale", client_hint=None, explicit=False, fastmcp_session=None)
    pin_active_run(run, context=ctx)
    import json

    path = pin_store_path()
    raw = json.loads(path.read_text())
    raw["sess-stale"]["last_heartbeat_ts"] = (
        (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    )
    path.write_text(json.dumps(raw))
    invalidate_pin_store_cache()

    server = _make_server()
    hb = _heartbeat(server)
    result = hb(ctx=SimpleNamespace(session_id="sess-stale"))

    last_ts = result["last_heartbeat_ts"]
    stale_after = result["stale_after_ts"]

    # Parse both: stale_after must equal last + run_staleness_hours*3600.
    def _parse(ts: str) -> datetime:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)

    delta = _parse(stale_after) - _parse(last_ts)
    config = get_config()
    expected = timedelta(hours=config.run_staleness_hours)
    # Allow 1s tolerance for clock jitter between ts composition.
    assert abs((delta - expected).total_seconds()) < 1.0


# ---------------------------------------------------------------------------
# FR08 — trw_adopt_run
# ---------------------------------------------------------------------------


def test_adopt_run_transfers_pin(isolated_project: Path) -> None:
    from trw_mcp.state._paths import TRWCallContext, pin_active_run
    from trw_mcp.state._pin_store import get_pin_entry, invalidate_pin_store_cache, pin_store_path

    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-aaaa1111")
    # Session A pins the run.
    ctx_a = TRWCallContext(session_id="sess-A", client_hint=None, explicit=False, fastmcp_session=None)
    pin_active_run(run, context=ctx_a)
    # Age out A's heartbeat so live-owner guard does not fire without force.
    import json

    path = pin_store_path()
    raw = json.loads(path.read_text())
    raw["sess-A"]["last_heartbeat_ts"] = (
        (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    )
    path.write_text(json.dumps(raw))
    invalidate_pin_store_cache()

    server = _make_server()
    adopt = _adopt(server)
    result = adopt(ctx=SimpleNamespace(session_id="sess-B"), run_path=str(run))

    assert result["adopted_run_id"] == run.name
    assert result["to_pin_key"] == "sess-B"
    assert result["previous_pin_key"] == "sess-A"
    assert result["force_used"] is False
    # Session B now owns the pin; A's entry is gone.
    assert get_pin_entry("sess-B") is not None
    assert get_pin_entry("sess-A") is None
    # Audit event present.
    assert _count_events(run / "meta" / "events.jsonl", "run_adopted") == 1


def test_adopt_refuses_out_of_project_path(
    isolated_project: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    from trw_mcp.exceptions import StateError

    outside = tmp_path_factory.mktemp("outside")
    outside_run = outside / "external-run"
    (outside_run / "meta").mkdir(parents=True, exist_ok=True)

    server = _make_server()
    adopt = _adopt(server)
    with pytest.raises(StateError):
        adopt(ctx=SimpleNamespace(session_id="sess-X"), run_path=str(outside_run))


def test_adopt_refuses_terminal_status_without_force(
    isolated_project: Path,
) -> None:
    from trw_mcp.exceptions import StateError

    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-aaaa1111", status="delivered")
    server = _make_server()
    adopt = _adopt(server)
    with pytest.raises(StateError) as exc:
        adopt(ctx=SimpleNamespace(session_id="sess-T"), run_path=str(run))
    assert "terminal" in str(exc.value).lower()


def test_adopt_succeeds_terminal_status_with_force(
    isolated_project: Path,
) -> None:
    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-aaaa1111", status="delivered")
    server = _make_server()
    adopt = _adopt(server)
    result = adopt(
        ctx=SimpleNamespace(session_id="sess-TF"),
        run_path=str(run),
        force=True,
    )
    assert result["force_used"] is True
    assert result["to_pin_key"] == "sess-TF"


def test_adopt_refuses_live_owner_without_force(
    isolated_project: Path,
) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.state._paths import TRWCallContext, pin_active_run

    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-aaaa1111")
    ctx_a = TRWCallContext(session_id="sess-live", client_hint=None, explicit=False, fastmcp_session=None)
    pin_active_run(run, context=ctx_a)  # fresh heartbeat now

    server = _make_server()
    adopt = _adopt(server)
    with pytest.raises(StateError) as exc:
        adopt(ctx=SimpleNamespace(session_id="sess-new"), run_path=str(run))
    assert "live pin" in str(exc.value).lower()


def test_adopt_succeeds_on_live_owner_with_force(
    isolated_project: Path,
) -> None:
    from trw_mcp.state._paths import TRWCallContext, pin_active_run

    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-aaaa1111")
    ctx_a = TRWCallContext(
        session_id="sess-live-force",
        client_hint=None,
        explicit=False,
        fastmcp_session=None,
    )
    pin_active_run(run, context=ctx_a)

    server = _make_server()
    adopt = _adopt(server)
    with capture_logs() as logs:
        result = adopt(
            ctx=SimpleNamespace(session_id="sess-force-B"),
            run_path=str(run),
            force=True,
        )

    assert result["force_used"] is True
    assert result["from_owner_was_live"] is True
    conflict = [e for e in logs if e.get("event") == "run_adopted_potential_writer_conflict"]
    assert conflict, f"expected conflict warning; logs={logs!r}"


def test_adopt_run_adopted_event_carries_audit_fields(
    isolated_project: Path,
) -> None:
    import json

    from trw_mcp.state._paths import TRWCallContext, pin_active_run
    from trw_mcp.state._pin_store import invalidate_pin_store_cache, pin_store_path

    run = _seed_run(isolated_project, "alpha", "20260101T000000Z-aaaa1111")
    ctx_a = TRWCallContext(session_id="audit-A", client_hint=None, explicit=False, fastmcp_session=None)
    pin_active_run(run, context=ctx_a)
    # Age out to skip the force requirement.
    path = pin_store_path()
    raw = json.loads(path.read_text())
    raw["audit-A"]["last_heartbeat_ts"] = (
        (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    )
    path.write_text(json.dumps(raw))
    invalidate_pin_store_cache()

    server = _make_server()
    adopt = _adopt(server)
    adopt(ctx=SimpleNamespace(session_id="audit-B"), run_path=str(run))

    events_path = run / "meta" / "events.jsonl"
    lines = events_path.read_text().splitlines()
    adopted_lines = [
        json.loads(line) for line in lines if line.strip() and json.loads(line).get("event") == "run_adopted"
    ]
    assert len(adopted_lines) == 1
    payload = adopted_lines[0]
    assert payload.get("from_pin_key") == "audit-A"
    assert payload.get("to_pin_key") == "audit-B"
    assert payload.get("force_used") is False
    assert payload.get("previous_owner_heartbeat_age_hours") is not None
