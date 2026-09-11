"""Empty captures stop at the real registered handler's pre-journal gate."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from fastmcp import FastMCP

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools import _learn_impl, learning, telemetry


@pytest.mark.parametrize("summary,detail", [("", ""), (" \t", "\n"), ("\u2003", "\r\n ")])
def test_empty_capture_never_allocates_journals_or_stores(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, summary, detail
):
    config = TRWConfig(telemetry_enabled=False, llm_utility_filter_enabled=False)
    monkeypatch.setattr(learning, "get_config", lambda: config)
    monkeypatch.setattr(telemetry, "get_config", lambda: config)
    monkeypatch.setattr(learning, "resolve_trw_dir", lambda: tmp_path / ".trw")
    boundaries = []
    for module, name in (
        (learning, "generate_learning_id"),
        (_learn_impl, "journal_accepted"),
        (learning, "adapter_store"),
        (learning, "list_active_learnings"),
        (learning, "check_and_handle_dedup"),
    ):
        boundary = Mock(side_effect=AssertionError(f"empty capture reached {name}"))
        monkeypatch.setattr(module, name, boundary)
        boundaries.append(boundary)
    server = FastMCP("empty-capture-test")
    learning.register_learning_tools(server)
    result = get_tools_sync(server)["trw_learn"].fn(
        summary=summary, detail=detail, metadata={"client_profile": "", "model_id": ""}
    )
    assert result == {
        "status": "rejected",
        "reason": "empty_content",
        "message": "Provide a nonempty summary or detail; empty learning was not persisted.",
    }
    for boundary in boundaries:
        boundary.assert_not_called()
    assert not (tmp_path / ".trw").exists()


@pytest.mark.parametrize("summary,detail", [("x", ""), ("", "x"), ("a", "b"), (" \t", "brief evidence")])
def test_short_or_single_field_content_keeps_existing_acceptance(summary, detail):
    from trw_mcp.tools._learn_preflight import run_accept_gates

    assert run_accept_gates(summary, detail, TRWConfig(llm_utility_filter_enabled=False), Mock()) is None
