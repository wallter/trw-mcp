"""Behavior tests for the dispatch MCP tools + registration safety.

Covers ``trw_dispatch`` (wait / background / resolution-error paths) and
``trw_dispatch_status``, plus a guard that registering the dispatch tools on the
real server does NOT drop any pre-existing core tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from tests.conftest import _run_async, extract_tool_fn
from trw_mcp.dispatch._jobs import DispatchJob
from trw_mcp.dispatch._types import DispatchResult
from trw_mcp.tools.dispatch import register_dispatch_tools


class _Cfg:
    def __init__(self, **overrides: Any) -> None:
        self.dispatch_enabled_clients: list[str] = ["codex", "claude", "agy", "opencode"]
        self.dispatch_default_client: str | None = "codex"
        self.dispatch_default_models: dict[str, str] = {}
        self.dispatch_default_timeout_s: int = 600
        self.dispatch_default_read_only: bool = True
        self.dispatch_role_client: dict[str, str] = {}
        for key, value in overrides.items():
            setattr(self, key, value)


class _RootCfg:
    def __init__(self, dispatch_cfg: _Cfg) -> None:
        self.dispatch = dispatch_cfg


def _tool(name: str) -> Any:
    server = FastMCP("test")
    register_dispatch_tools(server)
    return extract_tool_fn(server, name)


def _fake_result(text: str = "audited.", exit_code: int = 0) -> DispatchResult:
    return DispatchResult(
        client="codex",
        argv_redacted=["codex", "exec", "<prompt:5 chars>"],
        read_only_enforced=True,
        exit_code=exit_code,
        timed_out=False,
        duration_s=0.1,
        text=text,
        raw_stdout=text,
        raw_stderr="",
        structured=None,
    )


# --- trw_dispatch wait=True (synchronous) ---


def test_dispatch_wait_true_succeeded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", lambda _req: _fake_result("answer"))
    # timeout_s within the F-02 wait cap so the synchronous path runs.
    out = _tool("trw_dispatch")(prompt="check X", client="codex", wait=True, timeout_s=30)
    assert out["job_id"] is None
    assert out["status"] == "succeeded"
    assert out["result"]["text"] == "answer"


def test_dispatch_wait_true_failed_on_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", lambda _req: _fake_result("", exit_code=1))
    out = _tool("trw_dispatch")(prompt="check X", client="codex", wait=True, timeout_s=30)
    assert out["status"] == "failed"


# --- trw_dispatch wait=False (background) ---


def test_dispatch_wait_false_returns_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))

    fake_job = DispatchJob(
        job_id="abc123",
        client="codex",
        status="running",
        created_at="2026-06-21T00:00:00+00:00",
        pid=4242,
        argv_redacted=["codex", "exec", "<prompt:5 chars>"],
        result_path="/tmp/abc123.result.json",
        job_path="/tmp/abc123.json",
    )
    captured: dict[str, Any] = {}

    def _fake_start(req: Any) -> DispatchJob:
        captured["req"] = req
        return fake_job

    monkeypatch.setattr("trw_mcp.tools.dispatch.start_background", _fake_start)

    out = _tool("trw_dispatch")(prompt="check X", client="codex")  # wait defaults False
    assert out["job_id"] == "abc123"
    assert out["status"] == "running"
    assert out["client"] == "codex"
    assert "check X" not in str(out)  # prompt never echoed back
    # The resolved request actually reached start_background.
    assert getattr(captured["req"], "client") == "codex"


def test_dispatch_allow_writes_overrides_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    captured: dict[str, Any] = {}

    def _fake_start(req: Any) -> DispatchJob:
        captured["req"] = req
        return DispatchJob(
            job_id="x",
            client="codex",
            status="running",
            created_at="2026-06-21T00:00:00+00:00",
            pid=1,
            argv_redacted=[],
            result_path="/tmp/x.result.json",
            job_path="/tmp/x.json",
        )

    monkeypatch.setattr("trw_mcp.tools.dispatch.start_background", _fake_start)
    _tool("trw_dispatch")(prompt="p", client="codex", read_only=True, allow_writes=True)
    assert getattr(captured["req"], "read_only") is False


def test_dispatch_explicit_read_only_true_honored_over_config_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-03: caller read_only=True wins even when config default is read_only=False.

    This is the safety bypass the audit flagged — an explicit read-only request
    must never be silently overridden by a write-permitting config default.
    """
    monkeypatch.setattr(
        "trw_mcp.tools.dispatch.get_config",
        lambda: _RootCfg(_Cfg(dispatch_default_read_only=False)),
    )
    captured: dict[str, Any] = {}

    def _fake_start(req: Any) -> DispatchJob:
        captured["req"] = req
        return DispatchJob(
            job_id="x",
            client="codex",
            status="running",
            created_at="2026-06-21T00:00:00+00:00",
            pid=1,
            argv_redacted=[],
            result_path="/tmp/x.result.json",
            job_path="/tmp/x.json",
        )

    monkeypatch.setattr("trw_mcp.tools.dispatch.start_background", _fake_start)
    _tool("trw_dispatch")(prompt="p", client="codex", read_only=True, allow_writes=False)
    assert getattr(captured["req"], "read_only") is True


