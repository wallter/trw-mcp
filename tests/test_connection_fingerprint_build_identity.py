"""build_identity reports the running version, not the one on disk (feedback #140)."""

from __future__ import annotations

import importlib.metadata

import pytest

import trw_mcp
from trw_mcp.tools._connection_fingerprint import _resolve_build_identity


def test_the_running_version_wins_over_the_installed_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "9.9.9")
    monkeypatch.setattr(trw_mcp, "__version__", "1.0.0")

    assert _resolve_build_identity() == "1.0.0"


def test_metadata_is_the_fallback_when_no_running_version_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "9.9.9")
    monkeypatch.setattr(trw_mcp, "__version__", "")

    assert _resolve_build_identity() == "9.9.9"
