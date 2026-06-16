"""F2 regression (round-2 transport e2e): middleware pin-key precedence.

``_resolve_runtime_run_dir`` must resolve the pin key with the SAME documented
PRD-CORE-141 precedence the tool layer uses when WRITING pins:

    1. explicit arg   2. TRW_SESSION_ID env   3. FastMCP ctx   4. process UUID

The pre-fix bug forced ``ctx.session_id`` in as the Layer-1 ``explicit`` override,
which shadowed Layer-2 (``TRW_SESSION_ID``). With ``TRW_SESSION_ID`` set, phase
resolution then read a pin keyed on the ctx UUID that the tool layer never wrote,
so the run stayed in RESEARCH forever.

These tests prove the middleware now lets ``resolve_pin_key`` apply its full
layering — by asserting the pin key the middleware actually keys its lookup on.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class _FakeCtx:
    """Minimal FastMCP-like context exposing a transport session id."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


def _run_resolution(monkeypatch: pytest.MonkeyPatch, *, env_session: str | None) -> str:
    """Drive ``_resolve_runtime_run_dir`` and capture the resolved pin key.

    Returns the ``context.session_id`` (resolved pin key) the middleware used
    for its pinned-run lookup.
    """
    from trw_mcp.middleware import _mcp_security_helpers as helpers
    from trw_mcp.state import _paths

    if env_session is None:
        monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    else:
        monkeypatch.setenv("TRW_SESSION_ID", env_session)

    # ctx-isolation must be ON for the layering to run (the kill switch
    # short-circuits to the process UUID otherwise).
    class _Cfg:
        ctx_isolation_enabled = True

    monkeypatch.setattr(_paths, "get_config", lambda: _Cfg())

    captured: dict[str, str] = {}

    def _spy_get_pinned_run(context: object | None = None) -> Path | None:
        if context is not None:
            captured["pin_key"] = getattr(context, "session_id", "")
        return Path("/tmp/does-not-matter")

    monkeypatch.setattr(_paths, "get_pinned_run", _spy_get_pinned_run)
    monkeypatch.setattr(_paths, "find_active_run", lambda context=None: None)

    ctx = _FakeCtx(session_id="ctx-uuid-1234")
    helpers._resolve_runtime_run_dir(
        configured_run_dir=None,
        session_id=ctx.session_id,  # what safe_session_id_from_context returns
        fastmcp_context=ctx,
    )
    return captured.get("pin_key", "")


@pytest.mark.unit
def test_trw_session_id_env_wins_over_ctx_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    """With TRW_SESSION_ID set, the middleware keys its pin lookup on the env
    value (Layer 2) — NOT the transport ctx UUID. This is the F2 fix: previously
    the ctx UUID was forced in as Layer-1 ``explicit`` and shadowed the env."""
    pin_key = _run_resolution(monkeypatch, env_session="operator-forced-id")
    assert pin_key == "operator-forced-id"
    assert pin_key != "ctx-uuid-1234"


@pytest.mark.unit
def test_ctx_uuid_used_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without TRW_SESSION_ID, the resolver falls through to the FastMCP ctx
    session id (Layer 3) — the per-connection UUID — exactly as before."""
    pin_key = _run_resolution(monkeypatch, env_session=None)
    assert pin_key == "ctx-uuid-1234"
