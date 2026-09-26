"""W32 — agy and grok stay ``posture_unsupported`` for the reviewer channel, on
purpose, because neither CLI exposes an argv/env channel TRW can render a
per-dispatch trw-mcp server into (OD-6: "argv, not a sentence").

agy's only MCP config surface is a GLOBAL, cross-project
``~/.gemini/config/mcp_config.json`` with no per-invocation override
(TRW's antigravity-cli provider notes §3). grok's
per-invocation ``GROK_CONFIG`` overlay drops the ``mcp_servers`` table entirely
before the child reads it (OQ-1, closed 2026-09-19;
TRW's grok provider notes, FUTURE-WORK §3). Filling
``reviewer_argv_template`` for either from an unverified guess would be worse
than leaving it empty: a caller who asked for a bounded reviewer would receive
a silently unbounded child. This file asserts the refusal holds at both the
registry level and the CLI/MCP resolution level so a future edit cannot
reintroduce a fake template without failing a test.
"""

from __future__ import annotations

import pytest

from trw_mcp.dispatch._client_specs import client_spec_for
from trw_mcp.dispatch._env import build_subprocess_env
from trw_mcp.dispatch._resolve import DispatchResolutionError, resolve_dispatch_request


class _Cfg:
    dispatch_default_client = "codex"
    dispatch_role_client: dict[str, str] = {}
    dispatch_enabled_clients = ["agy", "grok", "codex"]
    dispatch_default_models: dict[str, str] = {}
    dispatch_default_timeout_s = 60
    dispatch_default_read_only = True
    dispatch_child_trw_access = False


@pytest.mark.parametrize("client", ["agy", "grok"])
def test_registry_reports_no_reviewer_posture_support(client: str) -> None:
    spec = client_spec_for(client)
    assert spec.reviewer_argv_template == ()
    assert spec.supports_reviewer_posture is False


@pytest.mark.parametrize("client", ["agy", "grok"])
def test_resolve_dispatch_request_refuses_reviewer_posture(client: str) -> None:
    with pytest.raises(DispatchResolutionError) as exc:
        resolve_dispatch_request(
            client=client,
            prompt="audit this",
            role=None,
            model=None,
            cwd=None,
            timeout_s=None,
            read_only=None,
            isolate=True,
            use_pty=False,
            posture="reviewer",
            dispatch_cfg=_Cfg(),
        )
    assert exc.value.exit_code == 2


@pytest.mark.parametrize("client", ["agy", "grok"])
def test_reviewer_posture_never_marks_the_child_env(client: str) -> None:
    """Even if a caller forced posture='reviewer' past resolution (e.g. by
    calling build_subprocess_env directly, as a defense-in-depth check), the
    env overlay must stay a no-op for a client with no reviewer_env."""
    env = build_subprocess_env(client, {"PATH": "/usr/bin"}, posture="reviewer")
    assert "TRW_SURFACE_ROLE" not in env
