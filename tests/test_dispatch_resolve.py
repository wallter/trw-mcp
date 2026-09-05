"""Behavior tests for the shared dispatch-request resolver.

The CLI (``_cli.py``) and the MCP tools (``tools/dispatch.py``) both delegate to
``resolve_dispatch_request`` so these tests pin the single source of truth for
client / model / timeout / read-only precedence and the rejection paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.dispatch._resolve import (
    DispatchResolutionError,
    resolve_dispatch_request,
)


class _Cfg:
    """Attribute-only stand-in for ``config.dispatch``."""

    def __init__(self, **overrides: Any) -> None:
        self.dispatch_enabled_clients: list[str] = ["codex", "claude", "agy", "opencode"]
        self.dispatch_default_client: str | None = "codex"
        self.dispatch_default_models: dict[str, str] = {}
        self.dispatch_default_timeout_s: int = 600
        self.dispatch_default_read_only: bool = True
        self.dispatch_role_client: dict[str, str] = {}
        for key, value in overrides.items():
            setattr(self, key, value)


def _resolve(cfg: _Cfg, **kw: Any) -> Any:
    base: dict[str, Any] = {
        "client": None,
        "prompt": "review this",
        "role": None,
        "model": None,
        "cwd": None,
        "timeout_s": None,
        "read_only": None,
        "isolate": True,
        "use_pty": False,
        "dispatch_cfg": cfg,
    }
    base.update(kw)
    return resolve_dispatch_request(**base)


# --- client precedence ---


def test_explicit_client_wins_over_role_and_default() -> None:
    cfg = _Cfg(dispatch_default_client="claude", dispatch_role_client={"adversarial-audit": "agy"})
    req = _resolve(cfg, client="codex", role="adversarial-audit")
    assert req.client == "codex"


def test_role_client_beats_default_when_no_explicit() -> None:
    cfg = _Cfg(dispatch_default_client="claude", dispatch_role_client={"adversarial-audit": "codex"})
    req = _resolve(cfg, client=None, role="adversarial-audit")
    assert req.client == "codex"


def test_default_client_used_when_no_explicit_or_role() -> None:
    cfg = _Cfg(dispatch_default_client="agy")
    req = _resolve(cfg, client=None, role=None)
    assert req.client == "agy"


def test_no_client_resolved_raises_exit_2() -> None:
    cfg = _Cfg(dispatch_default_client=None)
    with pytest.raises(DispatchResolutionError) as exc:
        _resolve(cfg, client=None, role=None)
    assert exc.value.exit_code == 2
    assert "No dispatch client resolved" in str(exc.value)


# --- enabled gates ---


def test_unknown_client_rejected_exit_2() -> None:
    # A client id that is not a dispatch target at all is rejected by the
    # enabled-clients gate rather than resolving to something unexpected.
    cfg = _Cfg()
    with pytest.raises(DispatchResolutionError) as exc:
        _resolve(cfg, client="not-a-real-cli")
    assert exc.value.exit_code == 2
    assert "disabled" in str(exc.value)


def test_disabled_client_rejected_exit_2() -> None:
    cfg = _Cfg(dispatch_enabled_clients=["claude"])
    with pytest.raises(DispatchResolutionError) as exc:
        _resolve(cfg, client="codex")
    assert exc.value.exit_code == 2
    assert "disabled" in str(exc.value)


# --- model resolution ---


def test_model_falls_back_to_per_client_default() -> None:
    cfg = _Cfg(dispatch_default_models={"codex": "gpt-5.5"})
    req = _resolve(cfg, client="codex", model=None)
    assert req.model == "gpt-5.5"


def test_explicit_model_wins() -> None:
    cfg = _Cfg(dispatch_default_models={"codex": "gpt-5.5"})
    req = _resolve(cfg, client="codex", model="gpt-4o")
    assert req.model == "gpt-4o"


# --- timeout resolution ---


def test_timeout_falls_back_to_config_default() -> None:
    cfg = _Cfg(dispatch_default_timeout_s=120)
    req = _resolve(cfg, client="codex", timeout_s=None)
    assert req.timeout_s == 120


def test_explicit_timeout_wins() -> None:
    cfg = _Cfg(dispatch_default_timeout_s=120)
    req = _resolve(cfg, client="codex", timeout_s=30)
    assert req.timeout_s == 30


# --- read-only resolution (F-03: explicit value honored, None -> config) ---


def test_read_only_none_falls_back_to_config_default_false() -> None:
    cfg = _Cfg(dispatch_default_read_only=False)
    req = _resolve(cfg, client="codex", read_only=None)
    assert req.read_only is False


def test_read_only_none_falls_back_to_config_default_true() -> None:
    cfg = _Cfg(dispatch_default_read_only=True)
    req = _resolve(cfg, client="codex", read_only=None)
    assert req.read_only is True


def test_explicit_read_only_false_forces_writes() -> None:
    # --allow-writes is mapped to read_only=False by the caller; it wins.
    cfg = _Cfg(dispatch_default_read_only=True)
    req = _resolve(cfg, client="codex", read_only=False)
    assert req.read_only is False


def test_explicit_read_only_true_honored_over_config_default_false() -> None:
    # F-03 safety fix: an explicit read_only=True must NOT be silently overridden
    # by a config default of False.
    cfg = _Cfg(dispatch_default_read_only=False)
    req = _resolve(cfg, client="codex", read_only=True)
    assert req.read_only is True


# --- role + passthrough fields ---


def test_role_preamble_applied_to_prompt() -> None:
    cfg = _Cfg()
    req = _resolve(cfg, client="codex", role="adversarial-audit", prompt="check X")
    assert req.prompt.endswith("check X")
    assert "read-only" in req.prompt.lower()


def test_cwd_isolate_pty_passthrough() -> None:
    cfg = _Cfg()
    req = _resolve(cfg, client="codex", cwd=Path("/tmp"), isolate=False, use_pty=True)
    assert req.cwd == Path("/tmp")
    assert req.isolate is False
    assert req.use_pty is True


# --------------------------------------------------------------------------- #
# PRD-CORE-266-FR04 / NFR02 — an unverified client is refused, loudly
# --------------------------------------------------------------------------- #


def test_unverified_client_refuses_and_names_the_verification_needed() -> None:
    from trw_mcp.dispatch._client_specs import CLIENT_SPECS

    assert CLIENT_SPECS["grok"].verification.method == "unverified"
    cfg = _Cfg(dispatch_enabled_clients=["codex", "grok"])
    with pytest.raises(DispatchResolutionError) as exc:
        _resolve(cfg, client="grok")
    message = str(exc.value)
    assert exc.value.exit_code == 2
    assert "grok" in message
    assert "UNVERIFIED" in message
    # The message must name the SPECIFIC outstanding verification, not merely
    # report that one is open — a caller with no next step reaches for a bypass.
    assert "sandbox" in message
    assert "output-format" in message or "output_format" in message


def test_unverified_refusal_does_not_substitute_the_default_client() -> None:
    # A silent fallback would answer the operator's question with a different
    # agent's output. The refusal must not resolve to `codex` under any path.
    cfg = _Cfg(dispatch_default_client="codex", dispatch_enabled_clients=["codex", "grok"])
    with pytest.raises(DispatchResolutionError) as exc:
        _resolve(cfg, client="grok")
    assert "codex" not in str(exc.value)


def test_unverified_refusal_precedes_any_argv_construction(monkeypatch: Any) -> None:
    # FR04's ordering requirement, asserted rather than inferred: the builder is
    # never reached, so provisional flag data cannot land on a command line.
    import trw_mcp.dispatch._commands as commands

    calls: list[Any] = []
    monkeypatch.setattr(commands, "build_command", lambda req: calls.append(req))
    cfg = _Cfg(dispatch_enabled_clients=["grok"])
    with pytest.raises(DispatchResolutionError):
        _resolve(cfg, client="grok")
    assert calls == []


def test_a_role_mapping_cannot_route_around_the_unverified_refusal() -> None:
    # The refusal sits after client PRECEDENCE resolution, so an unverified
    # client reached through a role mapping is refused identically.
    cfg = _Cfg(
        dispatch_role_client={"adversarial-audit": "grok"},
        dispatch_enabled_clients=["codex", "grok"],
    )
    with pytest.raises(DispatchResolutionError) as exc:
        _resolve(cfg, role="adversarial-audit")
    assert "UNVERIFIED" in str(exc.value)


def test_a_verified_client_still_resolves_normally() -> None:
    # Non-vacuity: the refusal must not be a blanket rejection.
    from trw_mcp.dispatch._client_specs import CLIENT_SPECS

    cfg = _Cfg(dispatch_enabled_clients=["copilot", "cursor-cli", "codex"])
    for client in ("copilot", "cursor-cli", "codex"):
        assert CLIENT_SPECS[client].verification.method != "unverified"
        assert _resolve(cfg, client=client).client == client


def test_an_enabled_client_with_no_registry_entry_raises_rather_than_defaulting() -> None:
    # NFR02: an id TRW cannot describe must not fall through to a default spec.
    cfg = _Cfg(dispatch_enabled_clients=["ghost-cli"], dispatch_default_client="ghost-cli")
    with pytest.raises(DispatchResolutionError) as exc:
        _resolve(cfg, client="ghost-cli")
    assert "ghost-cli" in str(exc.value)
    assert "no dispatch client spec" in str(exc.value)
