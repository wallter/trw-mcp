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


# --- gemini + enabled gates ---


def test_gemini_rejected_before_enabled_check() -> None:
    # gemini is not in enabled_clients either, but the EOL redirect must win.
    cfg = _Cfg()
    with pytest.raises(DispatchResolutionError) as exc:
        _resolve(cfg, client="gemini")
    assert exc.value.exit_code == 2
    msg = str(exc.value)
    assert "EOL" in msg
    assert "agy" in msg


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
