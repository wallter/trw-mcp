"""PRD-CORE-354 FR01/FR02/FR04: the read-only v1 status snapshot, its truthfulness
rules, the opt-in cache, and the ``local status --json/--format line`` CLI path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.conftest import _fresh_process_incarnations, comms_server  # noqa: F401
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.services import status_snapshot as ss
from trw_mcp.services.status_line import render_status_line

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
SID = "sess-1"

_TOP_KEYS = {
    "schema_version",
    "generated_at",
    "session_id",
    "client",
    "run",
    "checkpoint",
    "evidence",
    "gate_preview",
    "project_aggregate",
    "inbox",
    "degraded",
    "unknown",
}


def _ts(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def _project(tmp_path: Path, *, pin: bool = True, phase: str = "implement") -> tuple[Path, Path]:
    trw_dir = tmp_path / "proj" / ".trw"
    run = trw_dir / "runs" / "task-a" / "20261004T000000Z-abc"
    (run / "meta").mkdir(parents=True)
    (run / "meta" / "run.yaml").write_text(
        f"run_id: 20261004T000000Z-abc\ntask: task-a\nphase: {phase}\nstatus: active\n", encoding="utf-8"
    )
    (trw_dir / "runtime").mkdir(parents=True)
    if pin:
        (trw_dir / "runtime" / "pins.json").write_text(
            json.dumps(
                {SID: {"run_path": str(run), "created_ts": _ts(5), "last_heartbeat_ts": _ts(1), "pid": os.getpid()}}
            ),
            encoding="utf-8",
        )
    return trw_dir, run


def _append(path: Path, *records: dict[str, Any] | str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write((record if isinstance(record, str) else json.dumps(record)) + "\n")


def _ceremony(trw_dir: Path, **fields: Any) -> None:
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context" / "ceremony-state.json").write_text(json.dumps(fields), encoding="utf-8")


def _snap(trw_dir: Path, sid: str | None = SID, **kwargs: Any) -> dict[str, Any]:
    return ss.build_status_snapshot(trw_dir, sid, now=NOW, environ={}, **kwargs)


def _assert_v1_shape(snap: dict[str, Any]) -> None:
    assert set(snap) == _TOP_KEYS
    assert snap["schema_version"] == 1
    assert set(snap["run"]) == {"state", "run_id", "task", "phase", "status", "run_path", "as_of"}
    assert set(snap["checkpoint"]) == {"state", "count", "last_ts", "age_s", "scope", "as_of"}
    assert set(snap["evidence"]) == {"build", "review", "deliver", "as_of"}
    assert set(snap["evidence"]["build"]) == {"state", "scope", "ts", "test_count", "build_scope"}
    assert set(snap["evidence"]["review"]) == set(snap["evidence"]["deliver"]) == {"state", "scope", "ts"}
    assert set(snap["gate_preview"]) == {"state", "summary", "preview", "as_of"}
    assert snap["gate_preview"]["preview"] is True
    assert set(snap["project_aggregate"]) == {"build_check_result", "review_verdict", "deliver_called", "scope"}
    assert set(snap["inbox"]) == {"state", "pending", "formation_id", "as_of"}
    assert set(snap["degraded"]) == {"state"}
    json.dumps(snap)  # serialisable


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(str(path.relative_to(root)).encode())
        if path.is_file():
            digest.update(path.read_bytes())
            digest.update(str(path.stat().st_mtime_ns).encode())
    return digest.hexdigest()


def test_fixture_contract_file_is_schema_valid() -> None:
    fixture = Path(__file__).parent / "fixtures" / "status_snapshot_v1.json"
    _assert_v1_shape(json.loads(fixture.read_text(encoding="utf-8")))


def test_no_pin_reports_none_without_guessing(tmp_path: Path) -> None:
    trw_dir, _run = _project(tmp_path, pin=False)
    snap = _snap(trw_dir)
    _assert_v1_shape(snap)
    assert snap["run"]["state"] == "none" and snap["run"]["run_path"] == ""
    assert snap["checkpoint"]["state"] == "none"
    assert snap["evidence"]["build"]["state"] == "none"
    assert snap["inbox"]["state"] == "none"
    assert snap["degraded"]["state"] == "no"
    assert snap["unknown"] == []
    assert render_status_line(snap) == "TRW"


def test_no_session_id_is_none_and_degraded_unknown(tmp_path: Path) -> None:
    trw_dir, _run = _project(tmp_path)
    snap = _snap(trw_dir, None)
    assert snap["session_id"] is None and snap["run"]["state"] == "none"
    assert snap["degraded"]["state"] == "unknown" and "degraded" in snap["unknown"]


@pytest.mark.parametrize("bad", ["../escape", "-flag", "..", "a/b", "x" * 201])
def test_unsafe_session_ids_are_never_used_as_paths(tmp_path: Path, bad: str) -> None:
    trw_dir, _run = _project(tmp_path)
    snap = _snap(trw_dir, bad)
    assert snap["session_id"] is None and snap["run"]["state"] == "none"
    assert ss.write_cached_snapshot(trw_dir, {**snap, "session_id": bad}) is None


def test_pinned_run_with_events(tmp_path: Path) -> None:
    trw_dir, run = _project(tmp_path)
    _append(run / "meta" / "checkpoints.jsonl", {"ts": _ts(20), "message": "a"}, {"ts": _ts(12), "message": "b"})
    _append(
        run / "meta" / "events.jsonl",
        {
            "ts": _ts(10),
            "event": "build_check_complete",
            "tests_passed": True,
            "static_checks_clean": True,
            "test_count": 42,
            "scope": "full",
        },
        {"ts": _ts(8), "event": "review_complete", "verdict": "PASS", "critical_count": 0},
    )
    snap = _snap(trw_dir)
    _assert_v1_shape(snap)
    assert snap["run"] | {"as_of": None} == {
        "state": "ok",
        "run_id": "20261004T000000Z-abc",
        "task": "task-a",
        "phase": "implement",
        "status": "active",
        "run_path": str(run),
        "as_of": None,
    }
    assert snap["checkpoint"] | {"as_of": None} == {
        "state": "ok",
        "count": 2,
        "last_ts": _ts(12),
        "age_s": 720,
        "scope": "run",
        "as_of": None,
    }
    build = snap["evidence"]["build"]
    assert build == {"state": "passed", "scope": "run", "ts": _ts(10), "test_count": 42, "build_scope": "full"}
    assert snap["evidence"]["review"] == {"state": "pass", "scope": "run", "ts": _ts(8)}
    assert snap["evidence"]["deliver"]["state"] == "none"
    assert snap["gate_preview"]["state"] in {"ready", "blocked"}
    assert snap["unknown"] == []
    assert render_status_line(snap) == "TRW ▸ implement · task-a"


def test_failed_and_degenerate_builds_are_not_passes(tmp_path: Path) -> None:
    trw_dir, run = _project(tmp_path)
    _append(
        run / "meta" / "events.jsonl",
        {"ts": _ts(3), "event": "build_check_complete", "tests_passed": True, "test_count": 0, "scope": "full"},
    )
    assert _snap(trw_dir)["evidence"]["build"]["state"] == "failed"


def test_server_logged_run_without_evidence_is_none_not_unknown(tmp_path: Path) -> None:
    """A run whose log holds trw_* tool_call rows is server-logged; the server writes
    build/review/deliver into that same log, so their absence there proves "none"."""
    trw_dir, run = _project(tmp_path)
    _append(run / "meta" / "events.jsonl", {"ts": _ts(2), "event": "tool_call", "tool_name": "trw_status"})
    _ceremony(trw_dir, build_check_result="passed", review_verdict="pass", deliver_called=True)
    snap = _snap(trw_dir)
    assert snap["evidence"]["build"]["state"] == "none"
    assert snap["evidence"]["review"]["state"] == "none"
    assert snap["evidence"]["deliver"]["state"] == "none"
    line = render_status_line(snap)
    assert "build" not in line and "✓" not in line


def test_no_positive_tick_from_project_aggregate(tmp_path: Path) -> None:
    """FR02/HB-1: another session's passing build must never read as this run's."""
    trw_dir, run = _project(tmp_path)
    _append(run / "meta" / "events.jsonl", {"ts": _ts(2), "event": "file_modified", "tool": "Write"})
    _ceremony(
        trw_dir,
        build_check_result="passed",
        review_verdict="pass",
        deliver_called=True,
        session_build_results={"other-session": "passed"},
    )
    snap = _snap(trw_dir)
    assert snap["evidence"]["build"]["state"] == "unknown"
    assert snap["evidence"]["build"]["scope"] == "unknown"
    assert snap["evidence"]["review"]["state"] == "unknown"
    assert snap["evidence"]["deliver"]["state"] == "unknown"
    assert snap["project_aggregate"] == {
        "build_check_result": "passed",
        "review_verdict": "pass",
        "deliver_called": True,
        "scope": "project_aggregate",
    }
    line = render_status_line(snap)
    assert "?" not in line and "✓" not in line and "build" not in line


def test_session_build_result_has_session_scope(tmp_path: Path) -> None:
    trw_dir, _run = _project(tmp_path)
    _ceremony(
        trw_dir,
        build_check_result="failed",
        session_build_results={SID: "passed"},
        session_build_results_at={SID: _ts(4)},
    )
    build = _snap(trw_dir)["evidence"]["build"]
    assert build == {"state": "passed", "scope": "session", "ts": _ts(4), "test_count": None, "build_scope": None}


def test_run_events_outrank_the_session_result(tmp_path: Path) -> None:
    trw_dir, run = _project(tmp_path)
    _append(run / "meta" / "events.jsonl", {"ts": _ts(1), "event": "build_check_complete", "tests_passed": False})
    _ceremony(trw_dir, session_build_results={SID: "passed"})
    assert _snap(trw_dir)["evidence"]["build"]["state"] == "failed"


def test_corrupt_events_line_is_skipped(tmp_path: Path) -> None:
    trw_dir, run = _project(tmp_path)
    _append(
        run / "meta" / "events.jsonl",
        "{not json",
        {"ts": _ts(5), "event": "trw_deliver_complete"},
        '{"ts": "torn',
    )
    snap = _snap(trw_dir)
    assert snap["evidence"]["deliver"] == {"state": "called", "scope": "run", "ts": _ts(5)}
    assert "evidence.deliver" not in snap["unknown"]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores mode bits")
def test_unreadable_sources_are_unknown_never_a_pass(tmp_path: Path) -> None:
    trw_dir, run = _project(tmp_path)
    events = run / "meta" / "events.jsonl"
    events.write_text("{}\n", encoding="utf-8")
    events.chmod(0)  # unreadable log
    (run / "meta" / "checkpoints.jsonl").write_text('{"ts": "not-a-time"}\n', encoding="utf-8")
    (trw_dir / "context").mkdir()
    (trw_dir / "context" / "ceremony-state.json").write_text("{corrupt", encoding="utf-8")
    try:
        snap = _snap(trw_dir)
    finally:
        events.chmod(0o600)
    _assert_v1_shape(snap)
    for dotted in ("checkpoint", "project_aggregate", "gate_preview"):
        assert dotted in snap["unknown"]
    assert {"evidence.build", "evidence.review", "evidence.deliver"} <= set(snap["unknown"])
    assert render_status_line(snap) == "TRW ▸ implement · task-a"


def test_corrupt_pin_store_and_run_yaml_are_unknown(tmp_path: Path) -> None:
    trw_dir, run = _project(tmp_path)
    (run / "meta" / "run.yaml").write_text("- not\n- a mapping\n", encoding="utf-8")
    snap = _snap(trw_dir)
    assert snap["run"]["state"] == "unknown" and snap["run"]["run_path"] == str(run)
    assert {"run", "checkpoint", "inbox"} <= set(snap["unknown"])
    (trw_dir / "runtime" / "pins.json").write_text("{", encoding="utf-8")
    assert _snap(trw_dir)["run"]["state"] == "unknown"


def test_pin_to_a_vanished_run_is_none(tmp_path: Path) -> None:
    trw_dir, run = _project(tmp_path)
    (run / "meta" / "run.yaml").unlink()
    (run / "meta").rmdir()
    run.rmdir()
    assert _snap(trw_dir)["run"]["state"] == "none"


def test_stale_checkpoint(tmp_path: Path) -> None:
    trw_dir, run = _project(tmp_path)
    _append(run / "meta" / "checkpoints.jsonl", {"ts": _ts(47), "message": "old"})
    snap = _snap(trw_dir)
    assert snap["checkpoint"]["state"] == "stale" and snap["checkpoint"]["age_s"] == 47 * 60
    assert "ckpt" not in render_status_line(snap)


def test_degraded_latch(tmp_path: Path) -> None:
    trw_dir, _run = _project(tmp_path)
    (trw_dir / "runtime" / "degraded-mode").mkdir()
    (trw_dir / "runtime" / "degraded-mode" / SID).touch()
    snap = _snap(trw_dir)
    assert snap["degraded"] == {"state": "yes"}
    assert render_status_line(snap) == "TRW ⚠ MCP not seen"
    assert _snap(trw_dir, "another-session")["degraded"] == {"state": "no"}


def test_degraded_latch_is_stale_after_a_later_mcp_tool_call(tmp_path: Path) -> None:
    """The latch is cleared only at session start; a later server-written trw_* tool_call
    proves MCP attached, so the line must not keep claiming "MCP not seen" (HB-1)."""
    import os

    trw_dir, run = _project(tmp_path)
    latch = trw_dir / "runtime" / "degraded-mode" / SID
    latch.parent.mkdir(parents=True)
    latch.touch()
    latched_at = (NOW - timedelta(hours=1)).timestamp()  # anchored to the fixed clock, not wall time
    os.utime(latch, (latched_at, latched_at))
    _append(run / "meta" / "events.jsonl", {"ts": _ts(1), "event": "file_modified", "tool": "Write"})
    assert _snap(trw_dir)["degraded"] == {"state": "yes"}, "a hook row is not MCP evidence"
    _append(run / "meta" / "events.jsonl", {"ts": _ts(1), "event": "tool_call", "tool_name": "trw_checkpoint"})
    snap = _snap(trw_dir)
    assert snap["degraded"] == {"state": "no"}
    assert render_status_line(snap) != "TRW ⚠ MCP not seen"


def _latch(trw_dir: Path, age_h: float = 1) -> Path:
    import os

    latch = trw_dir / "runtime" / "degraded-mode" / SID
    latch.parent.mkdir(parents=True, exist_ok=True)
    latch.touch()
    at = (NOW - timedelta(hours=age_h)).timestamp()
    os.utime(latch, (at, at))
    return latch


def test_degraded_latch_stale_via_session_events_when_unpinned(tmp_path: Path) -> None:
    """No pin: the server's tool_call rows go to context/session-events.jsonl; they clear the latch too."""
    trw_dir, _run = _project(tmp_path, pin=False)
    _latch(trw_dir)
    stream = trw_dir / "context" / "session-events.jsonl"
    stream.parent.mkdir(parents=True)
    _append(stream, {"ts": _ts(120), "event": "tool_call", "tool_name": "trw_session_start"})  # older than latch
    _append(stream, {"ts": _ts(1), "event": "tool_call", "tool_name": "Bash"}, "{torn")
    assert _snap(trw_dir)["degraded"] == {"state": "yes"}
    _append(stream, {"ts": _ts(1), "event": "tool_call", "tool_name": "trw_checkpoint"})
    assert _snap(trw_dir)["degraded"] == {"state": "no"}


