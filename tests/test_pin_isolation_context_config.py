"""Tests for pin-isolation context resolution and config defaults."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs


def test_trw_call_context_is_frozen() -> None:
    """TRWCallContext is a frozen dataclass; setters raise FrozenInstanceError."""
    from trw_mcp.state._paths import TRWCallContext

    ctx = TRWCallContext(
        session_id="abc",
        client_hint="claude-code",
        explicit=False,
        fastmcp_session="abc",
    )
    assert ctx.session_id == "abc"
    assert ctx.client_hint == "claude-code"
    assert ctx.explicit is False
    assert ctx.fastmcp_session == "abc"

    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.session_id = "other"  # type: ignore[misc]


def test_trw_call_context_accepts_none_hints() -> None:
    """client_hint and fastmcp_session may be None."""
    from trw_mcp.state._paths import TRWCallContext

    ctx = TRWCallContext(
        session_id="abc",
        client_hint=None,
        explicit=True,
        fastmcp_session=None,
    )
    assert ctx.client_hint is None
    assert ctx.fastmcp_session is None


def test_resolve_pin_key_layer_1_explicit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit arg beats env, ctx, and process."""
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.setenv("TRW_SESSION_ID", "env-id")
    ctx = SimpleNamespace(session_id="ctx-id")

    result = resolve_pin_key(ctx=ctx, explicit="explicit-id")
    assert result == "explicit-id"


def test_resolve_pin_key_layer_2_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """TRW_SESSION_ID env var returned when no explicit, no ctx."""
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.setenv("TRW_SESSION_ID", "abc")
    assert resolve_pin_key(ctx=None) == "abc"


def test_resolve_pin_key_layer_3_ctx_session_id_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """ctx.session_id probe succeeds and logs source=ctx, ctx_attr_path=session_id."""
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    ctx = SimpleNamespace(session_id="x")

    with capture_logs() as logs:
        result = resolve_pin_key(ctx=ctx)

    assert result == "x"
    resolved_events = [e for e in logs if e.get("event") == "pin_resolved"]
    assert any(e.get("source") == "ctx" and e.get("ctx_attr_path") == "session_id" for e in resolved_events), (
        f"Expected pin_resolved source=ctx ctx_attr_path=session_id, got {resolved_events}"
    )


def test_resolve_pin_key_layer_3_ctx_request_context_meta_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """ctx.request_context.meta.session_id probe when ctx.session_id missing."""
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    meta = SimpleNamespace(session_id="y")
    request_context = SimpleNamespace(meta=meta)
    ctx = SimpleNamespace(request_context=request_context)

    with capture_logs() as logs:
        result = resolve_pin_key(ctx=ctx)

    assert result == "y"
    resolved = [e for e in logs if e.get("event") == "pin_resolved"]
    assert any(
        e.get("source") == "ctx" and e.get("ctx_attr_path") == "request_context.meta.session_id" for e in resolved
    ), f"Expected ctx_attr_path=request_context.meta.session_id, got {resolved}"


def test_resolve_pin_key_layer_3_ctx_request_id_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """ctx.request_id probe when other ctx paths missing."""
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    ctx = SimpleNamespace(request_id="z")

    with capture_logs() as logs:
        result = resolve_pin_key(ctx=ctx)

    assert result == "z"
    resolved = [e for e in logs if e.get("event") == "pin_resolved"]
    assert any(e.get("source") == "ctx" and e.get("ctx_attr_path") == "request_id" for e in resolved), (
        f"Expected ctx_attr_path=request_id, got {resolved}"
    )


class _ExplodingCtx:
    """Ctx object where every attribute access raises AttributeError."""

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"no attribute {name}")


def test_resolve_pin_key_all_probes_fail_logs_warn_and_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """All ctx probes fail → fastmcp_context_probe_error WARN fires, process fallback."""
    from trw_mcp.state._paths import get_session_id, resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)

    with capture_logs() as logs:
        result = resolve_pin_key(ctx=_ExplodingCtx())

    assert result == get_session_id()
    warns = [e for e in logs if e.get("event") == "fastmcp_context_probe_error"]
    assert warns, f"Expected fastmcp_context_probe_error WARN in {logs}"
    assert any(e.get("log_level") == "warning" for e in warns)

    resolved = [e for e in logs if e.get("event") == "pin_resolved"]
    assert any(e.get("source") == "process" for e in resolved)


def test_resolve_pin_key_layer_4_process_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """No explicit, no env, no ctx → process-level _session_id."""
    from trw_mcp.state._paths import get_session_id, resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)

    with capture_logs() as logs:
        result = resolve_pin_key(ctx=None)

    assert result == get_session_id()
    resolved = [e for e in logs if e.get("event") == "pin_resolved"]
    assert any(e.get("source") == "process" for e in resolved)


def test_resolve_pin_key_precedence_explicit_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.setenv("TRW_SESSION_ID", "env-id")
    assert resolve_pin_key(ctx=None, explicit="explicit-id") == "explicit-id"


def test_resolve_pin_key_precedence_env_beats_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state._paths import resolve_pin_key

    monkeypatch.setenv("TRW_SESSION_ID", "env-id")
    ctx = SimpleNamespace(session_id="ctx-id")
    assert resolve_pin_key(ctx=ctx) == "env-id"


def test_resolve_pin_key_precedence_ctx_beats_process(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state._paths import get_session_id, resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    ctx = SimpleNamespace(session_id="ctx-id")
    result = resolve_pin_key(ctx=ctx)
    assert result == "ctx-id"
    assert result != get_session_id()


def test_ctx_isolation_disabled_reverts_to_process_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ctx_isolation_enabled=False, resolver ignores ctx and returns process UUID."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import get_session_id, resolve_pin_key

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    cfg = get_config()
    monkeypatch.setattr(cfg, "ctx_isolation_enabled", False, raising=False)

    ctx = SimpleNamespace(session_id="ctx-id-should-be-ignored")

    with capture_logs() as logs:
        result = resolve_pin_key(ctx=ctx)

    assert result == get_session_id()
    resolved = [e for e in logs if e.get("event") == "pin_resolved"]
    assert any(e.get("source") == "process" and e.get("kill_switch") is True for e in resolved), (
        f"Expected kill_switch=True pin_resolved event, got {resolved}"
    )


def test_config_sweep_fields_round_trip() -> None:
    """All seven new PRD-CORE-141 config fields exist with documented defaults."""
    from trw_mcp.models.config import TRWConfig

    cfg = TRWConfig()
    assert cfg.run_staleness_hours == 48
    assert cfg.run_staleness_grace_hours == 12
    assert cfg.pin_ttl_hours == 24
    assert cfg.run_archive_hours == 720
    assert cfg.cleanup_on_boot is True
    assert cfg.checkpoint_suggest_hours == 4
    assert cfg.ctx_isolation_enabled is True


def test_config_sweep_fields_env_var_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config fields reachable via TRW_<UPPER> env vars."""
    from trw_mcp.models.config import TRWConfig

    monkeypatch.setenv("TRW_RUN_STALENESS_HOURS", "72")
    monkeypatch.setenv("TRW_CTX_ISOLATION_ENABLED", "false")
    monkeypatch.setenv("TRW_PIN_TTL_HOURS", "6")

    cfg = TRWConfig()
    assert cfg.run_staleness_hours == 72
    assert cfg.ctx_isolation_enabled is False
    assert cfg.pin_ttl_hours == 6
