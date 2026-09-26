"""PRD-CORE-290-FR03: dispatch defaults come from the task-class table, overridable, and recorded.

Precedence for each of effort and model: explicit request > operator config >
table default (the role's task class, PRD-CORE-290-FR02). The selected source and
the requested-versus-applied values are recorded on the request and the result.
Roles are the existing dispatch roles; there is no new taxonomy.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from tests.conftest import extract_tool_fn
from trw_mcp.dispatch._resolve import DispatchResolutionError, resolve_dispatch_request
from trw_mcp.dispatch._types import DispatchRequest, DispatchResult

pytestmark = pytest.mark.unit


class _Cfg:
    def __init__(self, **overrides: Any) -> None:
        self.dispatch_enabled_clients = ["claude", "codex", "agy"]
        self.dispatch_default_client = "claude"
        self.dispatch_default_models: dict[str, str] = {}
        self.dispatch_default_timeout_s = 600
        self.dispatch_default_read_only = True
        self.dispatch_role_client: dict[str, str] = {}
        self.dispatch_default_effort: str | None = None
        for key, value in overrides.items():
            setattr(self, key, value)


def _resolve(*, client: str = "claude", role: str | None = None, **kw: Any) -> DispatchRequest:
    cfg = kw.pop("cfg", _Cfg())
    return resolve_dispatch_request(
        client=client,
        prompt="review this",
        role=role,
        model=kw.pop("model", None),
        effort=kw.pop("effort", None),
        cwd=Path("/tmp"),
        timeout_s=None,
        isolate=True,
        use_pty=False,
        dispatch_cfg=cfg,
    )


# ── Effort: every precedence pair ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("effort", "config", "role", "expected", "source"),
    [
        ("low", "high", "adversarial-audit", "low", "request"),  # request beats config and table
        ("low", None, "adversarial-audit", "low", "request"),  # request beats table
        (None, "low", "adversarial-audit", "low", "config"),  # config beats table
        (None, None, "adversarial-audit", "medium", "table"),  # security row
        (None, None, "code-review", "medium", "table"),  # review row
        (None, None, None, None, "none"),  # unclassified prompt: nothing invented
        (None, "low", None, "low", "config"),  # config applies without a role
    ],
)
def test_effort_precedence(
    effort: str | None, config: str | None, role: str | None, expected: str | None, source: str
) -> None:
    req = _resolve(role=role, effort=effort, cfg=_Cfg(dispatch_default_effort=config))
    assert (req.effort, req.effort_source) == (expected, source)


@pytest.mark.parametrize("where", ["request", "config"])
def test_an_invalid_effort_is_rejected_before_launch(where: str) -> None:
    kwargs: dict[str, Any] = (
        {"effort": "extreme"} if where == "request" else {"cfg": _Cfg(dispatch_default_effort="extreme")}
    )
    with pytest.raises(DispatchResolutionError) as exc:
        _resolve(role="code-review", **kwargs)
    assert exc.value.exit_code == 2
    assert "extreme" in str(exc.value)


# ── Model (tier): every precedence pair, and an adapter without a tier map ───


@pytest.mark.parametrize(
    ("client", "model", "config", "role", "expected", "source"),
    [
        ("claude", "haiku", {"claude": "sonnet"}, "code-review", "haiku", "request"),
        ("claude", None, {"claude": "sonnet"}, "code-review", "sonnet", "config"),
        ("claude", None, {}, "code-review", "opus", "table"),  # review -> frontier -> opus
        ("claude", None, {}, None, None, "none"),
        # codex has no verified tier -> model mapping: TRW passes nothing and says so
        ("codex", None, {}, "code-review", None, "unsupported"),
        ("codex", None, {"codex": "gpt-x"}, "code-review", "gpt-x", "config"),
    ],
)
def test_model_precedence(
    client: str, model: str | None, config: dict[str, str], role: str | None, expected: str | None, source: str
) -> None:
    req = _resolve(client=client, role=role, model=model, cfg=_Cfg(dispatch_default_models=config))
    assert (req.model, req.model_source) == (expected, source)


# ── Recorded: requested versus applied ────────────────────────────────────────


def test_the_result_records_requested_versus_applied_effort() -> None:
    from trw_mcp.dispatch._policy import policy_record

    claude = _resolve(role="adversarial-audit")
    codex = _resolve(client="codex", role="adversarial-audit")

    assert policy_record(claude)["effort"] == {"requested": "medium", "applied": "medium", "source": "table"}
    # codex documents no effort flag: the intent is recorded, and nothing was applied
    assert policy_record(codex)["effort"] == {"requested": "medium", "applied": None, "source": "table"}
    assert policy_record(codex)["model"] == {"requested": None, "applied": None, "source": "unsupported"}


# ── Public entry points: CLI and MCP ──────────────────────────────────────────


def _fake_result() -> DispatchResult:
    return DispatchResult(
        client="claude",
        argv_redacted=["claude", "<prompt>"],
        read_only_enforced=True,
        exit_code=0,
        timed_out=False,
        duration_s=0.1,
        text="ok",
        raw_stdout="ok",
        raw_stderr="",
        structured=None,
    )


def test_cli_effort_flag_reaches_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch import _cli
    from trw_mcp.server._cli_argparse import _build_arg_parser

    ns = _build_arg_parser().parse_args(
        ["dispatch", "--client", "claude", "--role", "adversarial-audit", "--effort", "low", "--prompt", "review this"]
    )
    captured: list[DispatchRequest] = []
    monkeypatch.setattr(_cli, "dispatch", lambda req: captured.append(req) or _fake_result())
    with pytest.raises(SystemExit):
        _cli.run_dispatch(ns)
    assert (captured[0].effort, captured[0].effort_source) == ("low", "request")


def test_cli_rejects_an_invalid_effort() -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    with pytest.raises(SystemExit):
        _build_arg_parser().parse_args(["dispatch", "--client", "claude", "--effort", "extreme", "--prompt", "x"])


class _RootCfg:
    def __init__(self, dispatch_cfg: _Cfg) -> None:
        self.dispatch = dispatch_cfg


def _mcp_dispatch() -> Any:
    from trw_mcp.tools.dispatch import register_dispatch_tools

    server = FastMCP("test")
    register_dispatch_tools(server)
    return extract_tool_fn(server, "trw_dispatch")


def test_mcp_effort_reaches_the_request_and_the_result_records_it(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[DispatchRequest] = []
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", lambda req: captured.append(req) or _fake_result())

    out = _mcp_dispatch()(
        prompt="review this", client="claude", role="code-review", effort="high", timeout_s=60, wait=True
    )

    assert (captured[0].effort, captured[0].effort_source) == ("high", "request")
    assert out["policy"]["effort"] == {"requested": "high", "applied": "high", "source": "request"}


def test_mcp_rejects_an_invalid_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _RootCfg(_Cfg()))
    out = _mcp_dispatch()(prompt="x", client="claude", effort="extreme", timeout_s=60, wait=True)
    assert out["exit_code"] == 2
    assert "extreme" in out["error"]


def test_argparse_namespace_without_effort_still_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers that build a Namespace by hand (older harnesses) get the table default."""
    from trw_mcp.dispatch import _cli

    ns = argparse.Namespace(
        client="claude",
        prompt="x",
        prompt_file=None,
        role="code-review",
        model=None,
        cwd=None,
        timeout=600,
        output_file=None,
        no_isolate=False,
        allow_writes=False,
        pty=False,
        json=False,
    )
    captured: list[DispatchRequest] = []
    monkeypatch.setattr(_cli, "dispatch", lambda req: captured.append(req) or _fake_result())
    with pytest.raises(SystemExit):
        _cli.run_dispatch(ns)
    assert (captured[0].effort, captured[0].effort_source) == ("medium", "table")


def test_mcp_dispatch_takes_effort_and_no_longer_takes_use_pty() -> None:
    """The lead's budget decision: effort joins the MCP surface, use_pty leaves it (CLI keeps --pty)."""
    import inspect

    params = inspect.signature(_mcp_dispatch()).parameters
    assert "effort" in params
    assert "use_pty" not in params
