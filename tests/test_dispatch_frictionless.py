"""Frictionless trw_dispatch: simple target names, fan-out, plain roles, compact output."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from tests.conftest import extract_tool_fn
from trw_mcp.dispatch._targets import (
    Target,
    TargetError,
    compact_result,
    list_clients,
    parse_targets,
    resolve_role,
)
from trw_mcp.dispatch._types import DispatchResult
from trw_mcp.tools.dispatch import register_dispatch_tools


class _Cfg:
    def __init__(self) -> None:
        self.dispatch_enabled_clients = ["codex", "claude", "agy", "grok"]
        self.dispatch_default_client = "codex"
        self.dispatch_default_models: dict[str, str] = {}
        self.dispatch_default_timeout_s = 600
        self.dispatch_default_read_only = True


class _Root:
    dispatch = _Cfg()


def _tool() -> Any:
    server = FastMCP("t")
    register_dispatch_tools(server)
    return extract_tool_fn(server, "trw_dispatch")


def _result(client: str, text: str, ok: bool = True) -> DispatchResult:
    return DispatchResult(
        client=client,  # type: ignore[arg-type]
        argv_redacted=[client],
        read_only_enforced=True,
        exit_code=0 if ok else 1,
        timed_out=False,
        duration_s=0.5,
        text=text,
        raw_stdout=text,
        raw_stderr="" if ok else "auth expired: run `grok login`",
        structured={"usage": {"input_tokens": 99999}},
    )


# --- pure name resolution ---


def test_parse_targets_accepts_names_models_aliases_and_lists() -> None:
    assert parse_targets("codex, agy ,grok:grok-4.7", None) == [
        Target("codex", None),
        Target("agy", None),
        Target("grok", "grok-4.7"),
    ]
    assert parse_targets("sonnet", None) == [Target("claude", "sonnet")]
    assert parse_targets("antigravity", None) == [Target("agy", None)]
    assert parse_targets("cursor", "m1") == [Target("cursor-cli", "m1")]
    assert parse_targets("codex,codex", None) == [Target("codex", None)]
    assert parse_targets(None, "x") is None


def test_unknown_target_names_the_valid_choices() -> None:
    with pytest.raises(TargetError, match=r"grok:grok-4\.7"):
        parse_targets("gork", None)


def test_resolve_role_maps_plain_roles_and_rejects_typos() -> None:
    assert resolve_role("review") == ("code-review", False)
    assert resolve_role("critique") == ("adversarial-audit", False)
    assert resolve_role("plan") == ("plan", False)
    assert resolve_role("implement") == (None, True)
    assert resolve_role(None) == (None, None)
    with pytest.raises(TargetError, match="critique"):
        resolve_role("reveiw")


def test_list_clients_covers_registry_and_roles() -> None:
    out = list_clients({"codex": "gpt-x"})
    ids = [c["client"] for c in out["clients"]]  # type: ignore[index]
    assert {"codex", "agy", "grok", "claude"} <= set(ids)
    codex = next(c for c in out["clients"] if c["client"] == "codex")  # type: ignore[union-attr]
    assert codex["default_model"] == "gpt-x"
    assert "critique" in out["roles"]  # type: ignore[operator]


def test_list_clients_reports_provider_fallback_without_inventing_other_models() -> None:
    out = list_clients()
    clients = {c["client"]: c for c in out["clients"]}  # type: ignore[union-attr]
    assert clients["codex"]["default_model"] == "gpt-6.1-sol"
    assert all("default_model" not in c for cid, c in clients.items() if cid != "codex")


def test_compact_result_keeps_answer_and_failure_context() -> None:
    good = compact_result("codex", {"ok": True, "text": "OK", "duration_s": 1.0, "structured": {"x": 1}})
    assert good == {"target": "codex", "ok": True, "text": "OK", "duration_s": 1.0}
    bad = compact_result("grok", {"ok": False, "text": "", "raw_stderr": "boom", "exit_code": 1})
    assert bad["error"] == "boom" and bad["exit_code"] == 1 and bad["reason"] == "failed"


# --- tool behavior ---


def test_fanout_wait_runs_every_target_and_returns_compact_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    seen: list[tuple[str, str | None]] = []

    def fake(req: Any) -> DispatchResult:
        seen.append((req.client, req.model))
        return _result(req.client, f"hi from {req.client}", ok=req.client != "grok")

    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", fake)
    out = _tool()(prompt="critique this", client="codex,agy,grok:grok-4.7", role="critique", wait=True)
    assert sorted(seen) == [("agy", None), ("codex", "gpt-6.1-sol"), ("grok", "grok-4.7")]
    assert ("grok", "grok-4.7") in seen
    assert out["status"] == "2/3 succeeded"
    targets = [r["target"] for r in out["results"]]
    assert targets == ["codex", "agy", "grok:grok-4.7"]
    assert out["results"][0] == {"target": "codex", "ok": True, "text": "hi from codex", "duration_s": 0.5}
    assert "grok login" in out["results"][2]["error"]


def test_fanout_lane_with_disabled_client_is_reported_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", lambda req: _result(req.client, "ok"))
    out = _tool()(prompt="p", client="codex,copilot", wait=True)
    lanes = {r["target"]: r for r in out["results"]}
    assert lanes["codex"]["ok"] is True
    assert lanes["copilot"]["ok"] is False and "disabled" in lanes["copilot"]["error"]


def test_wait_without_timeout_defaults_to_cap_instead_of_refusing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    captured: dict[str, int] = {}

    def fake(req: Any) -> DispatchResult:
        captured["t"] = req.timeout_s
        return _result("codex", "OK")

    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", fake)
    out = _tool()(prompt="p", client="codex", wait=True)
    assert out["status"] == "succeeded"
    assert captured["t"] == 120
    assert "structured" not in out["result"]  # compact on success


def test_a_role_preset_never_refuses_the_callers_permission_and_implement_enables_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DISPATCH-SIMPLIFY: a read-only preset only defaults read_only; the caller's explicit choice wins."""
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    seen: list[bool] = []

    def fake(req: Any) -> DispatchResult:
        seen.append(req.read_only)
        return _result("codex", "done")

    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", fake)
    _tool()(prompt="p", client="codex", role="review", allow_writes=True, wait=True)
    _tool()(prompt="p", client="codex", role="critique", wait=True)
    _tool()(prompt="p", client="codex", role="implement", wait=True)
    assert seen == [False, True, False]


