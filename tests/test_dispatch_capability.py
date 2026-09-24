"""Dispatch refuses a client CLI that lacks a flag it would be passed, and types quota refusals.

Measured live 2026-09-23: an installed ``cursor-agent`` rejected ``--sandbox``;
the VS Code ``copilot`` shim with no Copilot CLI behind it answered an install
prompt with exit 0 (read as ``ok=True``); codex and grok reported exhausted
usage as an untyped stop. Stubs on PATH stand in for each installed binary.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trw_mcp.dispatch import dispatch
from trw_mcp.dispatch._normalize import classify_silence, normalize_output
from trw_mcp.dispatch._types import DispatchRequest


def _install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    help_text: str | bytes,
    answer: str,
    *,
    help_only_in: Path | None = None,
) -> Path:
    """Put a stub *name* on PATH: ``--help`` prints *help_text* verbatim; anything else records a run.

    With *help_only_in*, help is printed only when the stub runs in that directory.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    ran = tmp_path / f"{name}.ran"
    help_file = tmp_path / f"{name}.help"
    help_file.write_bytes(help_text if isinstance(help_text, bytes) else help_text.encode())
    where = f'[ "$PWD" = "{help_only_in}" ] && ' if help_only_in else ""
    stub = bin_dir / name
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'if [ "${{@: -1}}" = --help ]; then {where}cat {help_file}; exit 0; fi\n'
        f'echo "$@" > {ran}\n'
        f"printf %s {json.dumps(answer)}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return ran


_CURSOR_ANSWER = json.dumps({"type": "result", "subtype": "success", "result": "no findings"})


def test_cursor_without_sandbox_flag_refuses_read_only_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = _install(tmp_path, monkeypatch, "cursor-agent", "  -p, --print\n  --output-format <format>\n", _CURSOR_ANSWER)

    result = dispatch(DispatchRequest(client="cursor-cli", prompt="review", read_only=True))

    assert result.ok is False
    assert result.silence_reason == "sandbox_unsupported"
    assert "--sandbox" in result.raw_stderr and "upgrade" in result.raw_stderr
    assert not ran.exists(), "the client must never run without its sandbox"


def test_cursor_advertising_sandbox_flag_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    help_text = "  -p, --print\n  --output-format <format>\n  --sandbox <mode>\n"
    ran = _install(tmp_path, monkeypatch, "cursor-agent", help_text, _CURSOR_ANSWER)

    result = dispatch(DispatchRequest(client="cursor-cli", prompt="review", read_only=True))

    assert result.silence_reason is None, result.raw_stderr
    assert "--sandbox enabled" in ran.read_text()


def test_ansi_styled_help_is_read_as_plain_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    help_text = (
        "\x1b[1m-p\x1b[0m, \x1b[1m--print\x1b[0m\n\x1b[1m--output-format\x1b[0m\n\x1b[1m--sandbox\x1b[0m <mode>\n"
    )
    ran = _install(tmp_path, monkeypatch, "cursor-agent", help_text, _CURSOR_ANSWER)

    result = dispatch(DispatchRequest(client="cursor-cli", prompt="review", read_only=True))

    assert result.silence_reason is None, result.raw_stderr
    assert ran.exists()


def test_non_utf8_help_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    help_bytes = b"\xff\xfe caf\xe9\n  -p, --print\n  --output-format <format>\n"
    ran = _install(tmp_path, monkeypatch, "cursor-agent", help_bytes, _CURSOR_ANSWER)

    result = dispatch(DispatchRequest(client="cursor-cli", prompt="review", read_only=True))

    assert result.silence_reason == "sandbox_unsupported"
    assert not ran.exists()


def test_sandbox_refusal_names_only_sandbox_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, monkeypatch, "cursor-agent", "  -p, --print\n", _CURSOR_ANSWER)

    result = dispatch(DispatchRequest(client="cursor-cli", prompt="review", read_only=True))

    assert result.silence_reason == "sandbox_unsupported"
    refusal, rest = result.raw_stderr.split("\n", 1)
    assert "--sandbox" in refusal and "--output-format" not in refusal
    assert rest.startswith("also client_unsupported:") and "--output-format" in rest


def test_help_probe_runs_in_the_request_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work = (tmp_path / "work").resolve()
    work.mkdir()
    ran = _install(
        tmp_path, monkeypatch, "cursor-agent", "  -p\n  --output-format\n", _CURSOR_ANSWER, help_only_in=work
    )

    result = dispatch(DispatchRequest(client="cursor-cli", prompt="review", read_only=True, cwd=work))

    assert result.silence_reason == "sandbox_unsupported"  # help was read, so --sandbox was judged
    assert not ran.exists()


def test_opencode_write_dispatch_uses_documented_auto_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    help_text = (
        "  -m, --model\n  --format\n  --dir\n  --auto  auto-approve permissions that are not explicitly denied\n"
    )
    answer = json.dumps({"type": "text", "part": {"text": "done"}})
    ran = _install(tmp_path, monkeypatch, "opencode", help_text, answer)

    writes = dispatch(DispatchRequest(client="opencode", prompt="edit", read_only=False))
    assert writes.silence_reason != "client_unsupported", writes.raw_stderr
    assert "--auto" in ran.read_text().split()

    dispatch(DispatchRequest(client="opencode", prompt="review", read_only=True))
    assert "--auto" not in ran.read_text().split()


def test_copilot_shim_without_cli_is_not_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shim = "Cannot find GitHub Copilot CLI (https://gh.io/copilot-cli)\nInstall GitHub Copilot CLI? ['y/N']"
    _install(tmp_path, monkeypatch, "copilot", shim, shim)

    result = dispatch(DispatchRequest(client="copilot", prompt="review", read_only=True))

    assert result.ok is False
    assert result.silence_reason == "client_unsupported"
    assert "install or upgrade the copilot CLI" in result.raw_stderr


def test_codex_usage_limit_is_quota_exhausted() -> None:
    event = {"type": "turn.failed", "error": {"message": "You've hit your usage limit. Upgrade to Pro."}}
    text, structured = normalize_output("codex", json.dumps(event))

    reason = classify_silence(text=text, raw_stderr="", structured=structured, exit_code=1, timed_out=False)

    assert reason == "quota_exhausted"
    assert "usage limit" in json.dumps(structured)  # the provider message stays with the result


@pytest.mark.parametrize("channel", ["text", "raw_stderr"])
def test_grok_payment_required_is_quota_exhausted(channel: str) -> None:
    message = "402 Payment Required: Grok Build usage balance exhausted"
    streams = {"text": "", "raw_stderr": "", channel: message}

    reason = classify_silence(**streams, structured=None, exit_code=1, timed_out=False)

    assert reason == "quota_exhausted"


def test_answer_that_mentions_usage_limit_is_not_quota() -> None:
    reason = classify_silence(
        text="The usage limit check in billing.py is off by one.",
        raw_stderr="prompt: review the usage limit code",
        structured=None,
        exit_code=0,
        timed_out=False,
    )

    assert reason is None