def test_session_events_tail_is_bounded(tmp_path: Path) -> None:
    from trw_mcp.services import _status_sources as src

    stream = tmp_path / "s.jsonl"
    _append(stream, {"ts": _ts(1), "event": "tool_call", "tool_name": "trw_old"})
    _append(stream, *({"pad": "x" * 100} for _ in range(50)))
    assert src.read_jsonl_tail(stream, 1024)  # tail parses, the cut first line is dropped
    assert all("pad" in r for r in src.read_jsonl_tail(stream, 1024))


def test_symlinked_degraded_latch_is_not_a_latch(tmp_path: Path) -> None:
    trw_dir, _run = _project(tmp_path)
    target = tmp_path / "elsewhere"
    target.touch()
    link = trw_dir / "runtime" / "degraded-mode" / SID
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    assert _snap(trw_dir)["degraded"] == {"state": "no"}


def test_run_status_complete_is_not_deliver_evidence(tmp_path: Path) -> None:
    trw_dir, run = _project(tmp_path)
    (run / "meta" / "run.yaml").write_text(
        "run_id: 20261004T000000Z-abc\ntask: task-a\nphase: deliver\nstatus: complete\n", encoding="utf-8"
    )
    assert _snap(trw_dir)["evidence"]["deliver"]["state"] == "none"
    _append(
        run / "meta" / "events.jsonl",
        {"ts": _ts(2), "event": "tool_call", "tool_name": "trw_deliver", "success": False},
    )
    assert _snap(trw_dir)["evidence"]["deliver"]["state"] == "none"
    _append(
        run / "meta" / "events.jsonl", {"ts": _ts(1), "event": "tool_call", "tool_name": "trw_deliver", "success": True}
    )
    deliver = _snap(trw_dir)["evidence"]["deliver"]
    assert deliver["state"] == "called" and deliver["ts"] == _ts(1)


