"""PRD-CORE-248 FR02 — five ``boot_phase`` events on one monotonic origin.

Until now the only boot event was ``trw_server_initialized`` and it carried no
timing at all, so the 1.106 s of module import that is 99 % of the
pre-``initialize`` window was entirely invisible in production logs. That
invisibility is why the reported 16.5 s handshake could be dismissed rather than
attributed.

``test_boot_phase_sequence_is_monotonic_and_complete`` spawns a REAL cold server
process and reads its stderr, because "all five events are present in a captured
cold-start stderr stream" is the PRD's own completion evidence and only a real
process produces ``transport_ready``. The in-process tests cover the buffering
and ordering contracts around it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._structlog_capture import captured_structlog as captured_structlog

#: Generous: a cold start measured 1.25 s median, and CI is slower than a laptop.
_COLD_START_TIMEOUT_S = 90.0


def _rpc(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload) + "\n").encode("utf-8")


def _cold_start_stderr(tmp_path: Path) -> tuple[list[dict[str, object]], str]:
    """Spawn a real trw-mcp, complete a handshake, and return its boot_phase events."""
    env = dict(os.environ)
    env["TRW_LOG_JSON"] = "1"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.Popen(
        # --log-json is a top-level flag, BEFORE the subcommand; argparse rejects
        # it after "serve". JSON is what makes the stderr stream parseable here.
        [sys.executable, "-m", "trw_mcp.server", "--log-json", "serve"],
        cwd=str(tmp_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(
            _rpc(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "prd-core-248-probe", "version": "1.0"},
                    },
                }
            )
        )
        proc.stdin.flush()
        reply = proc.stdout.readline()
        assert reply, "the server produced no initialize reply"
        # A real client sends `initialized` and then starts calling; both are
        # needed for initialize_answered and deferred_work_complete to fire.
        proc.stdin.write(_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        proc.stdin.write(_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}))
        proc.stdin.flush()
        proc.stdout.readline()
    finally:
        # Read stderr directly rather than through communicate(): the stdin pipe
        # has already been written to and closing it out from under
        # communicate() trips its internal bookkeeping on CPython 3.12.
        proc.terminate()
        proc.wait(timeout=_COLD_START_TIMEOUT_S)
        assert proc.stderr is not None
        err = proc.stderr.read()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
    text = err.decode("utf-8", errors="replace")
    events: list[dict[str, object]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("event") == "boot_phase":
            events.append(record)
    return events, text


@pytest.mark.slow
def test_boot_phase_sequence_is_monotonic_and_complete(tmp_path: Path) -> None:
    """A real cold start emits all five phases, non-decreasing, initialize before deferred."""
    from trw_mcp.server._boot_timeline import BOOT_ORIGIN_LABEL, BOOT_PHASES

    events, raw = _cold_start_stderr(tmp_path)

    seen = [str(e["phase"]) for e in events]
    missing = [p for p in BOOT_PHASES if p not in seen]
    assert not missing, f"missing boot phases {missing}; stderr was:\n{raw[-4000:]}"

    elapsed = [int(e["elapsed_ms"]) for e in events]  # type: ignore[arg-type]
    assert elapsed == sorted(elapsed), f"elapsed_ms must be non-decreasing, got {list(zip(seen, elapsed))}"
    assert seen.index("initialize_answered") < seen.index("deferred_work_complete")
    assert all(e["origin"] == BOOT_ORIGIN_LABEL for e in events), (
        "every event must name its own origin rather than implying process spawn"
    )


def test_phase_names_and_order_are_the_declared_contract() -> None:
    """BOOT_PHASES is the single source consumers read, in chronological order."""
    from trw_mcp.server._boot_timeline import BOOT_PHASES

    assert BOOT_PHASES == (
        "import_complete",
        "app_constructed",
        "transport_ready",
        "initialize_answered",
        "deferred_work_complete",
    )


def test_import_phases_are_recorded_by_merely_importing_the_server() -> None:
    """The first two phases are wired into the production import path, not a helper."""
    import trw_mcp.server._tools  # noqa: F401  — the module whose import emits app_constructed
    from trw_mcp.server._boot_timeline import recorded_boot_phases

    recorded = [phase for phase, _ms in recorded_boot_phases()]
    assert "import_complete" in recorded
    assert "app_constructed" in recorded
    assert recorded.index("import_complete") < recorded.index("app_constructed")


def test_events_are_buffered_until_emission_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A phase emitted before configure_logging must not reach the stdout JSON-RPC channel.

    structlog's unconfigured default is a PrintLogger writing to STDOUT, which on
    the serve process is the MCP wire. Buffering is what keeps a boot event from
    corrupting the stream, so this asserts the buffer is real rather than
    decorative.
    """
    from trw_mcp.server import _boot_timeline

    rendered: list[tuple[str, int]] = []
    monkeypatch.setattr(_boot_timeline, "_log_phase", lambda phase, ms: rendered.append((phase, ms)))
    monkeypatch.setattr(_boot_timeline, "_emission_enabled", False)
    monkeypatch.setattr(_boot_timeline, "_buffered", [])

    _boot_timeline.emit_boot_phase("import_complete")
    assert rendered == [], "a phase emitted before emission is enabled must not be rendered"

    _boot_timeline.enable_boot_timeline_emission()
    assert [p for p, _ in rendered] == ["import_complete"], "enabling must flush the buffer"

    _boot_timeline.emit_boot_phase("transport_ready")
    assert [p for p, _ in rendered] == ["import_complete", "transport_ready"]


def test_buffer_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unbounded buffer on the boot path is not worth the risk of a caller bug."""
    from trw_mcp.server import _boot_timeline

    monkeypatch.setattr(_boot_timeline, "_emission_enabled", False)
    monkeypatch.setattr(_boot_timeline, "_buffered", [])
    for _ in range(_boot_timeline._BUFFER_LIMIT + 25):
        _boot_timeline.emit_boot_phase("import_complete")
    assert len(_boot_timeline._buffered) == _boot_timeline._BUFFER_LIMIT


def test_emitted_event_carries_phase_elapsed_and_origin(captured_structlog: list[dict[str, object]]) -> None:
    """The payload shape FR02 specifies, and nothing beyond it (NFR03)."""
    from trw_mcp.server._boot_timeline import BOOT_ORIGIN_LABEL, emit_boot_phase, enable_boot_timeline_emission

    enable_boot_timeline_emission()
    emit_boot_phase("transport_ready")

    events = [log for log in captured_structlog if log.get("event") == "boot_phase"]
    assert events, f"no boot_phase captured; got {captured_structlog}"
    event = events[-1]
    assert event["phase"] == "transport_ready"
    assert isinstance(event["elapsed_ms"], int)
    assert event["origin"] == BOOT_ORIGIN_LABEL
    assert set(event) == {"event", "log_level", "phase", "elapsed_ms", "origin"}


def test_transport_ready_is_emitted_before_mcp_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wiring: the transport phase is on the real path, immediately before mcp.run()."""
    import structlog

    from trw_mcp.server import _transport

    order: list[str] = []
    monkeypatch.setattr(_transport, "emit_boot_phase", lambda phase: order.append(phase))
    monkeypatch.setattr(_transport.mcp, "run", lambda: order.append("mcp.run"))

    _transport.resolve_and_run_transport(debug=False, log=structlog.get_logger("test"))

    assert order == ["transport_ready", "mcp.run"]
