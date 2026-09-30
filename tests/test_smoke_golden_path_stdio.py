"""Smoke tier: the documented golden path through REAL stdio servers, ending at trw_deliver (E2E-INC-001 gap).

Unit and in-process tests exercised build_check and deliver separately; the only golden-path e2e test stopped
at build_check, so a deliver-gate break on the documented path (and a trw_status READY that deliver then
refused) shipped unseen. These tests drive ``python -m trw_mcp.server`` over stdio exactly as a client does,
and assert what the user sees at each step -- including that trw_status and trw_deliver agree.

Marker ``smoke``: excluded from the ``-m unit`` loop, selected with ``-m smoke``; budget <= 60 s for the file.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests._memory_fixtures import MemoryDaemon, attach_checkout
from tests._stdio_harness import ServerProcess, StdioServerHarness, stdio_import_skip_reason

pytestmark = [pytest.mark.smoke, pytest.mark.integration]

_TIMEOUT_S = 60.0
_BUILD = {"tests_passed": True, "test_count": 12, "scope": "full"}


@pytest.fixture
def harness(memory_daemon: MemoryDaemon, tmp_path: Path) -> Iterator[StdioServerHarness]:
    if reason := stdio_import_skip_reason():
        pytest.skip(reason)  # skip-category: optional-dependency
    project = tmp_path / "repo"
    project.mkdir()
    # A real repository: the build receipt binds the working tree (E2E-INC-018), and a bare .git dir is not one.
    (project / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    for argv in (["init", "-q"], ["add", "-A"], ["-c", "user.email=e@e", "-c", "user.name=e", "commit", "-qm", "init"]):
        subprocess.run(["git", *argv], cwd=project, check=True, capture_output=True)
    (project / ".trw").mkdir()
    (project / ".trw" / "config.yaml").write_text("target_platforms:\n- claude-code\n", encoding="utf-8")
    attach_checkout(project / ".trw", memory_daemon)
    runner = StdioServerHarness(project, memory_daemon.user_dir, tmp_path / "stderr")
    try:
        yield runner
    finally:
        runner.teardown()


def _raw(runner: StdioServerHarness, server: ServerProcess, tool: str, arguments: dict[str, Any]) -> tuple[bool, Any]:
    """One ``tools/call``; returns (is_error, payload) so refusals can be asserted, not just successes."""
    request_id = server.next_id()
    runner._send(
        server,
        {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
    )
    reply = runner._await_id(server, request_id, time.monotonic() + _TIMEOUT_S)
    assert "error" not in reply, (tool, reply)  # a JSON-RPC protocol error is never an acceptable answer
    result = reply.get("result") or {}
    text = (result.get("content") or [{}])[0].get("text", "")
    payload = result.get("structuredContent")
    if payload is None:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = text
    return bool(result.get("isError")), payload


def _ok(runner: StdioServerHarness, server: ServerProcess, tool: str, arguments: dict[str, Any]) -> Any:
    is_error, payload = _raw(runner, server, tool, arguments)
    assert not is_error, (tool, payload)
    return payload


def _session(runner: StdioServerHarness, label: str) -> ServerProcess:
    server = runner.spawn(label)
    runner.initialize(server, started_at=time.monotonic())
    _ok(runner, server, "trw_session_start", {"query": "smoke"})
    return server


def _gate_ready(status: Any) -> bool:
    return isinstance(status, dict) and status.get("deliver_gate_summary") == "READY"


def test_golden_path_delivers_and_status_agrees(harness: StdioServerHarness) -> None:
    server = _session(harness, "golden")
    _ok(harness, server, "trw_init", {"task_name": "smoke-golden", "task_type": "coding"})
    learned = _ok(harness, server, "trw_learn", {"summary": "smoke: widget cache", "detail": "golden path"})
    assert learned["status"] == "recorded"
    found = _ok(harness, server, "trw_recall", {"query": "widget cache"})
    assert learned["learning_id"] in {stub["id"] for stub in found["learnings"]}
    _ok(harness, server, "trw_build_check", {**_BUILD, "static_checks_clean": True})
    _ok(harness, server, "trw_review", {"findings": [], "review_completed": True})
    status = _ok(harness, server, "trw_status", {})
    assert _gate_ready(status), status.get("deliver_gate_summary")
    delivered = _ok(harness, server, "trw_deliver", {})
    assert delivered.get("success") is not False, delivered


@pytest.mark.parametrize(
    ("build", "label"),
    [
        ({**_BUILD}, "static-omitted"),  # CONSTITUTION §1.a: static checks are part of a passing build
        ({**_BUILD, "static_checks_clean": False}, "static-failed"),
        ({**_BUILD, "tests_passed": False, "static_checks_clean": True}, "tests-failed"),
    ],
)
def test_status_never_says_ready_when_deliver_refuses(
    harness: StdioServerHarness, build: dict[str, Any], label: str
) -> None:
    server = _session(harness, label)
    _ok(harness, server, "trw_init", {"task_name": f"smoke-{label}", "task_type": "coding"})
    _raw(harness, server, "trw_build_check", build)
    _ok(harness, server, "trw_review", {"findings": [], "review_completed": True})
    status = _ok(harness, server, "trw_status", {})
    is_error, delivered = _raw(harness, server, "trw_deliver", {})
    refused = is_error or (isinstance(delivered, dict) and delivered.get("success") is False)
    assert refused, delivered
    assert not _gate_ready(status), status  # the preview must not promise what deliver refuses


def test_a_learning_survives_into_the_next_session(harness: StdioServerHarness) -> None:
    first = _session(harness, "first")
    learned = _ok(harness, first, "trw_learn", {"summary": "smoke: survives restart", "detail": "cross-session"})
    harness.reap_one(first)
    second = _session(harness, "second")
    found = _ok(harness, second, "trw_recall", {"query": "survives restart"})
    assert learned["learning_id"] in {stub["id"] for stub in found["learnings"]}


def test_an_edit_after_the_build_check_is_stale_for_status_and_deliver(harness: StdioServerHarness) -> None:
    """E2E-INC-018: an edit that never passed through a Write/Edit hook (a shell edit) must not ride old evidence."""
    server = _session(harness, "stale")
    _ok(harness, server, "trw_init", {"task_name": "smoke-stale", "task_type": "coding"})
    _ok(harness, server, "trw_build_check", {**_BUILD, "static_checks_clean": True})
    _ok(harness, server, "trw_review", {"findings": [], "review_completed": True})
    assert _gate_ready(_ok(harness, server, "trw_status", {}))
    app = harness.project_root / "app.py"
    app.write_text(app.read_text(encoding="utf-8").replace("return 1", "return 2"), encoding="utf-8")
    status = _ok(harness, server, "trw_status", {})
    assert not _gate_ready(status) and "app.py" in str(status.get("deliver_gate_summary")), status
    is_error, delivered = _raw(harness, server, "trw_deliver", {})
    assert is_error or (isinstance(delivered, dict) and delivered.get("success") is False), delivered
