"""Shared path helpers for bundled asset contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).parent
_PKG_DATA = _TESTS_DIR.parent / "src" / "trw_mcp" / "data"
_MONOREPO_CLAUDE = _TESTS_DIR.parent.parent / ".claude"


def _resolve_data_path(pkg_subdir: str, monorepo_subdir: str) -> Path:
    """Resolve a data path, preferring package data over monorepo location."""
    pkg = _PKG_DATA / pkg_subdir
    if pkg.exists():
        return pkg

    mono = _MONOREPO_CLAUDE / monorepo_subdir
    if mono.exists():
        return mono

    pytest.skip(f"{pkg_subdir} not found in package data or monorepo")
