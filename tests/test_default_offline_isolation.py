"""Hermetic defaults for tests that exercise session-start behavior."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import get_tools_sync, make_test_server
from trw_mcp.state import _memory_connection


def test_ordinary_session_start_cannot_launch_embedding_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-embedding tests run session-start without owning an HF client."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        _memory_connection,
        "get_embedder",
        lambda: pytest.fail("ordinary session-start attempted a model download"),
    )
    tools = get_tools_sync(make_test_server("ceremony"))

    result = tools["trw_session_start"].fn()

    assert os.environ["TRW_OFFLINE"] == "1"
    assert result.get("status") != "error"
    assert "embedder_warmup_scheduled" not in result