def test_client_label(tmp_path: Path) -> None:
    trw_dir, _run = _project(tmp_path)
    assert ss.build_status_snapshot(trw_dir, SID, now=NOW, environ={"CLAUDE_CODE_SESSION_ID": SID})["client"] == (
        "claude-code"
    )
    assert _snap(trw_dir)["client"] is None


def test_snapshot_is_read_only(tmp_path: Path) -> None:
    trw_dir, run = _project(tmp_path)
    _append(run / "meta" / "checkpoints.jsonl", {"ts": _ts(3), "message": "a"})
    _append(
        run / "meta" / "events.jsonl",
        {"ts": _ts(2), "event": "build_check_complete", "tests_passed": True, "test_count": 3, "scope": "unit"},
    )
    _ceremony(trw_dir, build_check_result="passed")
    before = _tree_digest(tmp_path)
    _snap(trw_dir)
    ss.load_or_build_snapshot(trw_dir, SID, now=NOW)  # no --cache-ttl: no write either
    assert _tree_digest(tmp_path) == before


def test_cache_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir, _run = _project(tmp_path)
    calls: list[int] = []
    real = ss.build_status_snapshot

    def counting(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(ss, "build_status_snapshot", counting)
    first = ss.load_or_build_snapshot(trw_dir, SID, cache_ttl_s=5, now=NOW)
    cache = ss.cache_path(trw_dir, SID)
    assert cache.is_file() and stat.S_IMODE(cache.stat().st_mode) == 0o600
    second = ss.load_or_build_snapshot(trw_dir, SID, cache_ttl_s=5, now=NOW + timedelta(seconds=3))
    assert second == first and len(calls) == 1, "a fresh cache is returned as is"

    ss.load_or_build_snapshot(trw_dir, SID, cache_ttl_s=5, now=NOW + timedelta(seconds=9))
    assert len(calls) == 2, "an expired cache recomputes"

    cache.write_text("{corrupt", encoding="utf-8")
    rebuilt = ss.load_or_build_snapshot(trw_dir, SID, cache_ttl_s=5, now=NOW + timedelta(seconds=9))
    assert len(calls) == 3 and json.loads(cache.read_text(encoding="utf-8")) == rebuilt, "corrupt -> rewritten"

    cache.write_text(json.dumps({**rebuilt, "schema_version": 2}), encoding="utf-8")
    ss.load_or_build_snapshot(trw_dir, SID, cache_ttl_s=5, now=NOW + timedelta(seconds=9))
    assert len(calls) == 4, "a foreign schema is ignored"

    ss.load_or_build_snapshot(trw_dir, SID, cache_ttl_s=5, allow_cache_write=False, now=NOW + timedelta(seconds=60))
    assert json.loads(cache.read_text(encoding="utf-8"))["schema_version"] == 1
    assert len(list(cache.parent.iterdir())) == 1, "no temp files left behind"


def test_cache_is_skipped_without_a_session(tmp_path: Path) -> None:
    trw_dir, _run = _project(tmp_path)
    ss.load_or_build_snapshot(trw_dir, None, cache_ttl_s=5, now=NOW)
    assert not (trw_dir / "runtime" / "status").exists()


# --- formation inbox ---------------------------------------------------------


def _pin_member(sc: SendScene, pin_key: str, member: str) -> None:
    pins_path = sc.formation.trw_dir / "runtime" / "pins.json"
    pins = json.loads(pins_path.read_text(encoding="utf-8"))
    pins[pin_key] = {**pins.get(pin_key, {}), "run_path": str(sc.formation.member_runs[member]), "pid": os.getpid()}
    pins_path.write_text(json.dumps(pins), encoding="utf-8")


def test_formation_inbox_pending_count(scene: SendScene) -> None:
    _pin_member(scene, "pin-b", "impl-2")
    trw_dir = scene.formation.trw_dir
    empty = ss.build_status_snapshot(trw_dir, "pin-b", environ={})
    assert empty["inbox"]["state"] == "ok" and empty["inbox"]["pending"] == 0
    assert empty["inbox"]["formation_id"] == "release-train"
    assert scene.send("one")["status"] == scene.send("two")["status"] == "ok"
    before = _tree_digest(trw_dir)
    snap = ss.build_status_snapshot(trw_dir, "pin-b", environ={})
    assert _tree_digest(trw_dir) == before, "the mailbox is read with mode=ro"
    assert snap["inbox"]["state"] == "ok" and snap["inbox"]["pending"] == 2
    assert "hello" not in json.dumps(snap), "no message body crosses the boundary"
    assert render_status_line(snap).endswith("· ✉2") and "ckpt" not in render_status_line(snap)


def test_formation_session_without_a_member_has_no_inbox(scene: SendScene) -> None:
    _pin_member(scene, "pin-z", "impl-2")  # a session that is not the member's pin key
    snap = ss.build_status_snapshot(scene.formation.trw_dir, "pin-z", environ={})
    assert snap["inbox"]["state"] == "none" and snap["inbox"]["pending"] is None


# --- CLI -------------------------------------------------------------------


def _cli(argv: list[str], trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> argparse.Namespace:
    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.state import _paths

    monkeypatch.setattr(_paths, "resolve_trw_dir", lambda: trw_dir)
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    return _build_arg_parser().parse_args(["local", "status", *argv])


def test_cli_json_unpinned_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    from trw_mcp.server._subcommands_misc import _run_local

    trw_dir, _run = _project(tmp_path, pin=False)
    _run_local(_cli(["--json", "--session-id", SID], trw_dir, monkeypatch))
    out = json.loads(capsys.readouterr().out)
    _assert_v1_shape(out)
    assert out["run"]["state"] == "none" and out["session_id"] == SID


def test_cli_line_uses_env_session_and_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    from trw_mcp.server._subcommands_misc import _run_local

    trw_dir, run = _project(tmp_path)
    _append(
        run / "meta" / "events.jsonl",
        {"ts": datetime.now(timezone.utc).isoformat(), "event": "review_complete", "verdict": "block"},
    )
    args = _cli(["--format", "line", "--cache-ttl", "5"], trw_dir, monkeypatch)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    _run_local(args)
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.startswith("TRW ▸ implement · task-a") and "review ✗" in out
    assert (trw_dir / "runtime" / "status" / f"{SID}.json").is_file()
    line_file = trw_dir / "runtime" / "status" / f"{SID}.line"
    assert line_file.read_text(encoding="utf-8") == out
    assert stat.S_IMODE(line_file.stat().st_mode) == 0o600


def test_cli_line_without_ttl_writes_no_line_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    from trw_mcp.server._subcommands_misc import _run_local

    trw_dir, _run = _project(tmp_path)
    _run_local(_cli(["--format", "line", "--session-id", SID], trw_dir, monkeypatch))
    capsys.readouterr()
    assert not (trw_dir / "runtime" / "status").exists()


def test_cli_line_never_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    from trw_mcp.server._subcommands_misc import _run_local

    trw_dir, _run = _project(tmp_path)
    args = _cli(["--format", "line", "--session-id", SID], trw_dir, monkeypatch)

    def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("broken")

    monkeypatch.setattr(ss, "load_or_build_snapshot", boom)
    _run_local(args)
    assert capsys.readouterr().out == "TRW · status unavailable\n"


def test_cli_text_default_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    from trw_mcp.server._subcommands_misc import _run_local

    trw_dir, run = _project(tmp_path)
    with pytest.raises(SystemExit) as exited:
        _run_local(_cli(["--run-path", str(run)], trw_dir, monkeypatch))
    assert exited.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("Run: 20261004T000000Z-abc\n  Task: task-a\n")


def test_status_cache_write_prunes_stale_siblings(tmp_path: Path) -> None:
    """Long-running sessions: cache files older than 7 days are pruned; fresh, foreign and symlinked ones stay."""
    import os
    import time

    from trw_mcp.services.status_snapshot import write_cached_line

    status = tmp_path / "runtime" / "status"
    status.mkdir(parents=True)
    old = time.time() - 8 * 24 * 3600
    stale = [status / "old-1.json", status / "old-1.line"]
    keep = [status / "fresh.json", status / "notes.txt"]
    for f in (*stale, *keep, status / "oldnotes.txt"):
        f.write_text("x", encoding="utf-8")
    for f in (*stale, status / "oldnotes.txt"):
        os.utime(f, (old, old))
    outside = tmp_path / "target.json"
    outside.write_text("x", encoding="utf-8")
    os.utime(outside, (old, old))
    link = status / "link.json"
    link.symlink_to(outside)
    assert write_cached_line(tmp_path, "sess-1", "TRW") is not None
    assert not any(f.exists() for f in stale)
    assert all(f.exists() for f in keep)
    assert (status / "oldnotes.txt").exists() and link.is_symlink() and outside.exists()
    assert (status / "sess-1.line").exists()


def test_status_cache_prune_failure_never_fails_the_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.services import status_snapshot
    from trw_mcp.services.status_snapshot import write_cached_line

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("scandir failed")

    # status_snapshot.os IS the os module: scope the patch so fixture teardown (tmp cleanup, the TMPDIR
    # sweep) never runs against the failing scandir -- it did on the Linux leg of the 9.2.2 release check.
    with monkeypatch.context() as patched:
        patched.setattr(status_snapshot.os, "scandir", boom)
        assert write_cached_line(tmp_path, "sess-2", "TRW") is not None
