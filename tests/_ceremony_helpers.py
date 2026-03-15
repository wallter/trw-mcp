"""Shared helpers for ceremony tool tests (DRY extraction)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import get_tools_sync, make_test_server


def make_ceremony_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, Any]:
    """Create a FastMCP server with all ceremony-related tools and patched project root.

    Registers ceremony (session_start, deliver), checkpoint (pre_compact),
    and review tools so tests can access them all from a single server.
    Sets TRW_PROJECT_ROOT to tmp_path.
    """
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return get_tools_sync(make_test_server("ceremony", "checkpoint", "review"))
