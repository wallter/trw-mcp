"""W33 — the ``dispatch`` CLI gets a ``--posture`` flag that reaches the same
posture machinery :func:`trw_dispatch` uses, and a review/audit ``--role``
fail-closed derives ``posture="reviewer"`` when the caller omits the flag.

Two levels of evidence:

1. Behavioral (``run_dispatch``): the resolved :class:`DispatchRequest` the
   stubbed runner receives carries the expected ``posture``, and an explicit
   ``--posture default`` override on a review role prints a warning.
2. Gate-level (``build_command``/``build_subprocess_env``, real code, no
   process spawned): a reviewer-posture request renders argv/env carrying
   ``TRW_SURFACE_ROLE=reviewer`` and no write-enabling flag, proving the CLI
   path cannot hand a review role a write tool.

3. End to end (``run_dispatch`` -> the real runner -> a FAKE ``claude`` on
   PATH): the fake reads the ``--mcp-config`` dispatch gave it, starts that
   trw-mcp server with the environment dispatch gave it, and calls
   ``trw_learn``; the server's denial is what comes back. No vendor CLI runs,
   so this proves the argv/env chain and the server bound, not a vendor's
   handling of ``--mcp-config``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._daemon_reaper import reap_daemons_under
from tests._stdio_benchmark_support import build_temp_project
from tests._stdio_harness import stdio_import_skip_reason
from trw_mcp.dispatch import DispatchResult
from trw_mcp.dispatch._cli import run_dispatch
from trw_mcp.dispatch._commands import build_command
from trw_mcp.dispatch._env import build_subprocess_env
from trw_mcp.dispatch._resolve import resolve_dispatch_request
from trw_mcp.dispatch._roles import ROLE_TABLE


class _StubDispatchConfig:
    def __init__(self, **overrides: Any) -> None:
        self.dispatch_enabled_clients: list[str] = ["codex", "claude", "agy"]
        self.dispatch_default_client: str | None = "codex"
        self.dispatch_default_models: dict[str, str] = {}
        self.dispatch_default_timeout_s: int = 600
        self.dispatch_default_read_only: bool = True
        self.dispatch_role_client: dict[str, str] = {}
        self.dispatch_child_trw_access: bool = False
        for key, value in overrides.items():
            setattr(self, key, value)


class _StubConfig:
    def __init__(self, dispatch_cfg: _StubDispatchConfig) -> None:
        self.dispatch = dispatch_cfg


def _ns(**kw: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "client": "codex",
        "prompt": "review this",
        "prompt_file": None,
        "role": None,
        "posture": None,
        "model": None,
        "cwd": None,
        "timeout": 600,
        "output_file": None,
        "no_isolate": False,
        "allow_writes": False,
        "pty": False,
        "json": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _install(monkeypatch: pytest.MonkeyPatch, dispatch_cfg: _StubDispatchConfig | None = None) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    cfg = dispatch_cfg or _StubDispatchConfig()
    monkeypatch.setattr("trw_mcp.dispatch._cli.get_config", lambda: _StubConfig(cfg))

    def _capture(req: object) -> DispatchResult:
        captured["req"] = req
        return DispatchResult(
            client=getattr(req, "client"),
            argv_redacted=["x"],
            read_only_enforced=getattr(req, "read_only"),
            exit_code=0,
            timed_out=False,
            duration_s=0.1,
            text="ok",
            raw_stdout="ok",
            raw_stderr="",
            structured=None,
        )

    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", _capture)
    return captured


# --- 1. Behavioral: posture derivation from --role, override with a warning ---


@pytest.mark.parametrize("role", sorted(ROLE_TABLE))
def test_review_role_without_explicit_posture_derives_reviewer(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    """Every ROLE_TABLE entry today is a review/audit role: omitting --posture
    must never leave one of them running unbounded (fail-closed)."""
    captured = _install(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(role=role))
    assert exc.value.code == 0
    assert getattr(captured["req"], "posture") == "reviewer"


def test_no_role_without_explicit_posture_stays_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install(monkeypatch)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(role=None))
    assert getattr(captured["req"], "posture") == "default"


def test_explicit_posture_reviewer_with_no_role_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install(monkeypatch)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(role=None, posture="reviewer"))
    assert getattr(captured["req"], "posture") == "reviewer"


def test_explicit_posture_default_overrides_review_role_with_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured = _install(monkeypatch)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(role="adversarial-audit", posture="default"))
    assert getattr(captured["req"], "posture") == "default"
    err = capsys.readouterr().err
    assert "UNBOUNDED" in err
    assert "adversarial-audit" in err


def test_explicit_posture_reviewer_on_review_role_prints_no_override_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured = _install(monkeypatch)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(role="code-review", posture="reviewer"))
    assert getattr(captured["req"], "posture") == "reviewer"
    assert "UNBOUNDED" not in capsys.readouterr().err


def test_posture_flag_parses_from_argv() -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    parser = _build_arg_parser()
    args = parser.parse_args(["dispatch", "--prompt", "x", "--posture", "reviewer"])
    assert args.posture == "reviewer"


def test_posture_flag_rejects_unknown_value() -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    parser = _build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["dispatch", "--prompt", "x", "--posture", "bogus"])


# --- 2. Gate: the derived reviewer posture actually bounds the child's argv/env ---


class _CmdCfg:
    dispatch_default_client = "codex"
    dispatch_role_client: dict[str, str] = {}
    dispatch_enabled_clients = ["codex"]
    dispatch_default_models: dict[str, str] = {}
    dispatch_default_timeout_s = 60
    dispatch_default_read_only = True
    dispatch_child_trw_access = False


def test_cli_derived_reviewer_posture_renders_trw_surface_role_env() -> None:
    """The request a review role derives resolves to argv/env carrying
    TRW_SURFACE_ROLE=reviewer -- the server-side control -- not merely a
    posture label on the request object."""
    req = resolve_dispatch_request(
        client="codex",
        prompt="audit this",
        role="adversarial-audit",
        model=None,
        cwd=None,
        timeout_s=None,
        isolate=True,
        use_pty=False,
        posture="reviewer",
        dispatch_cfg=_CmdCfg(),
    )
    assert req.posture == "reviewer"
    argv = build_command(req)
    env = build_subprocess_env(
        "codex",
        source_env=dict(os.environ),
        posture=req.posture,
        with_trw=req.with_trw,
    )
    assert env.get("TRW_SURFACE_ROLE") == "reviewer"
    # No write-enabling flag anywhere on the rendered argv.
    joined = " ".join(argv)
    for token in ("workspace-write", "--dangerously-bypass-approvals-and-sandbox", "--full-auto"):
        assert token not in joined


def test_cli_default_posture_does_not_mark_reviewer_env() -> None:
    req = resolve_dispatch_request(
        client="codex",
        prompt="audit this",
        role=None,
        model=None,
        cwd=None,
        timeout_s=None,
        isolate=True,
        use_pty=False,
        posture="default",
        dispatch_cfg=_CmdCfg(),
    )
    assert req.posture == "default"
    env = build_subprocess_env(
        "codex",
        source_env=dict(os.environ),
        posture=req.posture,
        with_trw=req.with_trw,
    )
    assert "TRW_SURFACE_ROLE" not in env


# --- 3. End to end: a fake client CLI calls a write tool on the server dispatch gave it ---

# Plays ``claude -p ... --mcp-config <json>``: starts the ``trw`` server from that
# JSON under the environment dispatch launched it with, calls trw_learn over
# stdio, and answers in claude's single-JSON-object shape.
_FAKE_CLAUDE = """#!{python}
import json, os, subprocess, sys

