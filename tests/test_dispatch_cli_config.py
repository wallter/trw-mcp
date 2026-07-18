"""Behavior tests for config-default resolution in the ``dispatch`` CLI handler.

These assert the resolved DispatchRequest the stub runner saw, proving that
``config.dispatch`` defaults actually flow into the request (client, model,
timeout, read-only) and that the enabled-clients / gemini gates fire.

``get_config`` is patched at the CONSUMER site (``trw_mcp.dispatch._cli``) per
the repo testing rules. ``dispatch`` (the runner) is stubbed so no child
process is launched.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from trw_mcp.dispatch import DispatchResult
from trw_mcp.dispatch._cli import run_dispatch


class _StubDispatchConfig:
    """Stand-in for ``config.dispatch`` with attribute-style access only.

    Mirrors the DispatchConfig attribute names ``run_dispatch`` reads via
    ``getattr`` so we can drive resolution without building a full TRWConfig.
    """

    def __init__(self, **overrides: Any) -> None:
        self.dispatch_enabled_clients: list[str] = ["codex", "claude", "agy", "opencode"]
        self.dispatch_default_client: str | None = "codex"
        self.dispatch_default_models: dict[str, str] = {}
        self.dispatch_default_timeout_s: int = 600
        self.dispatch_default_read_only: bool = True
        self.dispatch_role_client: dict[str, str] = {}
        for key, value in overrides.items():
            setattr(self, key, value)


class _StubConfig:
    def __init__(self, dispatch_cfg: _StubDispatchConfig) -> None:
        self.dispatch = dispatch_cfg


def _ns(**kw: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "client": None,
        "prompt": "review this",
        "prompt_file": None,
        "role": None,
        "model": None,
        "cwd": None,
        "timeout": None,
        "output_file": None,
        "no_isolate": False,
        "allow_writes": False,
        "pty": False,
        "json": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _install(monkeypatch: pytest.MonkeyPatch, dispatch_cfg: _StubDispatchConfig) -> dict[str, Any]:
    """Patch get_config + the runner; return a dict that captures the request."""
    captured: dict[str, Any] = {}

    monkeypatch.setattr("trw_mcp.dispatch._cli.get_config", lambda: _StubConfig(dispatch_cfg))

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


def test_client_omitted_uses_default_client(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install(monkeypatch, _StubDispatchConfig(dispatch_default_client="claude"))
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(client=None))
    assert exc.value.code == 0
    assert getattr(captured["req"], "client") == "claude"


def test_explicit_client_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install(monkeypatch, _StubDispatchConfig(dispatch_default_client="claude"))
    with pytest.raises(SystemExit):
        run_dispatch(_ns(client="codex"))
    assert getattr(captured["req"], "client") == "codex"


def test_role_default_client_used_when_no_explicit_or_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _StubDispatchConfig(
        dispatch_default_client=None,
        dispatch_role_client={"adversarial-audit": "codex"},
    )
    captured = _install(monkeypatch, cfg)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(client=None, role="adversarial-audit"))
    assert getattr(captured["req"], "client") == "codex"


def test_role_client_overrides_set_default_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # A role-specific mapping must beat a SET default_client — otherwise
    # role_client is unreachable (default_client defaults to "codex").
    cfg = _StubDispatchConfig(
        dispatch_default_client="claude",
        dispatch_role_client={"adversarial-audit": "codex"},
    )
    captured = _install(monkeypatch, cfg)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(client=None, role="adversarial-audit"))
    assert getattr(captured["req"], "client") == "codex"


def test_explicit_client_beats_role_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicit --client is the strongest signal, above any role mapping.
    cfg = _StubDispatchConfig(dispatch_role_client={"adversarial-audit": "codex"})
    captured = _install(monkeypatch, cfg)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(client="claude", role="adversarial-audit"))
    assert getattr(captured["req"], "client") == "claude"


def test_no_client_resolvable_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _StubDispatchConfig(dispatch_default_client=None)
    captured = _install(monkeypatch, cfg)
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(client=None, role=None))
    assert exc.value.code == 2
    assert "req" not in captured  # runner never called


def test_disabled_client_exits_2_no_dispatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _StubDispatchConfig(dispatch_enabled_clients=["claude"])
    captured = _install(monkeypatch, cfg)
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(client="codex"))
    assert exc.value.code == 2
    assert "disabled" in capsys.readouterr().err
    assert "req" not in captured


def test_gemini_rejected_before_enabled_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # gemini is not in enabled_clients either, but the EOL redirect must win.
    cfg = _StubDispatchConfig()
    _install(monkeypatch, cfg)
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(client="gemini"))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "EOL" in err
    assert "agy" in err


def test_model_omitted_uses_default_models(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _StubDispatchConfig(dispatch_default_models={"codex": "gpt-5.5"})
    captured = _install(monkeypatch, cfg)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(client="codex", model=None))
    assert getattr(captured["req"], "model") == "gpt-5.5"


def test_explicit_model_overrides_default_models(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _StubDispatchConfig(dispatch_default_models={"codex": "gpt-5.5"})
    captured = _install(monkeypatch, cfg)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(client="codex", model="gpt-4o"))
    assert getattr(captured["req"], "model") == "gpt-4o"


def test_timeout_omitted_uses_default_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _StubDispatchConfig(dispatch_default_timeout_s=120)
    captured = _install(monkeypatch, cfg)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(client="codex", timeout=None))
    assert getattr(captured["req"], "timeout_s") == 120


def test_explicit_timeout_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _StubDispatchConfig(dispatch_default_timeout_s=120)
    captured = _install(monkeypatch, cfg)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(client="codex", timeout=30))
    assert getattr(captured["req"], "timeout_s") == 30


def test_read_only_default_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _StubDispatchConfig(dispatch_default_read_only=False)
    captured = _install(monkeypatch, cfg)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(client="codex", allow_writes=False))
    assert getattr(captured["req"], "read_only") is False


def test_allow_writes_forces_read_only_false(monkeypatch: pytest.MonkeyPatch) -> None:
    # Config default is read-only True, but --allow-writes is authoritative.
    cfg = _StubDispatchConfig(dispatch_default_read_only=True)
    captured = _install(monkeypatch, cfg)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(client="codex", allow_writes=True))
    assert getattr(captured["req"], "read_only") is False
