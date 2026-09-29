"""PRD-CORE-334 / R8-SCOPE T2(6): a failure or decision recorded in one session is found by type in the next.

Session A and session B are two separate ``python -m trw_mcp.server`` processes
over stdio (the ``tests/_stdio_harness`` spawner), sharing only the checkout and
the memory daemon -- no in-process state can carry the answer across. A records
an incident and a decision with ``trw_learn``; B, started after A has exited,
asks ``trw_recall`` for each type through ``options={"record_type": ...}``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests._memory_fixtures import MemoryDaemon, attach_checkout
from tests._stdio_harness import ServerProcess, StdioServerHarness, stdio_import_skip_reason

_TIMEOUT_S = 100.0


@pytest.fixture
def harness(memory_daemon: MemoryDaemon, tmp_path: Path) -> Iterator[StdioServerHarness]:
    if reason := stdio_import_skip_reason():
        pytest.skip(reason)  # skip-category: optional-dependency
    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    (project / ".trw").mkdir()
    (project / ".trw" / "config.yaml").write_text("target_platforms:\n- claude-code\n", encoding="utf-8")
    attach_checkout(project / ".trw", memory_daemon)  # pins a fresh namespace and grants this checkout
    runner = StdioServerHarness(project, memory_daemon.user_dir, tmp_path / "stderr")
    try:
        yield runner
    finally:
        runner.teardown()


def _call(runner: StdioServerHarness, server: ServerProcess, tool: str, arguments: dict[str, Any]) -> Any:
    """One ``tools/call``; the tool's JSON answer (``trw_recall`` has no output schema, so it is the text)."""
    request_id = server.next_id()
    params = {"name": tool, "arguments": arguments}
    runner._send(server, {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params})
    reply = runner._await_id(server, request_id, time.monotonic() + _TIMEOUT_S)
    result = reply.get("result") or {}
    assert "error" not in reply and not result.get("isError"), (tool, reply)
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


def _session(runner: StdioServerHarness, label: str) -> ServerProcess:
    server = runner.spawn(label)
    runner.initialize(server, started_at=time.monotonic())
    _call(runner, server, "trw_session_start", {})
    return server


def test_a_recorded_decision_and_failure_are_recalled_by_type_in_a_fresh_session(
    harness: StdioServerHarness,
) -> None:
    session_a = _session(harness, "session-a")
    recorded = {
        kind: _call(
            harness, session_a, "trw_learn", {"summary": f"ledger {kind}: {text}", "detail": text, "type": kind}
        )
        for kind, text in (("incident", "the sync cursor deadlocked"), ("decision", "recall filters by type in SQL"))
    }
    assert {answer["status"] for answer in recorded.values()} == {"recorded"}
    harness.reap_one(session_a)
    assert session_a.proc.poll() is not None  # A is gone before B starts

    session_b = _session(harness, "session-b")
    for kind, answer in recorded.items():
        for query in ("*", "ledger"):
            found = _call(harness, session_b, "trw_recall", {"query": query, "options": {"record_type": kind}})
            assert [stub["id"] for stub in found["learnings"]] == [answer["learning_id"]], (kind, query, found)
    unfiltered = _call(harness, session_b, "trw_recall", {"query": "ledger"})
    assert {stub["id"] for stub in unfiltered["learnings"]} >= {a["learning_id"] for a in recorded.values()}