# --- F-02: cap wait=True timeout ---


def test_dispatch_wait_true_rejected_for_long_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    called: dict[str, bool] = {"ran": False}

    def _should_not_run(_req: Any) -> DispatchResult:
        called["ran"] = True
        return _fake_result()

    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", _should_not_run)
    out = _tool("trw_dispatch")(prompt="p", client="codex", wait=True, timeout_s=600)
    assert out["exit_code"] == 2
    assert "wait=True" in out["error"]
    assert called["ran"] is False  # the runner was never invoked


def test_dispatch_wait_true_allowed_at_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", lambda _req: _fake_result("ok"))
    out = _tool("trw_dispatch")(prompt="p", client="codex", wait=True, timeout_s=120)
    assert out["status"] == "succeeded"


# --- F-07: cwd traversal guard ---


def test_dispatch_cwd_with_dotdot_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    out = _tool("trw_dispatch")(prompt="p", client="codex", cwd="/work/../etc")
    assert out["exit_code"] == 2
    assert "cwd must not contain" in out["error"]


def test_dispatch_cwd_without_dotdot_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    captured: dict[str, Any] = {}

    def _fake_start(req: Any) -> DispatchJob:
        captured["req"] = req
        return DispatchJob(
            job_id="x",
            client="codex",
            status="running",
            created_at="2026-06-21T00:00:00+00:00",
            pid=1,
            argv_redacted=[],
            result_path="/tmp/x.result.json",
            job_path="/tmp/x.json",
        )

    monkeypatch.setattr("trw_mcp.tools.dispatch.start_background", _fake_start)
    out = _tool("trw_dispatch")(prompt="p", client="codex", cwd="/work/project")
    assert "error" not in out
    assert str(getattr(captured["req"], "cwd")) == "/work/project"


# --- F-07 (full): cwd confinement to project root when writes enabled ---


