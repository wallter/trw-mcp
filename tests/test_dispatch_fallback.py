"""Cross-vendor review fails over on an exhausted quota instead of stalling (7.0.0 W21).

Measured 2026-09-23 (L-rW01): codex, grok and agy quotas all ran out during the
6.0.0 release; a wrapper read codex's usage-limit refusal as "still running" and
same-vendor stand-in reviews missed P0s (L-qJmp). Dispatch now names the refusal
``quota_exhausted`` and walks an operator-listed client chain, recording every
attempt and which client produced the verdict.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.dispatch import DispatchRequest, DispatchResult
from trw_mcp.dispatch._cli import run_dispatch
from trw_mcp.dispatch._fallback import dispatch_with_fallback, host_dispatch_client
from trw_mcp.dispatch._normalize import classify_silence
from trw_mcp.dispatch._resolve import DispatchResolutionError

# --- detection ---------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "You've hit your usage limit. Upgrade to Pro or try again at Sep 25th.",  # codex, L-rW01
        "402 Payment Required: Grok Build usage balance exhausted",  # grok, efad8c720
        "HTTP 429 Too Many Requests",
        "Error: rate_limit_exceeded",
        "429 RESOURCE_EXHAUSTED: Quota exceeded for quota metric 'Generate Content API requests'",  # agy/gemini
        "API Error: Rate limit reached for requests",
    ],
)
def test_provider_refusal_on_a_failed_run_is_quota_exhausted(message: str) -> None:
    assert classify_silence(text="", raw_stderr=message, structured=None, exit_code=1, timed_out=False) == (
        "quota_exhausted"
    )


def test_a_review_that_discusses_rate_limits_is_still_a_clean_answer() -> None:
    reason = classify_silence(
        text="VERDICT: pass. The 429 Too Many Requests retry in client.py honours the rate limit reached header.",
        raw_stderr="prompt: review the rate_limit_exceeded handling",
        structured=None,
        exit_code=0,
        timed_out=False,
    )
    assert reason is None


_ECHOED_PROMPT = "Review the retry path: what happens when the provider says usage limit or rate limit exceeded?"


def test_a_short_prompt_does_not_cut_a_quota_marker_apart() -> None:
    reason = classify_silence(
        text="", raw_stderr="x\nError: quota exceeded", structured=None, exit_code=1, timed_out=False, prompt="x"
    )
    assert reason == "quota_exhausted"


@pytest.mark.parametrize(
    ("raw_stderr", "expected"),
    [
        # codex echoes the prompt to stderr; the real failure is the 401 beside it.
        (f"user\n{_ECHOED_PROMPT}\nERROR: 401 Unauthorized: token expired", "auth_or_content_stop"),
        # The prompt echo alone must not read as a provider refusal.
        (f"user\n{_ECHOED_PROMPT}\nERROR: stream disconnected", "nonzero_exit"),
        # A genuine quota line outside the echoed prompt is still a quota refusal.
        (f"user\n{_ECHOED_PROMPT}\nERROR: You've hit your usage limit.", "quota_exhausted"),
    ],
)
def test_an_echoed_prompt_never_decides_the_silence_reason(raw_stderr: str, expected: str) -> None:
    reason = classify_silence(
        text="", raw_stderr=raw_stderr, structured=None, exit_code=1, timed_out=False, prompt=_ECHOED_PROMPT
    )
    assert reason == expected


# --- the attempt loop --------------------------------------------------------


def _result(client: str, *, reason: str | None = None, exit_code: int | None = 0, text: str = "") -> DispatchResult:
    return DispatchResult(
        client=client,
        argv_redacted=[client],
        read_only_enforced=True,
        exit_code=exit_code,
        timed_out=reason == "timed_out",
        duration_s=0.1,
        text=text if reason is None else "",
        raw_stdout="",
        raw_stderr="",
        silence_reason=reason,
    )


_QUOTA = _result("codex", reason="quota_exhausted", exit_code=1)


def _runner(outcomes: dict[str, DispatchResult]) -> tuple[Callable[[DispatchRequest], DispatchResult], list[str]]:
    calls: list[str] = []

    def run(req: DispatchRequest) -> DispatchResult:
        calls.append(req.client)
        return outcomes[req.client].model_copy(update={"client": req.client})

    return run, calls


def _build(client: str) -> DispatchRequest:
    return DispatchRequest(client=client, prompt="review", read_only=True)  # type: ignore[arg-type]


def test_quota_on_first_client_moves_to_the_next() -> None:
    run, calls = _runner({"codex": _QUOTA, "grok": _result("grok", text="VERDICT: pass")})

    result = dispatch_with_fallback(_build("codex"), ["grok", "agy"], _build, run=run, host_client="claude")

    assert calls == ["codex", "grok"]
    assert result.ok and result.client == "grok" and result.text == "VERDICT: pass"
    assert [(a.client, a.reason) for a in result.attempts] == [("codex", "quota_exhausted"), ("grok", None)]
    assert "grok" in result.fallback_note and "codex" in result.fallback_note


def test_every_client_exhausted_is_reported_with_each_reason() -> None:
    launch_failed = _result("agy", reason="nonzero_exit", exit_code=-127)
    run, calls = _runner({"codex": _QUOTA, "grok": _QUOTA, "agy": launch_failed})

    result = dispatch_with_fallback(_build("codex"), ["grok", "agy"], _build, run=run, host_client="claude")

    assert calls == ["codex", "grok", "agy"]
    assert result.ok is False
    assert [(a.client, a.reason) for a in result.attempts] == [
        ("codex", "quota_exhausted"),
        ("grok", "quota_exhausted"),
        ("agy", "launch_failed"),
    ]
    assert result.fallback_note.startswith("no verdict: all 3 clients failed over")
    for client in ("codex", "grok", "agy"):
        assert client in result.fallback_note


def test_a_verdict_on_the_first_client_never_falls_back() -> None:
    run, calls = _runner({"codex": _result("codex", text="VERDICT: block")})

    result = dispatch_with_fallback(_build("codex"), ["grok", "agy"], _build, run=run, host_client="claude")

    assert calls == ["codex"]
    assert result.client == "codex" and result.ok
    assert [(a.client, a.reason) for a in result.attempts] == [("codex", None)]
    assert result.fallback_note == ""


@pytest.mark.parametrize("reason", ["timed_out", "nonzero_exit", "auth_or_content_stop", "empty_output"])
def test_a_failure_that_is_not_a_quota_or_launch_failure_stops_the_chain(reason: str) -> None:
    run, calls = _runner({"codex": _result("codex", reason=reason, exit_code=1), "grok": _result("grok", text="x")})

    result = dispatch_with_fallback(_build("codex"), ["grok"], _build, run=run, host_client="claude")

    assert calls == ["codex"], "a real failure is reported, never papered over by another client's answer"
    assert result.silence_reason == reason


def test_no_fallback_list_returns_the_single_result_untouched() -> None:
    run, calls = _runner({"codex": _QUOTA})

    result = dispatch_with_fallback(_build("codex"), [], _build, run=run, host_client="claude")

    assert calls == ["codex"]
    assert result.silence_reason == "quota_exhausted"
    assert result.attempts == [] and result.fallback_note == ""


def test_an_unresolvable_fallback_is_recorded_and_skipped() -> None:
    run, calls = _runner({"codex": _QUOTA, "agy": _result("agy", text="VERDICT: pass")})

    def build(client: str) -> DispatchRequest:
        if client == "grok":
            raise DispatchResolutionError("client 'grok' is disabled")
        return _build(client)

    result = dispatch_with_fallback(_build("codex"), ["codex", "grok", "agy"], build, run=run, host_client=None)

    assert calls == ["codex", "agy"], "the primary is never retried and a disabled client never runs"
    assert [(a.client, a.reason) for a in result.attempts] == [
        ("codex", "quota_exhausted"),
        ("grok", "unresolved"),
        ("agy", None),
    ]


def _reviewer(client: str) -> DispatchRequest:
    return DispatchRequest(client=client, prompt="review", read_only=True, posture="reviewer")  # type: ignore[arg-type]


@pytest.mark.parametrize("posture", ["reviewer", "isolated-review"])
def test_a_client_that_cannot_run_the_posture_is_skipped_never_launched(posture: str) -> None:
    """V10: grok and agy have no reviewer argv; a bounded review must never fall back to an unbounded child."""
    run, calls = _runner({"codex": _QUOTA, "claude": _result("claude", text="VERDICT: pass")})
    built: list[str] = []

    def build(client: str) -> DispatchRequest:
        built.append(client)  # a build that does not itself verify the posture
        return DispatchRequest(client=client, prompt="review", read_only=True, posture=posture)  # type: ignore[arg-type]

    first = DispatchRequest(client="codex", prompt="review", read_only=True, posture=posture)  # type: ignore[arg-type]
    result = dispatch_with_fallback(first, ["grok", "agy", "claude"], build, run=run, host_client=None)

    assert "grok" not in calls and "agy" not in calls and "grok" not in built and "agy" not in built
    assert [(a.client, a.reason) for a in result.attempts][:3] == [
        ("codex", "quota_exhausted"),
        ("grok", "posture_unsupported"),
        ("agy", "posture_unsupported"),
    ]
    if posture == "reviewer":
        assert calls == ["codex", "claude"] and result.client == "claude" and result.ok
    else:  # no client has an isolated-review lane on this host's registry
        assert calls == ["codex"] and result.ok is False
        assert result.fallback_note.startswith("no verdict")


def test_every_fallback_unable_to_run_the_posture_ends_with_no_verdict() -> None:
    run, calls = _runner({"codex": _QUOTA})

    result = dispatch_with_fallback(_reviewer("codex"), ["grok", "agy"], _reviewer, run=run, host_client="claude")

    assert calls == ["codex"]
    assert result.ok is False and result.client == "codex" and result.silence_reason == "quota_exhausted"
    assert result.fallback_note.startswith("no verdict: all 3 clients failed over")
    assert "grok=posture_unsupported" in result.fallback_note


def test_the_default_posture_still_falls_back_to_any_listed_client() -> None:
    run, calls = _runner({"codex": _QUOTA, "grok": _result("grok", text="VERDICT: pass")})

    result = dispatch_with_fallback(_build("codex"), ["grok"], _build, run=run, host_client="claude")

    assert calls == ["codex", "grok"] and result.client == "grok"


def test_a_verdict_from_the_host_client_says_so() -> None:
    run, _ = _runner({"codex": _QUOTA, "claude": _result("claude", text="VERDICT: pass")})

    result = dispatch_with_fallback(_build("codex"), ["claude"], _build, run=run, host_client="claude")

    assert result.client == "claude"
    assert "same client as the host" in result.fallback_note


def test_host_dispatch_client_maps_the_detected_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.dispatch._fallback.detect_client_profile", lambda: "claude-code")
    assert host_dispatch_client() == "claude"
    monkeypatch.setattr("trw_mcp.dispatch._fallback.detect_client_profile", lambda: "")
    assert host_dispatch_client() is None


@pytest.mark.parametrize("chained", [False, True])
def test_mcp_payload_carries_fallback_fields_only_when_a_chain_ran(chained: bool) -> None:
    from trw_mcp.tools.dispatch import _result_payload_capped

    run, _ = _runner({"codex": _QUOTA, "grok": _result("grok", text="VERDICT: pass")})
    result = dispatch_with_fallback(_build("codex"), ["grok"] if chained else [], _build, run=run)

    payload = _result_payload_capped(result)

    assert ("attempts" in payload, "fallback_note" in payload) == (chained, chained)
    if chained:
        assert payload["attempts"] == [
            {"client": "codex", "reason": "quota_exhausted"},
            {"client": "grok", "reason": None},
        ]


# --- the real CLI path, with stub clients on PATH ---------------------------


def _stub(bin_dir: Path, name: str, stdout: str, exit_code: int) -> None:
    """A client binary whose ``--help`` is empty (no capability verdict) and whose run prints *stdout*."""
    script = bin_dir / name
    script.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "${@: -1}" = --help ]; then exit 0; fi\n'
        f"printf '%s\\n' {json.dumps(stdout)}\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


class _Cfg:
    def __init__(self, **overrides: Any) -> None:
        self.dispatch_enabled_clients = ["codex", "grok", "agy", "claude"]
        self.dispatch_default_client: str | None = "codex"
        self.dispatch_default_models: dict[str, str] = {}
        self.dispatch_default_timeout_s = 60
        self.dispatch_default_read_only = True
        self.dispatch_role_client: dict[str, str] = {}
        self.dispatch_fallback_clients: list[str] = []
        self.__dict__.update(overrides)


def _run_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cfg: _Cfg, **args: object) -> tuple[int, dict[str, Any]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    usage_limit = {"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again at Sep 25th."}}
    _stub(bin_dir, "codex", json.dumps(usage_limit), 1)
    _stub(bin_dir, "grok", json.dumps({"text": "VERDICT: pass", "stopReason": "end_turn"}), 0)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr("trw_mcp.dispatch._cli.get_config", lambda: argparse.Namespace(dispatch=cfg))
    out = tmp_path / "result.json"
    ns = {"client": "codex", "prompt": "review", "cwd": str(tmp_path), "output_file": str(out), "role": None}
    ns.update(args)
    with pytest.raises(SystemExit) as exc:
        run_dispatch(argparse.Namespace(**ns))
    return int(exc.value.code or 0), json.loads(out.read_text(encoding="utf-8"))


def test_cli_fallback_flag_routes_a_usage_limited_codex_review_to_grok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, payload = _run_cli(monkeypatch, tmp_path, _Cfg(), fallback_clients="grok")

    assert code == 0
    assert payload["client"] == "grok" and payload["text"] == "VERDICT: pass"
    assert [(a["client"], a["reason"]) for a in payload["attempts"]] == [("codex", "quota_exhausted"), ("grok", None)]


def test_cli_uses_the_config_chain_and_an_empty_flag_disables_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, payload = _run_cli(monkeypatch, tmp_path / "a", _Cfg(dispatch_fallback_clients=["grok"]))
    assert (code, payload["client"]) == (0, "grok")

    code, payload = _run_cli(monkeypatch, tmp_path / "b", _Cfg(dispatch_fallback_clients=["grok"]), fallback_clients="")
    assert code == 1
    assert payload["client"] == "codex" and payload["silence_reason"] == "quota_exhausted"
    assert payload["attempts"] == []
