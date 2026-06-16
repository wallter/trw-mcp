"""Shared helpers for sync client test splits."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace


def _make_config(**overrides: object) -> SimpleNamespace:
    base = {
        "sync_interval_seconds": 300,
        "backend_url": "http://example.com",
        "backend_api_key": "key",
        "sync_push_batch_size": 100,
        "sync_push_timeout_seconds": 10.0,
        "sync_pull_timeout_seconds": 5.0,
        "intel_cache_ttl_seconds": 3600,
        "intel_cache_enabled": True,
        "team_sync_enabled": True,
        "model_family": "opus",
        "framework_version": "v1",
        # PRD-SEC-004: the sync-cycle tests exercise the CONSENTED sync path
        # (push of learning content + outcomes). Both consent flags default True
        # here so existing push assertions hold; dedicated opt-out tests live in
        # test_sync_consent_gate.py.
        "learning_sharing_enabled": True,
        "platform_telemetry_enabled": True,
    }
    base.update(overrides)
    base.setdefault("resolved_backend_url", base.get("backend_url", ""))
    base.setdefault("resolved_backend_api_key", base.get("backend_api_key", ""))
    return SimpleNamespace(**base)


@contextmanager
def _acquired_lock() -> Iterator[bool]:
    yield True
