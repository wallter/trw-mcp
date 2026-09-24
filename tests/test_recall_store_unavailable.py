"""An unopenable memory store is reported, never read as "no learnings" (L-PbZW).

A migrated (pinned) checkout with no memory grant is refused by the daemon store's
fail-closed open (PRD-CORE-298 FR01: ``StoreUnavailableError``). ``recall_learnings``
used to log an unopenable store and return ``[]``, so ``trw_recall`` answered
``total_matches: 0`` with no error and ``trw_session_start`` reported a normal,
empty session -- this file asserts the store's failure surfaces instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import get_config
from trw_mcp.server._app import create_app
from trw_mcp.server._tools import _tool_registrars


def _ungranted_pinned_checkout(project: Path) -> None:
    """A checkout pinned to a project namespace but never granted (no ``memory token``)."""
    trw_dir = project / ".trw"
    trw_dir.mkdir(exist_ok=True)
    (trw_dir / "config.yaml").write_text("project_namespace: project:ungranted\n", encoding="utf-8")


async def _call(tool: str, args: dict[str, object]) -> dict[str, object]:
    server = create_app()
    for register in _tool_registrars():
        register(server)
    async with Client(server) as client:
        result = await client.call_tool(tool, args)
    return dict(result.structured_content or {})


@pytest.fixture
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.server._boot_deferred._resolve_backend_sync", lambda: None)
    monkeypatch.setattr(get_config(), "embeddings_enabled", False)


@pytest.mark.usefixtures("_quiet")
async def test_trw_recall_reports_an_unopenable_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    _ungranted_pinned_checkout(tmp_path)
    payload = await _call("trw_recall", {"query": "token refresh"})
    assert "store_unavailable" in payload, payload
    assert "memory token" in str(payload["store_unavailable"])


@pytest.mark.usefixtures("_quiet")
async def test_trw_recall_on_a_real_store_reports_nothing(tmp_path: Path, fake_memory_store: FakeMemoryStore) -> None:
    """Control: the field appears only when the store failed."""
    (tmp_path / ".trw").mkdir(exist_ok=True)
    payload = await _call("trw_recall", {"query": "token refresh"})
    assert "store_unavailable" not in payload, payload


@pytest.mark.usefixtures("_quiet")
async def test_session_start_reports_an_unopenable_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    _ungranted_pinned_checkout(tmp_path)
    payload = await _call("trw_session_start", {"query": "token refresh"})
    assert "memory token" in json.dumps(payload, default=str), payload