def _patch_project_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Make resolve_trw_dir().parent == *root* (the tool imports it locally)."""
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: root / ".trw")


def _capture_start(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _fake_start(req: Any) -> DispatchJob:
        captured["req"] = req
        return DispatchJob(
            job_id="x",
            client="codex",
            status="running",
            created_at="2026-06-21T00:00:00+00:00",
            pid=1,
            argv_redacted=[],
            result_path="/tmp/x.result.json",
            job_path="/tmp/x.json",
        )

    monkeypatch.setattr("trw_mcp.tools.dispatch.start_background", _fake_start)
    return captured


def test_writes_enabled_cwd_outside_root_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    _patch_project_root(monkeypatch, tmp_path)
    captured = _capture_start(monkeypatch)

    outside = tmp_path.parent / "elsewhere"
    out = _tool("trw_dispatch")(prompt="p", client="codex", allow_writes=True, cwd=str(outside))

    assert out["exit_code"] == 2
    assert "within the project root" in out["error"]
    assert "req" not in captured  # dispatch was NOT spawned


def test_writes_enabled_cwd_inside_root_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    _patch_project_root(monkeypatch, tmp_path)
    captured = _capture_start(monkeypatch)

    inside = tmp_path / "subdir"
    inside.mkdir()
    out = _tool("trw_dispatch")(prompt="p", client="codex", allow_writes=True, cwd=str(inside))

    assert "error" not in out
    assert captured["req"] is not None  # dispatch reached start_background


def test_read_only_cwd_outside_root_not_confined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Read-only (the default) dispatches are lower-risk and must NOT be confined.
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    _patch_project_root(monkeypatch, tmp_path)
    captured = _capture_start(monkeypatch)

    outside = tmp_path.parent / "elsewhere"
    out = _tool("trw_dispatch")(prompt="p", client="codex", read_only=True, cwd=str(outside))

    assert "error" not in out
    assert captured["req"] is not None  # no confinement error, reached dispatch


# --- F-12: cap raw streams returned through MCP (failure path) ---


def test_dispatch_wait_true_truncates_long_raw_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """A FAILED result keeps the raw streams but truncates them at the 50k cap.

    Raw streams are only carried through MCP on failure (``ok=False``); success
    omits them (see the success-path tests below). The truncation cap must stay
    intact on the failure path where a caller still needs raw diagnostics.
    """
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    huge = "x" * 200_000
    # exit_code=1 => ok is False => streams retained (and truncated).
    big_result = DispatchResult(
        client="codex",
        argv_redacted=["codex", "exec", "<prompt:1 chars>"],
        read_only_enforced=True,
        exit_code=1,
        timed_out=False,
        duration_s=0.1,
        text="",
        raw_stdout=huge,
        raw_stderr=huge,
        structured=None,
    )
    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", lambda _req: big_result)
    out = _tool("trw_dispatch")(prompt="p", client="codex", wait=True, timeout_s=10)
    result = out["result"]
    assert out["status"] == "failed"
    assert len(result["raw_stdout"]) < len(huge)
    assert result["raw_stdout"].endswith("full output in result file]")
    assert len(result["raw_stderr"]) < len(huge)


# --- W6: omit raw streams on SUCCESS (compact-by-default) ---


def test_dispatch_wait_true_success_omits_raw_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """On ``ok=True`` the raw stdout/stderr are omitted; text/structured remain."""
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    monkeypatch.setattr(
        "trw_mcp.tools.dispatch.dispatch",
        lambda _req: _fake_result("the answer"),
    )
    out = _tool("trw_dispatch")(prompt="p", client="codex", wait=True, timeout_s=10)
    result = out["result"]
    assert out["status"] == "succeeded"
    assert "raw_stdout" not in result
    assert "raw_stderr" not in result
    assert result["raw_streams_omitted"] is True
    # The normalized answer is preserved — nothing load-bearing is lost.
    assert result["text"] == "the answer"


def test_dispatch_wait_true_verbose_restores_raw_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """``verbose=True`` restores the legacy full shape even on success."""
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    monkeypatch.setattr(
        "trw_mcp.tools.dispatch.dispatch",
        lambda _req: _fake_result("the answer"),
    )
    out = _tool("trw_dispatch")(prompt="p", client="codex", wait=True, timeout_s=10, verbose=True)
    result = out["result"]
    assert out["status"] == "succeeded"
    assert result["raw_stdout"] == "the answer"
    assert "raw_streams_omitted" not in result


# --- trw_dispatch resolution error ---


def test_dispatch_resolution_error_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trw_mcp.tools.dispatch.get_config",
        lambda: _RootCfg(_Cfg(dispatch_default_client=None)),
    )
    out = _tool("trw_dispatch")(prompt="p", client=None, role=None)
    assert "error" in out
    assert out["exit_code"] == 2


def test_dispatch_gemini_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    out = _tool("trw_dispatch")(prompt="p", client="gemini")
    assert "error" in out
    assert "agy" in str(out["error"])
    assert out["exit_code"] == 2


# --- trw_dispatch_status ---


def test_status_running_no_result(monkeypatch: pytest.MonkeyPatch) -> None:
    job = DispatchJob(
        job_id="j1",
        client="codex",
        status="running",
        created_at="2026-06-21T00:00:00+00:00",
        pid=1,
        argv_redacted=[],
        result_path="/tmp/j1.result.json",
        job_path="/tmp/j1.json",
    )
    monkeypatch.setattr("trw_mcp.dispatch._jobs.get_status", lambda jid, trw_dir=None: job)
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_status", lambda jid: job)
    out = _tool("trw_dispatch_status")(job_id="j1")
    assert out["job_id"] == "j1"
    assert out["status"] == "running"
    assert out["result"] is None


def test_status_terminal_includes_result(monkeypatch: pytest.MonkeyPatch) -> None:
    job = DispatchJob(
        job_id="j2",
        client="codex",
        status="succeeded",
        created_at="2026-06-21T00:00:00+00:00",
        pid=1,
        argv_redacted=[],
        result_path="/tmp/j2.result.json",
        job_path="/tmp/j2.json",
    )
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_status", lambda jid: job)
    monkeypatch.setattr("trw_mcp.dispatch._jobs.get_result", lambda jid, trw_dir=None: _fake_result("done"))
    out = _tool("trw_dispatch_status")(job_id="j2")
    assert out["status"] == "succeeded"
    assert out["result"]["text"] == "done"
    # W6: a successful terminal result omits raw streams and points at the
    # on-disk result file (which still holds the full untruncated streams).
    assert "raw_stdout" not in out["result"]
    assert out["result"]["raw_streams_omitted"] is True
    assert out["result"]["raw_streams_result_file"] == "/tmp/j2.result.json"


def test_status_terminal_verbose_includes_raw_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """``verbose=True`` restores raw streams on a successful terminal result."""
    job = DispatchJob(
        job_id="j3",
        client="codex",
        status="succeeded",
        created_at="2026-06-21T00:00:00+00:00",
        pid=1,
        argv_redacted=[],
        result_path="/tmp/j3.result.json",
        job_path="/tmp/j3.json",
    )
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_status", lambda jid: job)
    monkeypatch.setattr("trw_mcp.dispatch._jobs.get_result", lambda jid, trw_dir=None: _fake_result("done"))
    out = _tool("trw_dispatch_status")(job_id="j3", verbose=True)
    assert out["result"]["raw_stdout"] == "done"
    assert "raw_streams_omitted" not in out["result"]


def test_status_terminal_failed_keeps_raw_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """A FAILED terminal result keeps raw streams even without verbose."""
    job = DispatchJob(
        job_id="j4",
        client="codex",
        status="failed",
        created_at="2026-06-21T00:00:00+00:00",
        pid=1,
        argv_redacted=[],
        result_path="/tmp/j4.result.json",
        job_path="/tmp/j4.json",
    )
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_status", lambda jid: job)
    monkeypatch.setattr(
        "trw_mcp.dispatch._jobs.get_result",
        lambda jid, trw_dir=None: _fake_result("", exit_code=1),
    )
    out = _tool("trw_dispatch_status")(job_id="j4")
    assert out["status"] == "failed"
    assert "raw_stdout" in out["result"]
    assert "raw_streams_omitted" not in out["result"]


def test_status_unknown_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(jid: str) -> Any:
        raise KeyError(jid)

    monkeypatch.setattr("trw_mcp.tools.dispatch.get_status", _raise)
    out = _tool("trw_dispatch_status")(job_id="ghost")
    assert "error" in out
    assert "ghost" in str(out["error"])


def test_status_invalid_job_id_is_non_throwing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_job_id: str) -> Any:
        raise ValueError("invalid dispatch job_id")

    monkeypatch.setattr("trw_mcp.tools.dispatch.get_status", _raise)
    out = _tool("trw_dispatch_status")(job_id="../../escape")

    assert "error" in out


# --- registration safety: new tools present + core tools NOT dropped ---


def _registered_tool_names() -> set[str]:
    """Return the RAW set of registered tool names on the real singleton.

    Uses FastMCP's ``_list_tools()`` (the pre-middleware registration view)
    rather than the public ``list_tools()`` — the public view applies
    progressive-disclosure / deferral masking that hides some long-description
    tools (incl. ``trw_dispatch``), which is orthogonal to whether the tool was
    actually REGISTERED. Registration drops are exactly what this guard catches.
    """
    from trw_mcp.server._app import mcp

    tools = _run_async(mcp._list_tools())
    return {t.name for t in tools}


def test_full_registration_includes_dispatch_and_preserves_core() -> None:
    """Registering ALL tools on the real server yields dispatch + every core tool.

    Guards against the historical regression where a registrar reorder silently
    dropped trw_deliver / adopt_run from the registered tool set.
    """
    from trw_mcp.server._tools import _register_tools

    _register_tools()
    names = _registered_tool_names()

    # New Phase 3 tools are registered.
    assert "trw_dispatch" in names
    assert "trw_dispatch_status" in names

    # Pre-existing core tools survive the new registration.
    core = {
        "trw_session_start",
        "trw_deliver",
        "trw_checkpoint",
        "trw_build_check",
        "trw_review",
        "trw_learn",
        "trw_recall",
    }
    missing = core - names
    assert not missing, f"core tools dropped: {sorted(missing)}"