argv = sys.argv[1:]
if argv[-1:] == ["--help"]:
    sys.exit(0)
entry = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]["trw"]
server = subprocess.Popen(
    [entry["command"], *entry["args"]],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    env={{**os.environ, **entry.get("env", {{}})}},
    text=True,
)

def send(message):
    server.stdin.write(json.dumps(message) + "\\n")
    server.stdin.flush()

def call(request_id, method, params):
    send({{"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}})
    for line in server.stdout:
        reply = json.loads(line)
        if reply.get("id") == request_id:
            return reply
    raise SystemExit("server closed stdout")

call(1, "initialize", {{"protocolVersion": "2025-06-18", "capabilities": {{}}, "clientInfo": {{"name": "fake-claude", "version": "0"}}}})
send({{"jsonrpc": "2.0", "method": "notifications/initialized"}})
reply = call(2, "tools/call", {{"name": "trw_learn", "arguments": {{"summary": "reviewer write probe", "detail": "must be denied"}}}})
server.stdin.close()  # EOF is a clean shutdown; the daemon it may have started is then already placed
try:
    server.wait(timeout=30)
except subprocess.TimeoutExpired:
    server.kill()
result = reply.get("result") or reply
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                  "result": json.dumps(result.get("structuredContent") or result)}}))
"""


@pytest.mark.slow
def test_cli_reviewer_dispatch_child_is_denied_a_write_tool(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    reason = stdio_import_skip_reason()
    if reason is not None:  # pragma: no cover - environment guard
        pytest.skip(reason)
    project, user_dir = build_temp_project(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text(_FAKE_CLAUDE.format(python=sys.executable), encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("HOME", str(user_dir))  # HOME is on the child allowlist; keep the real one out
    cfg = _StubDispatchConfig(dispatch_enabled_clients=["claude"], dispatch_default_client="claude")
    monkeypatch.setattr("trw_mcp.dispatch._cli.get_config", lambda: _StubConfig(cfg))
    learnings = project / ".trw" / "learnings"
    before = sorted(p.name for p in learnings.rglob("*"))
    out_file = tmp_path / "result.json"

    try:
        with pytest.raises(SystemExit):
            run_dispatch(
                _ns(
                    client="claude",
                    role="adversarial-audit",
                    cwd=str(project),
                    timeout=120,
                    output_file=str(out_file),
                    fallback_clients="",
                )
            )
    finally:
        # The child's server starts a memory daemon under the temp HOME; it outlives the fake CLI.
        reap_daemons_under(tmp_path, wait=True, by_process=True)

    result = json.loads(out_file.read_text(encoding="utf-8"))
    assert result["posture_enforced"] is True, result
    denial = json.loads(result["text"])
    assert denial["error_type"] == "tool_not_in_reviewer_surface", result
    assert denial["tool_name"] == "trw_learn"
    assert sorted(p.name for p in learnings.rglob("*")) == before
