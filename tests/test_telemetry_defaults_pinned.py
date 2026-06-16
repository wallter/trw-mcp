"""Pin the enterprise 'telemetry / network egress OFF by default' invariant.

A fresh ``TRWConfig()`` (no ``.trw/config.yaml`` and no ``TRW_*`` env overrides)
must not enable any outbound data path: no platform telemetry, no backend/platform
sync URLs, no auto-upgrade, no remote sync feature gates, and no OTEL export.

This is a *library-level* guarantee — installing the package and importing the
config model must never opt a user into sending data off-box. These tests fail
loudly if a future default flips an egress flag to "on", which would be a
privacy/compliance regression rather than a behavior tweak.

Note on ``telemetry_enabled``: that field governs the *local* on-disk
``tool-telemetry.jsonl`` logger (no network), so it is intentionally NOT asserted
off here — the invariant pinned is network egress, not local logging.
"""

from __future__ import annotations

import os

import pytest


def _fresh_config() -> object:
    """Construct a TRWConfig with all ``TRW_*`` env overrides stripped.

    Imports inside the function (per trw-mcp test isolation rules) and clears any
    ``TRW_*`` vars so a developer/CI environment cannot mask a flipped default.
    """
    import trw_mcp.models  # noqa: F401  (warm import order; avoids circular import)
    from trw_mcp.models.config import TRWConfig

    saved = {k: v for k, v in os.environ.items() if k.startswith("TRW_")}
    for key in saved:
        del os.environ[key]
    try:
        return TRWConfig()
    finally:
        os.environ.update(saved)


@pytest.mark.unit
def test_platform_telemetry_disabled_by_default() -> None:
    cfg = _fresh_config()
    assert cfg.platform_telemetry_enabled is False


@pytest.mark.unit
def test_platform_urls_empty_by_default() -> None:
    cfg = _fresh_config()
    # No platform endpoints means no outbound telemetry/sync target.
    assert cfg.platform_urls == []
    assert cfg.platform_url == ""


@pytest.mark.unit
def test_backend_url_empty_by_default() -> None:
    cfg = _fresh_config()
    # Empty backend URL => the sync pipeline has no remote to push to.
    assert cfg.backend_url == ""
    secret = cfg.backend_api_key
    raw = secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret
    assert raw == ""


@pytest.mark.unit
def test_sync_feature_gates_off_by_default() -> None:
    cfg = _fresh_config()
    # Remote-egress feature gates must be opt-in.
    assert cfg.team_sync_enabled is False
    assert cfg.meta_tune_enabled is False


@pytest.mark.unit
def test_auto_upgrade_off_by_default() -> None:
    cfg = _fresh_config()
    # No silent self-update / package fetch.
    assert cfg.auto_upgrade is False


@pytest.mark.unit
def test_otel_export_off_by_default() -> None:
    cfg = _fresh_config()
    # OpenTelemetry export is an outbound path — must be opt-in with no endpoint.
    assert cfg.otel_enabled is False
    assert cfg.otel_endpoint == ""


@pytest.mark.unit
def test_all_egress_flags_off_in_one_shot() -> None:
    """One consolidated assertion so a single flipped default is unmistakable."""
    cfg = _fresh_config()
    egress_off = {
        "platform_telemetry_enabled": cfg.platform_telemetry_enabled,
        "team_sync_enabled": cfg.team_sync_enabled,
        "meta_tune_enabled": cfg.meta_tune_enabled,
        "auto_upgrade": cfg.auto_upgrade,
        "otel_enabled": cfg.otel_enabled,
    }
    assert egress_off == {
        "platform_telemetry_enabled": False,
        "team_sync_enabled": False,
        "meta_tune_enabled": False,
        "auto_upgrade": False,
        "otel_enabled": False,
    }, f"A network-egress default flipped on: {egress_off}"
    assert cfg.platform_urls == []
    assert cfg.backend_url == ""
    assert cfg.platform_url == ""
    assert cfg.otel_endpoint == ""
