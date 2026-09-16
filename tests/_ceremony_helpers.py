"""Shared helpers for ceremony tool tests (DRY extraction)."""

from __future__ import annotations

import json
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
    Sets TRW_PROJECT_ROOT to tmp_path and defaults model downloads to offline.
    Ceremony behavior tests do not own the embedding warm-up lifecycle; allowing
    their session-start calls to launch a real Hugging Face client leaks sockets
    and an event loop beyond the test that triggered it. Dedicated embedding
    tests can explicitly remove ``TRW_OFFLINE`` when exercising online behavior.
    """
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_OFFLINE", "1")
    return get_tools_sync(make_test_server("ceremony", "checkpoint", "review"))


def payload_size_units(payload: object) -> int:
    """Test-only JSON size heuristic: four characters per unit, not actual tokens."""
    return max(1, len(json.dumps(payload, default=str, ensure_ascii=False)) // 4)
