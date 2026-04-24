"""Tests for meta_tune.boot_checks — PRD-HPO-SAFE-001 FR-15 / NFR-10."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.meta_tune.boot_checks import (
    audit_defaults,
    validate_defaults,
)
from trw_mcp.telemetry.event_base import DefaultResolutionError


def test_audit_defaults_returns_report_structure() -> None:
    report = audit_defaults()
    assert "pricing_yaml" in report
    assert "compression_algorithm" in report
    assert "hash_algorithm" in report


def test_validate_defaults_passes_when_pricing_yaml_exists() -> None:
    # pricing.yaml lives at trw-mcp/src/trw_mcp/data/pricing.yaml — real install.
    # Should NOT raise when all defaults resolve.
    validate_defaults()  # smoke — does not raise


def test_validate_defaults_raises_when_pricing_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.meta_tune import boot_checks as bc

    def _fake_resolve() -> Path | None:
        return None

    monkeypatch.setattr(bc, "_resolve_pricing_yaml", _fake_resolve)
    with pytest.raises(DefaultResolutionError) as ei:
        validate_defaults()
    msg = str(ei.value)
    assert "pricing.yaml" in msg
    assert "remediation" in msg.lower()


def test_validate_defaults_raises_on_unknown_hash_algorithm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.meta_tune import boot_checks as bc

    monkeypatch.setattr(bc, "_REQUIRED_HASH_ALGO", "fake-hash-not-real-256")
    with pytest.raises(DefaultResolutionError):
        validate_defaults()


def test_validate_defaults_raises_on_unknown_compression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.meta_tune import boot_checks as bc

    monkeypatch.setattr(bc, "_REQUIRED_COMPRESSION", "fake-zstd-not-real")
    with pytest.raises(DefaultResolutionError):
        validate_defaults()


def test_validate_defaults_is_fast() -> None:
    """NFR-10: ≤2s wall-clock."""
    import time

    start = time.monotonic()
    validate_defaults()
    elapsed = time.monotonic() - start
    assert elapsed < 2.0
