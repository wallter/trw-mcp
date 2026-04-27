"""Fail-loud startup + config E2E tests for MCP security bootstrap."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trw_mcp.models.config import TRWConfig, reload_config
from trw_mcp.models.config._sub_models import MCPSecurityConfig
from trw_mcp.security.mcp_registry import MCPSecurityConfigError, MCPSecurityUnavailableError
from trw_mcp.startup import init_security


def _security_rows(events_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(events_dir.glob("events-*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text().splitlines() if line)
    return [row for row in rows if row.get("event_type") == "mcp_security"]


def test_missing_cryptography_raises_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> tuple[type[object], type[object]]:
        raise MCPSecurityUnavailableError("missing cryptography")

    monkeypatch.setattr("trw_mcp.security.mcp_registry._load_crypto", _boom)

    with pytest.raises(MCPSecurityUnavailableError):
        init_security(MCPSecurityConfig())


def test_build_middleware_does_not_silently_bypass_security(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "trw_mcp.startup.init_security",
        lambda _config: (_ for _ in ()).throw(MCPSecurityUnavailableError("missing cryptography")),
    )
    reload_config(TRWConfig(meta_tune={"enabled": False}))

    with pytest.raises(MCPSecurityUnavailableError):
        from trw_mcp.server._app import _build_middleware

        _build_middleware()


def test_cli_allow_unsigned_overrides_config() -> None:
    reload_config(TRWConfig(meta_tune={"enabled": False}))
    from trw_mcp.server._cli import _apply_cli_security_overrides

    config = TRWConfig()
    args = SimpleNamespace(allow_unsigned=True)
    updated = _apply_cli_security_overrides(config, args)
    assert updated.security.mcp.allow_unsigned is True
    assert config.security.mcp.allow_unsigned is False


def test_relative_security_paths_require_anchor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TRW_PROJECT_ROOT", raising=False)
    with pytest.raises(MCPSecurityConfigError, match="anchored"):
        init_security(
            MCPSecurityConfig(
                allowlist_path="relative/allowlist.yaml",
            )
        )


def test_init_security_threads_anomaly_mode_and_auto_release_from_config() -> None:
    middleware = init_security(
        MCPSecurityConfig(
            anomaly={"mode": "enforce", "sigma_threshold": 3.0, "window_seconds": 30},
            quarantine={"auto_release": True},
        )
    )

    assert middleware._detector._config.mode == "enforce"
    assert middleware._detector._config.sigma_threshold == 3.0
    assert middleware._detector._config.window_seconds == 30
    assert middleware._quarantine_auto_release is True


def test_enforce_false_observe_mode_admits_unlisted_end_to_end(tmp_path: Path) -> None:
    middleware = init_security(
        MCPSecurityConfig(
            enforce=False,
            audit_log_path=str(tmp_path),
        )
    )

    decision = middleware.on_tool_call(
        transport="stdio",
        server="ghost",
        tool="exec_shell",
        session_id="sess-observe",
    )

    assert decision.allowed is True
    rows = _security_rows(tmp_path)
    assert rows[-1]["payload"]["reason"] == "server_not_in_allowlist"
    assert rows[-1]["payload"]["enforced"] is False


def test_allow_unsigned_emits_audit_event_end_to_end(tmp_path: Path) -> None:
    middleware = init_security(
        MCPSecurityConfig(
            allow_unsigned=True,
            audit_log_path=str(tmp_path),
        )
    )

    decision = middleware.on_tool_call(
        transport="stdio",
        server="ghost",
        tool="exec_shell",
        args={"cmd": "echo hi"},
        session_id="sess-unsigned",
    )

    assert decision.allowed is True
    rows = _security_rows(tmp_path)
    assert rows[-1]["payload"]["unsigned_admission"] is True
    assert bool(rows[-1]["payload"]["operator"])


def test_anomaly_enforce_mode_blocks_end_to_end(tmp_path: Path) -> None:
    middleware = init_security(
        MCPSecurityConfig(
            anomaly={"mode": "enforce", "sigma_threshold": 3.0},
            audit_log_path=str(tmp_path),
        )
    )
    middleware._detector.seed_baseline(
        known_pairs={("trw", "trw_recall")},
        historical_rates={("trw", "trw_recall"): [1.0, 1.0, 2.0, 1.0, 1.0, 2.0]},
    )

    decision = None
    for _ in range(30):
        decision = middleware.on_tool_call(
            transport="stdio",
            server="trw",
            tool="trw_recall",
            args={"q": "burst"},
            session_id="sess-rate",
        )

    assert decision is not None
    assert decision.allowed is False
    assert decision.reason == "rate_spike"


def test_sigma_threshold_honored_end_to_end(tmp_path: Path) -> None:
    strict = init_security(
        MCPSecurityConfig(
            anomaly={"mode": "enforce", "sigma_threshold": 3.0},
            audit_log_path=str(tmp_path / "strict"),
        )
    )
    default = init_security(
        MCPSecurityConfig(
            anomaly={"mode": "enforce"},
            audit_log_path=str(tmp_path / "default"),
        )
    )
    historical_rates = {("trw", "trw_recall"): [1.0, 1.0, 1.0, 3.0]}
    strict._detector.seed_baseline(known_pairs={("trw", "trw_recall")}, historical_rates=historical_rates)
    default._detector.seed_baseline(known_pairs={("trw", "trw_recall")}, historical_rates=historical_rates)

    strict_decision = None
    default_decision = None
    for _ in range(5):
        strict_decision = strict.on_tool_call(
            transport="stdio",
            server="trw",
            tool="trw_recall",
            args={"q": "threshold"},
            session_id="sess-strict",
        )
        default_decision = default.on_tool_call(
            transport="stdio",
            server="trw",
            tool="trw_recall",
            args={"q": "threshold"},
            session_id="sess-default",
        )

    assert strict_decision is not None and default_decision is not None
    assert strict_decision.allowed is False
    assert strict_decision.reason == "rate_spike"
    assert default_decision.allowed is True


def test_window_seconds_honored_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone

    from trw_mcp.middleware import mcp_security as mcp_security_module

    strict = init_security(
        MCPSecurityConfig(
            anomaly={"mode": "enforce", "sigma_threshold": 3.0, "window_seconds": 30},
            audit_log_path=str(tmp_path / "strict"),
        )
    )
    default = init_security(
        MCPSecurityConfig(
            anomaly={"mode": "enforce", "sigma_threshold": 3.0},
            audit_log_path=str(tmp_path / "default"),
        )
    )
    historical_rates = {("trw", "trw_recall"): [1.0, 1.0, 1.0, 3.0]}
    strict._detector.seed_baseline(known_pairs={("trw", "trw_recall")}, historical_rates=historical_rates)
    default._detector.seed_baseline(known_pairs={("trw", "trw_recall")}, historical_rates=historical_rates)
    timeline = [
        datetime(2026, 4, 24, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 5, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 10, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 20, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 45, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 45, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 45, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 45, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 5, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 10, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 20, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 45, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 45, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 45, tzinfo=timezone.utc),
        datetime(2026, 4, 24, 0, 0, 45, tzinfo=timezone.utc),
    ]
    index = {"value": 0}

    class _FakeDateTime:
        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            current = timeline[min(index["value"], len(timeline) - 1)]
            index["value"] += 1
            if tz is None:
                return current.replace(tzinfo=None)
            return current.astimezone(tz)

    monkeypatch.setattr(mcp_security_module, "datetime", _FakeDateTime)

    strict_final = None
    for _ in range(5):
        strict_final = strict.on_tool_call(
            transport="stdio",
            server="trw",
            tool="trw_recall",
            args={"q": "window"},
            session_id="sess-window-strict",
        )

    default_final = None
    for _ in range(5):
        default_final = default.on_tool_call(
            transport="stdio",
            server="trw",
            tool="trw_recall",
            args={"q": "window"},
            session_id="sess-window-default",
        )

    assert strict_final is not None and default_final is not None
    assert strict_final.allowed is True
    assert default_final.allowed is False
    assert default_final.reason == "rate_spike"


def test_auto_release_honored_end_to_end(tmp_path: Path) -> None:
    middleware = init_security(
        MCPSecurityConfig(
            quarantine={"auto_release": True},
            audit_log_path=str(tmp_path),
        )
    )

    blocked = middleware.on_tool_call(
        transport="sse",
        server="filesystem",
        tool="read_file",
        args={"path": "README.md"},
        observed_fingerprint="sha256:drifted-filesystem",
    )
    released = middleware.on_tool_call(
        transport="sse",
        server="filesystem",
        tool="read_file",
        args={"path": "README.md"},
        observed_fingerprint="sha256:e1e6ca97e71a612a9b6803b8327d25f16b35e1e509dc7158b6233b98b83b155c",
    )

    assert blocked.allowed is False
    assert blocked.reason == "signature_drift"
    assert released.allowed is True