def test_unknown_role_and_target_are_refused_with_guidance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    assert "critique" in str(_tool()(prompt="p", client="codex", role="reveiw")["error"])
    assert "clients" in str(_tool()(prompt="p", client="gork")["error"])


def test_clients_action_lists_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    out = _tool()(action="clients")
    assert any(c["client"] == "grok" for c in out["clients"])


def test_claude_child_loads_project_instructions_but_no_project_hooks() -> None:
    # `--setting-sources user` alone hid AGENTS.md from dispatched claude children
    # (measured: "NONE" vs the heading). Project sources load it; hooks stay off.
    from trw_mcp.dispatch._client_specs import CLIENT_SPECS

    spec = CLIENT_SPECS["claude"]
    for argv in (spec.isolation_argv, spec.reviewer_argv_template, spec.trw_access_argv_template):
        assert argv is not None
        args = list(argv)
        assert "project" in args[args.index("--setting-sources") + 1].split(",")
        assert args[args.index("--settings") + 1] == '{"disableAllHooks":true}'


def test_readonly_role_overrides_writable_config_default(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _Root()
    root.dispatch = _Cfg()
    root.dispatch.dispatch_default_read_only = False
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: root)
    seen: dict[str, Any] = {}

    def fake(req: Any) -> DispatchResult:
        seen["ro"] = req.read_only
        return _result("codex", "ok")

    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", fake)
    _tool()(prompt="p", client="codex", role="review", wait=True)
    assert seen["ro"] is True


def test_background_runner_env_keeps_claude_setting_sources_opt_out() -> None:
    from trw_mcp.dispatch._env import build_runner_env

    env = build_runner_env("claude", {"TRW_DISPATCH_CLAUDE_SETTING_SOURCES": "user", "SECRET": "x"})
    assert env["TRW_DISPATCH_CLAUDE_SETTING_SOURCES"] == "user"
    assert "SECRET" not in env


def test_implement_role_refuses_an_explicit_read_only_instead_of_silently_widening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DISPATCH-DELTA-LOW (2): role='implement' used to flip read_only=True to writes without a word."""
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    launched: list[Any] = []
    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", lambda req: launched.append(req) or _result("codex", "x"))
    out = _tool()(prompt="p", client="codex", role="implement", read_only=True, wait=True)
    assert out["exit_code"] == 2
    assert "read_only=True" in str(out["error"])
    assert launched == []


def _git_repo(path: Any, *, dirty: bool) -> Any:
    import subprocess

    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if dirty:
        (path / "wip.txt").write_text("uncommitted\n")
    return path


def test_uncommitted_work_warning_fires_only_for_a_writable_child_in_a_dirty_tree(tmp_path: Any) -> None:
    """DISPATCH-DELTA-LOW (1): warn (never refuse) before a writer lands on uncommitted work."""
    from trw_mcp.dispatch._resolve import uncommitted_work_warning

    dirty = _git_repo(tmp_path / "dirty", dirty=True)
    clean = _git_repo(tmp_path / "clean", dirty=False)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "1 uncommitted path" in uncommitted_work_warning(dirty, writes=True)
    assert uncommitted_work_warning(dirty, writes=False) == ""
    assert uncommitted_work_warning(clean, writes=True) == ""
    assert uncommitted_work_warning(plain / "missing", writes=True) == ""


def test_writable_dispatch_into_a_dirty_tree_carries_the_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", lambda req: _result("codex", "done"))
    repo = _git_repo(tmp_path / "repo", dirty=True)
    out = _tool()(prompt="p", client="codex", role="implement", cwd=str(repo), wait=True)
    assert out["status"] == "succeeded"
    assert "uncommitted" in str(out["warning"])
    readonly = _tool()(prompt="p", client="codex", role="review", cwd=str(repo), wait=True)
    assert "warning" not in readonly
