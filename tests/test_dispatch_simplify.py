"""DISPATCH-SIMPLIFY (operator directive 2026-09-26): the generic path is the default.

A role is an optional prompt preset: it never implies a posture and never refuses. A posture is an explicit,
best-effort opt-in: a client that cannot carry it still runs, and the request/result say what it did get,
unless the caller requires it. A list of prompts fans out one lane per variant.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest
from fastmcp import FastMCP

from tests.conftest import extract_tool_fn
from trw_mcp.dispatch._resolve import DispatchResolutionError, resolve_dispatch_request
from trw_mcp.dispatch._runner_results import _early_result
from trw_mcp.dispatch._targets import Target, compact_result, variant_lanes
from trw_mcp.dispatch._types import DispatchRequest, DispatchResult
from trw_mcp.tools.dispatch import _result_payload_capped, register_dispatch_tools


class _Cfg:
    dispatch_enabled_clients = ["codex", "claude", "agy", "grok"]
    dispatch_default_client = "codex"
    dispatch_default_models: dict[str, str] = {}
    dispatch_default_timeout_s = 600
    dispatch_default_read_only = True


class _Root:
    dispatch = _Cfg()


def _resolve(**overrides: Any) -> DispatchRequest:
    kwargs: dict[str, Any] = {
        "client": "grok",
        "prompt": "review this",
        "role": None,
        "model": None,
        "cwd": None,
        "timeout_s": None,
        "isolate": True,
        "use_pty": False,
        "dispatch_cfg": _Cfg(),
    }
    return resolve_dispatch_request(**{**kwargs, **overrides})


def _result(client: str, text: str, note: str = "") -> DispatchResult:
    return DispatchResult(
        client=client,  # type: ignore[arg-type]
        argv_redacted=[client],
        read_only_enforced=True,
        exit_code=0,
        timed_out=False,
        duration_s=0.1,
        text=text,
        raw_stdout=text,
        raw_stderr="",
        posture_note=note,
    )


# --- roles are presets ------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["plan", "code-review", "adversarial-audit"])
def test_a_role_never_implies_a_posture_or_refuses_a_client_without_one(role: str) -> None:
    req = _resolve(role=role)  # grok has no reviewer posture: this used to be refused through the CLI
    assert req.posture == "default" and req.posture_note == ""
    assert req.prompt.endswith("review this") and req.prompt != "review this", "the preset preamble still applies"


def test_the_cli_takes_the_posture_only_from_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch import _cli

    seen: dict[str, Any] = {}

    def capture(**kwargs: Any) -> DispatchRequest:
        seen.update(kwargs)
        raise DispatchResolutionError("stop here", exit_code=2)

    monkeypatch.setattr(_cli, "resolve_dispatch_request", capture)
    monkeypatch.setattr(_cli, "get_config", lambda: _Root())
    args = argparse.Namespace(prompt=["x"], prompt_file=None, client="grok", model=None, role="plan", cwd=None)
    with pytest.raises(SystemExit):
        _cli.run_dispatch(args)
    assert seen["posture"] == "default" and seen["require_posture"] is False


# --- posture is best effort -------------------------------------------------------------------------------


def test_an_uncarried_posture_runs_at_default_and_names_what_the_client_does_get() -> None:
    req = _resolve(posture="reviewer")

    assert req.posture == "default"
    assert "posture 'reviewer' not enforced" in req.posture_note and "Delivered:" in req.posture_note
    assert "TRW surface" in req.posture_note, "the caller learns whether TRW's tools are bounded"
    assert "Dispatch it with" not in req.posture_note, "a refusal's advice is wrong for a run that went ahead"


def test_require_posture_restores_the_refusal() -> None:
    with pytest.raises(DispatchResolutionError) as refused:
        _resolve(posture="reviewer", require_posture=True)
    assert refused.value.exit_code == 2


def test_a_reviewer_with_writes_is_still_a_contradiction_and_refused() -> None:
    with pytest.raises(DispatchResolutionError):
        _resolve(client="codex", posture="reviewer", read_only=False)


def test_a_client_that_carries_the_posture_gets_it_with_no_note() -> None:
    req = _resolve(client="codex", posture="reviewer")
    assert (req.posture, req.posture_note) == ("reviewer", "")


def test_the_note_reaches_the_result_and_every_fanout_lane() -> None:
    req = _resolve(posture="reviewer")
    early = _early_result(req, [], exit_code=-1, stderr="x")
    assert early.posture_note == req.posture_note and early.posture_enforced is False
    lane = compact_result("grok", {"ok": True, "text": "t", "posture_note": req.posture_note})
    assert lane["posture_enforced"] is False and lane["posture_note"] == req.posture_note


def test_an_empty_note_costs_no_response_tokens() -> None:
    assert "posture_note" not in _result_payload_capped(_result("codex", "ok"))
    assert _result_payload_capped(_result("codex", "ok", note="n"))["posture_note"] == "n"


# --- fan-out of prompt variants ----------------------------------------------------------------------------


def test_variant_lanes_cross_clients_with_prompts() -> None:
    lanes = variant_lanes([Target("codex", None), Target("agy", None)], None, 2)
    assert [lane.label for lane in lanes] == ["codex#1", "codex#2", "agy#1", "agy#2"]
    assert variant_lanes(None, None, 1) == [Target("", None)]


def test_a_list_prompt_fans_out_one_lane_per_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    seen: list[tuple[str, str]] = []

    def fake(req: DispatchRequest) -> DispatchResult:
        seen.append((req.client, req.prompt))
        return _result(req.client, f"answer to {req.prompt}")

    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", fake)
    server = FastMCP("t")
    register_dispatch_tools(server)

    out = extract_tool_fn(server, "trw_dispatch")(prompt=["variant A", "variant B"], client="codex", wait=True)

    assert out["status"] == "2/2 succeeded"
    assert [r["target"] for r in out["results"]] == ["codex#1", "codex#2"]
    assert sorted(p for _, p in seen) == ["variant A", "variant B"]


@pytest.mark.parametrize(("posture", "refused"), [("reviewer", False), ("reviewer!", True)])
def test_the_tool_runs_best_effort_unless_the_posture_is_required(
    monkeypatch: pytest.MonkeyPatch, posture: str, refused: bool
) -> None:
    monkeypatch.setattr("trw_mcp.tools.dispatch.get_config", lambda: _Root())
    monkeypatch.setattr("trw_mcp.tools.dispatch.dispatch", lambda req: _result(req.client, "ok", note=req.posture_note))
    server = FastMCP("t")
    register_dispatch_tools(server)

    out = extract_tool_fn(server, "trw_dispatch")(prompt="p", client="grok", posture=posture, wait=True)

    if refused:
        assert out["exit_code"] == 2
    else:
        assert "not enforced" in str(out["result"]["posture_note"])
